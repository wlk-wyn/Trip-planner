"""Synthetic cross-process admission and crashed-request lease recovery; no providers."""
import json
import multiprocessing as mp
import tempfile
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from app.services.request_gate import RequestGate, Policy, GateFull


def worker(path, number, ready, start, release, output):
    gate=RequestGate(path,{'interactive':Policy(2,1,100,1000)})
    gate.probe();ready.put(number);start.wait(20)
    admitted,rejected,peak,tokens=0,0,0,[]
    for _ in range(6):
        token=str(uuid4())
        try:gate.acquire(token,'interactive',f'worker-{number}');admitted+=1;tokens.append(token)
        except GateFull:rejected+=1
        db=gate.connect()
        try:peak=max(peak,db.execute('SELECT COUNT(*) FROM http_leases').fetchone()[0])
        finally:db.close()
    output.put({'admitted':admitted,'rejected':rejected,'peak':peak})
    release.wait(20)
    for token in tokens:gate.release(token)


def crashed_worker(path, output, wait):
    gate=RequestGate(path,{'interactive':Policy(1,1,100,100)},ttl=2)
    token=str(uuid4());gate.acquire(token,'interactive','crashed-worker');output.put(token)
    wait.wait(30)


def run():
    ctx=mp.get_context('spawn')
    with tempfile.TemporaryDirectory(prefix='trip-http-gate-') as folder:
        path=Path(folder)/'gate.sqlite3'
        gate=RequestGate(path,{'interactive':Policy(2,1,100,1000)})
        gate.probe();ready,output=ctx.Queue(),ctx.Queue();start,release=ctx.Event(),ctx.Event()
        processes=[ctx.Process(target=worker,args=(str(path),i,ready,start,release,output)) for i in range(4)]
        try:
            for process in processes:process.start()
            for _ in processes:ready.get(timeout=30)
            start.set();rows=[output.get(timeout=30) for _ in processes]
        finally:
            release.set()
            for process in processes:
                if process.pid:process.join(5)
                if process.is_alive():process.terminate();process.join(5)
        assert all(process.exitcode==0 for process in processes)
        assert sum(row['admitted'] for row in rows)==2
        assert sum(row['rejected'] for row in rows)==22
        assert max(row['peak'] for row in rows)==2
        db=gate.connect()
        try:remaining=db.execute('SELECT COUNT(*) FROM http_leases').fetchone()[0]
        finally:db.close()
        assert remaining==0
        crash_path=Path(folder)/'crash.sqlite3';output=ctx.Queue();stop=ctx.Event()
        process=ctx.Process(target=crashed_worker,args=(str(crash_path),output,stop))
        crash_gate=RequestGate(crash_path,{'interactive':Policy(1,1,100,100)},ttl=2)
        try:
            process.start();old=output.get(timeout=30)
            process.terminate();process.join(5)
            assert not process.is_alive()
            rejected=False
            try:crash_gate.acquire('early','interactive','replacement')
            except GateFull:rejected=True
            assert rejected,'crashed request must retain its lease before expiry'
            db=crash_gate.connect()
            try:expires=db.execute('SELECT expires FROM http_leases WHERE id=?',(old,)).fetchone()[0]
            finally:db.close()
            time.sleep(max(0,expires-time.time())+.03)
            crash_gate.acquire('replacement','interactive','replacement')
            crash_gate.release('replacement')
        finally:
            # A forcibly terminated waiter may leave its Event's process lock
            # owned. Never set that Event after intentionally killing the waiter.
            if process.is_alive():process.terminate();process.join(5)
        return {'kind':'synthetic-http-resource-admission-not-planning-performance','workers':4,
                'requests':24,'admitted':2,'rejected':22,'peak_active':2,'leases_after_release':remaining,
                'crashed_worker':{'rejected_before_expiry':rejected,'admitted_after_expiry':True},'rows':rows}


if __name__=='__main__':
    result=run()
    target=Path(__file__).resolve().parent/'results'/f"http-gate-{datetime.now():%Y%m%d-%H%M%S}.json"
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(target.name);print(json.dumps(result,ensure_ascii=False))
