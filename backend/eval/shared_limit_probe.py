"""Offline cross-process permit verification; no provider HTTP or real keys."""
import argparse
import asyncio
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from app.services import provider_limits as limits

ROOT = Path(__file__).resolve().parents[1]


async def worker(args):
    original = limits.attempt
    def observed(path, scope, qps):
        clock = []
        def instant():
            clock.append(time.time()); return clock[-1]
        delay = original(path, scope, qps, instant)
        if delay == 0:
            with closing(sqlite3.connect(path)) as db:
                db.execute('INSERT INTO observations VALUES (?,?)', (scope, clock[-1])); db.commit()
        return delay
    limits.attempt = observed
    async def permit():
        await limits.acquire(args.key, args.kind, 10, path=args.db)
    if args.cancel:
        # Prime inside the child so slow interpreter startup cannot consume the
        # test's waiting window before cancellation is exercised.
        await limits.acquire(args.key, args.kind, .1, path=args.db)
        tasks = [asyncio.create_task(permit()) for _ in range(20)]
        await asyncio.sleep(.015)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    else:
        for _ in range(args.count): await permit()


def probe():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder)/'limits.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE observations(scope TEXT,instant REAL)'); db.commit()
        children, logs = [], []
        def spawn(key='offline-key', kind='route', count=4, cancel=False):
            log = open(Path(folder)/f'worker-{len(children)}.log','w',encoding='utf-8'); logs.append(log)
            command=[sys.executable,'-X','utf8','-m','eval.shared_limit_probe','worker','--db',str(path),'--key',key,'--kind',kind,'--count',str(count)]
            if cancel: command.append('--cancel')
            child=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT);children.append(child)
            return child
        def join(group):
            for child in group:
                if child.wait(timeout=20) != 0: raise AssertionError('probe worker failed')
        try:
            join([spawn(),spawn()])
            scope=limits.scope_id('offline-key','route')
            with closing(sqlite3.connect(path)) as db:
                first=[row[0] for row in db.execute('SELECT instant FROM observations WHERE scope=? ORDER BY instant',(scope,))]
            gaps=[b-a for a,b in zip(first,first[1:])]
            assert len(first)==8 and min(gaps)>=.099, gaps
            # A restarted process uses the same persisted clock, not a reset bucket.
            join([spawn(count=2)])
            with closing(sqlite3.connect(path)) as db:
                all_marks=[row[0] for row in db.execute('SELECT instant FROM observations WHERE scope=? ORDER BY instant',(scope,))]
            assert len(all_marks)==10 and min(b-a for a,b in zip(all_marks,all_marks[1:]))>=.099
            join([spawn(kind='poi',count=2),spawn(key='different-offline-key',count=2)])
            # The child primes its own current slot before creating waiters.
            cancel_scope=limits.scope_id('cancel-key','route')
            join([spawn(key='cancel-key',cancel=True)])
            with closing(sqlite3.connect(path)) as db:
                after=db.execute('SELECT last_grant FROM permits WHERE scope=?',(cancel_scope,)).fetchone()[0]
                grants=db.execute('SELECT instant FROM observations WHERE scope=?',(cancel_scope,)).fetchall()
            assert len(grants)==1, 'cancelled batch spent a new slot'
            before=grants[0][0]
            assert before==after, 'cancelled batch spent a new slot'
            return {'scope':'synthetic local permits; no external services', 'processes':len(children),
                    'shared_route_permits':10,'minimum_shared_gap_seconds':round(min(gaps),6),
                    'configured_gap_seconds':.1,'restart_preserved_spacing':True,
                    'independent_key_and_kind_completed':True,'cancelled_batch_reserved_no_future_slots':True}
        finally:
            for child in children:
                if child.poll() is None:child.kill();child.wait(timeout=5)
            for log in logs:log.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',nargs='?',default='run',choices=['run','worker'])
    parser.add_argument('--db');parser.add_argument('--key');parser.add_argument('--kind',default='route')
    parser.add_argument('--count',type=int,default=4);parser.add_argument('--cancel',action='store_true')
    args=parser.parse_args()
    if args.mode=='worker':asyncio.run(worker(args));return
    result=probe()
    path=ROOT/'eval'/'results'/f'shared-limit-{datetime.now():%Y%m%d-%H%M%S}.json'
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False));print('report:',path)


if __name__=='__main__':main()
