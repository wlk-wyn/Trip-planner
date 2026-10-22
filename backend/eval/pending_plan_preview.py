"""Local-only fault fixture for the actual Home submission/recovery UI.

Uses an isolated DB and a public saved plan, with no model generation. Never run
as the application server. PENDING_PREVIEW_FAILURE=before drops the first POST
before admission; the default drops its response after real SQLite admission.
"""
import hashlib
import json
import os
from fastapi.responses import JSONResponse
from app.services import planning_runs as jobs
from app.services.run_leases import claim
from .frontend_preview import app, repo, row

# Test process only: deliberately prevent queued jobs from calling any providers.
jobs.launch = lambda *_args, **_kwargs: None
mode = os.getenv('PENDING_PREVIEW_FAILURE', 'after')
if mode not in ('before', 'after'):
    raise ValueError('Unknown pending preview failure mode')
audit = {'fixture': 'synthetic-recovery-not-generation', 'mode': mode, 'posts': [], 'reads': 0}


def finish_public_fixture(run_id):
    with repo.connect() as db:
        job = db.execute('SELECT * FROM planning_runs WHERE id=?', (run_id,)).fetchone()
    if job and job['status'] == 'queued':
        token = claim(repo, job['owner'], run_id, ttl=60, capacity=2, max_attempts=3)
        if token:
            payload = json.loads(job['payload'])
            repo.create(job['owner'], payload['body']['request'], row['plan_snapshot'], [], run_id,
                        {'fixture': 'public-snapshot-not-generated'}, lease_token=token)


@app.middleware('http')
async def fail_acknowledgement(request, call_next):
    path = request.url.path
    if path == '/api/trips/plan-runs' and request.method == 'POST':
        body = await request.json()
        audit['posts'].append({
            'id': body['request_id'], 'city': body['request']['city'],
            'body_sha256': hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        })
        if mode == 'before' and len(audit['posts']) == 1:
            return JSONResponse({'detail': '合成验收：请求未送达'}, status_code=503)
        response = await call_next(request)
        if len(audit['posts']) == 1 and response.status_code == 202:
            return JSONResponse({'detail': '合成验收：已接收但响应丢失'}, status_code=503)
        if mode == 'before' and response.status_code == 202:
            finish_public_fixture(body['request_id'])
        return response
    if path.startswith('/api/trips/plan-runs/') and request.method == 'GET':
        audit['reads'] += 1
        if mode == 'after' and audit['reads'] == 1:
            return JSONResponse({'detail': '合成验收：暂时无法查询状态'}, status_code=503)
        run_id = path.rsplit('/', 1)[-1]
        # Admission, lease, save and retrieval use real repository/service code.
        finish_public_fixture(run_id)
    return await call_next(request)


@app.get('/preview-audit')
def preview_audit():
    with repo.connect() as db:
        count = db.execute('SELECT COUNT(*) FROM planning_runs').fetchone()[0]
    return {**audit, 'persisted_jobs': count}
