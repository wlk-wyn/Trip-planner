"""SQLite-fenced ownership of planning jobs shared by local worker processes."""
import time
from datetime import datetime, timezone
from uuid import uuid4


class RunLeaseLost(Exception):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def require_lease(row, token):
    if (not row or row['status'] != 'running' or not token or row['lease_token'] != token
            or row['lease_until'] is None or row['lease_until'] <= time.time()):
        raise RunLeaseLost('任务执行权已转移')


def claim(repo, owner, run_id, *, ttl, capacity, max_attempts, queue_timeout=180):
    token, instant = str(uuid4()), time.time()
    with repo.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM planning_runs WHERE id=? AND owner=?', (run_id, owner)).fetchone()
        if not row or row['status'] not in {'queued', 'running'}:
            return None
        if row['lease_until'] is not None and row['lease_until'] > instant:
            return None
        if row['attempt_count'] == 0 and instant-datetime.fromisoformat(row['created_at']).timestamp() >= queue_timeout:
            db.execute("UPDATE planning_runs SET status='failed',stage='排队超时',error='等待处理时间过长，尚未开始规划，请稍后重新发起',lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (stamp(), run_id))
            return None
        if row['attempt_count'] >= max_attempts:
            db.execute("UPDATE planning_runs SET status='failed',stage='未完成',error='任务多次中断，请重新发起',lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?", (stamp(), run_id))
            return None
        active = db.execute("SELECT count(*) FROM planning_runs WHERE status='running' AND lease_until>?", (instant,)).fetchone()[0]
        if active >= capacity:
            db.execute("UPDATE planning_runs SET status='queued',stage='排队等待处理',lease_token=NULL,lease_until=NULL WHERE id=?", (run_id,))
            return None
        db.execute("UPDATE planning_runs SET status='running',lease_token=?,lease_until=?,attempt_count=attempt_count+1,updated_at=? WHERE id=?",
                   (token, instant+ttl, stamp(), run_id))
    return token


def renew(repo, owner, run_id, token, ttl):
    instant = time.time()
    with repo.connect() as db:
        result = db.execute("UPDATE planning_runs SET lease_until=? WHERE id=? AND owner=? AND status='running' AND lease_token=? AND lease_until>?",
                            (instant+ttl, run_id, owner, token, instant))
    return result.rowcount == 1


def owned_update(repo, owner, run_id, token, values):
    """Update internal job fields only while this execution still owns its lease."""
    if set(values) - {'stage', 'queries', 'payload', 'status', 'error', 'updated_at'}:
        raise ValueError('Unsupported planning job update')
    with repo.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM planning_runs WHERE id=? AND owner=?', (run_id, owner)).fetchone()
        require_lease(row, token)
        fields, args = list(values), list(values.values())
        if values.get('status') in {'failed', 'conflict'}:
            fields += ['lease_token', 'lease_until']; args += [None, None]
        db.execute('UPDATE planning_runs SET '+','.join(f'{key}=?' for key in fields)+' WHERE id=?', [*args, run_id])


def release(repo, owner, run_id, token):
    # Do not touch another executor's work or resurrect a terminal job.
    with repo.connect() as db:
        db.execute("UPDATE planning_runs SET status='queued',stage='等待恢复任务',lease_token=NULL,lease_until=NULL WHERE id=? AND owner=? AND status='running' AND lease_token=?",
                   (run_id, owner, token))
