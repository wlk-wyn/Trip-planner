"""Isolated multi-process SQLite cache correctness probe, no network or model.

Four workers share only a disposable cache path. Their writes are read back
and checked with SQLite integrity_check. This is not a production load test.
"""
import argparse
import json
import multiprocessing
import sqlite3
import tempfile
from concurrent.futures import ProcessPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def worker(path,index,barrier):
    from app.services.schedule_validator import _disk_cache
    stamp=datetime.now(timezone.utc).isoformat();failures=[]
    barrier.wait(timeout=30)
    for item in range(8):
        key=('worker',index,item)
        try:
            _disk_cache(key,path=Path(path))
            _disk_cache(key,({'duration_minutes':12+index},stamp),Path(path))
        except sqlite3.Error as error:failures.append(type(error).__name__)
    return {'worker':index,'attempted':8,'errors':failures}


def run():
    context=multiprocessing.get_context('spawn')
    with tempfile.TemporaryDirectory(prefix='disk-workers-') as folder,context.Manager() as manager:
        rows=[]
        for repeat in range(3):
            path=Path(folder)/f'cache-{repeat}.sqlite3';barrier=manager.Barrier(4)
            with ProcessPoolExecutor(max_workers=4,mp_context=context) as pool:
                futures=[pool.submit(worker,str(path),i,barrier) for i in range(4)]
                workers=[future.result(timeout=45) for future in futures]
            with closing(sqlite3.connect(path)) as db:
                integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
                values=db.execute('SELECT key,result FROM route_cache').fetchall()
            valid=sum(json.loads(value)['duration_minutes']==12+json.loads(key)[1] for key,value in values)
            rows.append({'round':repeat+1,'workers':workers,'rows':len(values),'correct_rows':valid,'integrity':integrity})
    return {'scope':__doc__.splitlines()[0],'sqlite_version':sqlite3.sqlite_version,'processes':4,'rounds':rows,
            'passed':all(r['rows']==32 and r['correct_rows']==32 and r['integrity']=='ok' and not any(w['errors'] for w in r['workers']) for r in rows)}


if __name__=='__main__':
    report=run()
    target=Path(__file__).parent/'results'/f'disk-workers-{datetime.now():%Y%m%d-%H%M%S}.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));print(target.name)
    raise SystemExit(0 if report['passed'] else 1)
