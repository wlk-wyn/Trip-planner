"""Offline cold-cache and identical POI burst probe. No model/provider requests.

Use --label before/after to retain independent receipts. These timings measure
only local orchestration with a synthetic 30 ms provider, not planning latency.
"""
import argparse
import asyncio
import json
import logging
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.services import amap_rest as amap, schedule_validator as routes


class Warnings(logging.Handler):
    def __init__(self):
        super().__init__(); self.count=0

    def emit(self, record):
        if record.levelno >= logging.WARNING:self.count+=1


async def run():
    receipt={'scope':__doc__.splitlines()[0], 'network_calls':0,'synthetic_provider_ms':30,'disk_rounds':[]}
    stamp=datetime.now(timezone.utc).isoformat()
    handler=Warnings();routes.logger.addHandler(handler)
    try:
        with tempfile.TemporaryDirectory(prefix='cache-fanout-') as folder:
            for repeat in range(5):
                path=Path(folder)/f'cold-{repeat}.sqlite3'
                routes._disk_disabled_until=0
                before=handler.count; started=time.perf_counter()
                with patch.object(routes,'ROUTE_CACHE_PATH',path):
                    await asyncio.gather(*(routes._best_effort_disk(('probe',i)) for i in range(32)))
                    await asyncio.gather(*(routes._best_effort_disk(('probe',i),({'duration_minutes':12},stamp)) for i in range(32)))
                    hits=await asyncio.gather(*(routes._best_effort_disk(('probe',i)) for i in range(32)))
                    receipt['disk_rounds'].append({'round':repeat+1,'keys':32,'hits':sum(v is not None for v in hits),
                        'warning_count':handler.count-before,'backoff_active':routes._disk_disabled_until>time.monotonic(),
                        'seconds':round(time.perf_counter()-started,4)})
    finally:routes.logger.removeHandler(handler)
    amap._poi_cache.clear()
    calls=0
    async def provider(*_args):
        nonlocal calls
        calls+=1;await asyncio.sleep(.03)
        return {'pois':[{'id':'synthetic','name':'合成地点','location':'116.4,39.9'}]}
    started=time.perf_counter()
    try:
        with patch.object(amap,'_get_json',side_effect=provider):
            points=await asyncio.gather(*(amap.place_detail('synthetic') for _ in range(32)))
            receipt['poi_burst']={'requests':32,'provider_calls':calls,'valid_results':sum(p['poi_id']=='synthetic' for p in points),
                'seconds':round(time.perf_counter()-started,4),'unique_observation_times':len({p['observed_at'] for p in points})}
    finally:await amap.close_client();await routes.close_route_queries();amap._poi_cache.clear()
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--label',choices=['before','after'],required=True)
    args=parser.parse_args();report=asyncio.run(run());report['label']=args.label
    target=Path(__file__).parent/'results'/f'cache-fanout-{args.label}-{datetime.now():%Y%m%d-%H%M%S}.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
