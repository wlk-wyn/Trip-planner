"""Offline subprocess fault checks for durable jobs; no model or map calls.

python -m eval.job_reliability
Uses temporary SQLite files and terminates only child processes it creates.
"""
import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.config import get_settings
from app.models.schemas import TripPlan, TripRequest
from app.services import planning_runs as jobs
from app.services.run_leases import owned_update, RunLeaseLost
from app.services.trip_repository import TripRepository

ROOT = Path(__file__).resolve().parents[1]
OWNER = 'offline-process-fixture'
REQUEST = TripRequest(city='杭州', start_date='2026-11-01', end_date='2026-11-01', travel_days=1,
                      transportation='步行', accommodation='无需住宿')
PLAN = TripPlan(city='杭州', start_date='2026-11-01', end_date='2026-11-01', overall_suggestions='离线任务可靠性夹具',
                days=[{'date':'2026-11-01','day_index':0,'description':'测试地点','transportation':'步行','accommodation':'无需住宿',
                       'attractions':[{'name':'测试地点','address':'测试地址','location':{'longitude':120.15,'latitude':30.24},
                                       'visit_duration':60,'description':'不调用外部服务'}]}])


def marker(repo, id, stage):
    with repo.connect() as db:
        db.execute('INSERT INTO probe_events VALUES (?,?,?)', (id, stage, jobs._checkpoint_namespace.get() or ''))


async def worker(args):
    repo = TripRepository(args.db)
    settings = get_settings()
    settings.planning_lease_seconds = 10
    settings.planning_run_timeout_seconds = 30
    async def generate(request, id, progress):
        marker(repo, id, 'generate')
        await asyncio.sleep(args.hold)
        return PLAN.model_copy(deep=True)
    async def no_ticket_calls(*args):
        return None
    jobs._generate, jobs._estimate_tickets = generate, no_ticket_calls
    if args.prepared_hold:
        create = repo.create
        def delayed_create(*values, **kwargs):
            marker(repo, args.run, 'prepared')
            time.sleep(30)
            return create(*values, **kwargs)
        repo.create = delayed_create
    try:
        if args.recover:
            jobs.start_recovery(repo)
            async with asyncio.timeout(35):
                while jobs.read_run(repo, OWNER, args.run)['status'] not in jobs.TERMINAL:
                    await asyncio.sleep(.05)
        else:
            await jobs.execute(repo, OWNER, args.run)
    finally:
        await jobs.shutdown_runs()


def count(repo, stage):
    with repo.connect() as db:
        return db.execute('SELECT count(*) FROM probe_events WHERE stage=?', (stage,)).fetchone()[0]


def wait_until(condition, processes, timeout=15):
    end = time.monotonic()+timeout
    while time.monotonic() < end:
        if condition():
            return
        failed = [p.returncode for p in processes if p.poll() not in (None, 0)]
        if failed:
            raise RuntimeError(f'fixture process exited: {failed}')
        time.sleep(.04)
    raise TimeoutError('fault probe timed out')


def run_case(kind):
    with tempfile.TemporaryDirectory() as folder:
        repo = TripRepository(Path(folder)/'jobs.sqlite3')
        with repo.connect() as db:
            db.execute('CREATE TABLE probe_events(run_id TEXT,stage TEXT,namespace TEXT)')
        id = str(uuid4())
        # Queue without a local asyncio executor; children compete for its SQL lease.
        launch = jobs.launch
        try:
            jobs.launch = lambda *args: None
            jobs.start_create_run(repo, OWNER, REQUEST.model_dump(), id)
        finally:
            jobs.launch = launch
        processes, outputs = [], []
        def spawn(*extra):
            stream = open(Path(folder)/f'worker-{len(processes)}.log', 'w', encoding='utf-8')
            outputs.append(stream)
            process = subprocess.Popen([sys.executable, '-X', 'utf8', '-m', 'eval.job_reliability', 'worker',
                '--db', str(repo.path), '--run', id, *extra], cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            processes.append(process)
            return process
        try:
            if kind == 'duplicate_workers':
                spawn('--hold', '.6'); spawn('--hold', '.6')
            elif kind == 'crash_after_prepared':
                first = spawn('--prepared-hold')
                wait_until(lambda: count(repo, 'prepared') == 1, processes)
                first.kill(); first.wait(timeout=5)
                processes.remove(first)  # intentional crash, already confirmed terminal
                spawn('--recover')
            elif kind == 'expired_live_owner':
                spawn('--hold', '2')
                wait_until(lambda: count(repo, 'generate') == 1, processes)
                with repo.connect() as db:
                    old = db.execute('SELECT lease_token FROM planning_runs WHERE id=?', (id,)).fetchone()[0]
                    db.execute('UPDATE planning_runs SET lease_until=? WHERE id=?', (time.time()-1, id))
                spawn('--hold', '.5')
                wait_until(lambda: count(repo, 'generate') == 2, processes)
                try:
                    owned_update(repo, OWNER, id, old, {'stage':'stale executor'})
                except RunLeaseLost:
                    pass
                else:
                    raise AssertionError('expired owner could write')
            elif kind == 'remote_cancel':
                spawn('--hold', '20')
                wait_until(lambda: count(repo, 'generate') == 1, processes)
                jobs.cancel_run(repo, OWNER, id)
            for process in processes:
                if process.wait(timeout=40) != 0:
                    raise RuntimeError('worker did not finish cleanly')
            job = jobs.read_run(repo, OWNER, id)
            saved, generated = len(repo.list(OWNER)), count(repo, 'generate')
            expected_state = 'cancelled' if kind == 'remote_cancel' else 'succeeded'
            assert job['status'] == expected_state, job['status']
            assert saved == (0 if kind == 'remote_cancel' else 1), saved
            assert generated == (2 if kind == 'expired_live_owner' else 1), generated
            with repo.connect() as db:
                namespaces = db.execute("SELECT count(DISTINCT namespace) FROM probe_events WHERE stage='generate'").fetchone()[0]
            if kind == 'expired_live_owner':
                assert namespaces == 2, 'executors shared a mutable agent checkpoint namespace'
            return {'case':kind,'status':'passed','job_status':job['status'],'saved_trips':saved,
                    'synthetic_generate_calls':generated,'checkpoint_namespaces':namespaces}
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill(); process.wait(timeout=5)
            for stream in outputs:
                stream.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', nargs='?', default='run', choices=['run','worker'])
    parser.add_argument('--db'); parser.add_argument('--run')
    parser.add_argument('--hold', type=float, default=0)
    parser.add_argument('--prepared-hold', action='store_true')
    parser.add_argument('--recover', action='store_true')
    args = parser.parse_args()
    if args.mode == 'worker':
        asyncio.run(worker(args)); return
    results = []
    for kind in ('duplicate_workers','crash_after_prepared','expired_live_owner','remote_cancel'):
        try:
            row = run_case(kind)
        except Exception as exc:
            row = {'case':kind,'status':'failed','error':str(exc)}
        results.append(row); print(json.dumps(row, ensure_ascii=False), flush=True)
    path = ROOT/'eval'/'results'/f'job-reliability-{datetime.now():%Y%m%d-%H%M%S}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'scope':'offline subprocess jobs; no external services', 'cases':results}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('report:', path)
    if any(row['status'] != 'passed' for row in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
