"""Offline multi-process queue load probe. Never opens the user trip store.

Compares two-second polling alone with local completion wakeups. Work is a
synthetic 150ms wait, NOT a model/map latency or production throughput claim.
"""
import argparse
import asyncio
import json
import math
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.config import get_settings
from app.services import planning_runs as jobs
from app.services.queue_admission import QueueCapacityError
from app.services.trip_repository import TripRepository
from eval.job_reliability import REQUEST, PLAN

ROOT=Path(__file__).resolve().parents[1]


async def worker(args):
    repo=TripRepository(args.db)
    settings=get_settings();settings.planning_max_running=2
    async def generate(request, run_id, progress):
        with repo.connect() as db: db.execute('INSERT INTO probe_started VALUES (?,?)',(run_id,time.time()))
        await progress('离线调度负载，不调用外部服务')
        await asyncio.sleep(.15)
        return PLAN.model_copy(deep=True)
    async def tickets(*args): pass
    jobs._generate=generate;jobs._estimate_tickets=tickets
    with repo.connect() as db: db.execute('INSERT INTO probe_ready DEFAULT VALUES')
    async with asyncio.timeout(25):
        while True:
            with repo.connect() as db: go=db.execute('SELECT count(*) FROM probe_go').fetchone()[0]
            if go:break
            await asyncio.sleep(.02)
        jobs.start_recovery(repo)
        if args.poll_only:
            class NoWake:
                def set(self): pass
            jobs._recovery_wakeups[str(repo.path.resolve())]=NoWake()
        try:
            while True:
                with repo.connect() as db:
                    count=db.execute("SELECT count(*) FROM planning_runs WHERE status IN ('queued','running')").fetchone()[0]
                if not count:break
                await asyncio.sleep(.02)
        finally: await jobs.shutdown_runs()


def case(poll_only):
    settings=get_settings();settings.planning_max_pending=8;settings.planning_max_pending_per_owner=2
    with tempfile.TemporaryDirectory(prefix='queue-load-') as folder:
        repo=TripRepository(Path(folder)/'jobs.sqlite3')
        with repo.connect() as db:
            db.executescript('CREATE TABLE probe_ready(id INTEGER PRIMARY KEY);CREATE TABLE probe_go(id INTEGER PRIMARY KEY);CREATE TABLE probe_started(run_id TEXT,at REAL);')
        processes=[];logs=[]
        try:
            for i in range(3):
                log=open(Path(folder)/f'worker-{i}.log','w',encoding='utf-8');logs.append(log)
                processes.append(subprocess.Popen([sys.executable,'-X','utf8','-m','eval.queue_load','worker','--db',str(repo.path),*(['--poll-only'] if poll_only else [])],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT))
            deadline=time.monotonic()+20
            while True:
                with repo.connect() as db: ready=db.execute('SELECT count(*) FROM probe_ready').fetchone()[0]
                if ready==3:break
                if time.monotonic()>deadline or any(p.poll() is not None for p in processes):raise RuntimeError('fixture workers did not become ready')
                time.sleep(.03)
            accepted=[];rejected=0
            for _ in range(16):
                owner,id=str(uuid4()),str(uuid4())
                try:
                    run=jobs.start_create_run(repo,owner,REQUEST.model_dump(),id,schedule=False)
                    accepted.append((owner,run['id']))
                except QueueCapacityError:rejected+=1
            owner,id=accepted[0]
            assert jobs.start_create_run(repo,owner,REQUEST.model_dump(),id,schedule=False)['id']==id
            start=time.time();maximum_running=0
            with repo.connect() as db: db.execute('INSERT INTO probe_go DEFAULT VALUES')
            deadline=time.monotonic()+20
            while True:
                with repo.connect() as db:
                    rows=db.execute('SELECT status,updated_at FROM planning_runs').fetchall()
                    active=db.execute("SELECT count(*) FROM planning_runs WHERE status='running' AND lease_until>?",(time.time(),)).fetchone()[0]
                maximum_running=max(maximum_running,active)
                if all(r['status'] in jobs.TERMINAL for r in rows):break
                if time.monotonic()>deadline or any(p.poll() not in (None,0) for p in processes):raise RuntimeError('fixture queue did not drain')
                time.sleep(.01)
            for p in processes:p.wait(timeout=5)
            with repo.connect() as db:
                generated=db.execute('SELECT count(*),count(DISTINCT run_id) FROM probe_started').fetchone()
                trips=db.execute('SELECT count(*) FROM trips').fetchone()[0]
                versions=db.execute('SELECT count(*) FROM versions').fetchone()[0]
            elapsed=sorted(datetime.fromisoformat(r['updated_at']).timestamp()-start for r in rows)
            assert len(rows)==8 and rejected==8 and generated[0]==generated[1]==trips==versions==8
            assert maximum_running<=2 and all(r['status']=='succeeded' for r in rows)
            return {'mode':'poll_only' if poll_only else 'completion_wakeup','workers':3,'burst_requests':16,'accepted':8,'rejected':8,'generated_once':8,'trips':trips,'versions':versions,'sampled_max_running':maximum_running,'completion_p50_seconds':round((elapsed[3]+elapsed[4])/2,3),'completion_p95_seconds':round(elapsed[math.ceil(.95*len(elapsed))-1],3),'wall_seconds':round(time.time()-start,3),'all_succeeded':True}
        finally:
            for p in processes:
                if p.poll() is None:p.kill();p.wait(timeout=5)
            for log in logs:log.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['run','worker'],nargs='?',default='run');parser.add_argument('--db');parser.add_argument('--poll-only',action='store_true');args=parser.parse_args()
    if args.mode=='worker':asyncio.run(worker(args));return
    rows=[case(True),case(False)]
    result={'scope':'offline local multi-process durable queue; synthetic 150ms work, no model/map; descriptive small sample, not production latency','cases':rows}
    path=ROOT/'var'/f'queue-load-{datetime.now():%Y%m%d-%H%M%S}.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2));print('report:',path)


if __name__=='__main__':main()
