"""One real public POI queried through 32 concurrent ASGI API requests.

No saved-trip database, model, or user coordinates. API dispatch is in-process;
only provider traffic is real, so this is not a production HTTP load test.
"""
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI
from app.api.routes.poi import router
from app.services import amap_rest as rest
from app.services.observability import usage_scope


async def run():
    rest._poi_cache.clear()
    app=FastAPI();app.include_router(router,prefix='/api')
    try:
        with usage_scope() as usage:
            started=time.perf_counter()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://public-poi-eval') as client:
                responses=await asyncio.gather(*(client.get('/api/poi/detail/B000A84GDN') for _ in range(32)))
                elapsed=time.perf_counter()-started
                before=dict(usage['events'])
                warm=await client.get('/api/poi/detail/B000A84GDN')
            data=[r.json().get('data') for r in responses if r.status_code==200]
            report={'scope':__doc__.splitlines()[0],'api_transport':'ASGI in process','public_poi_id':'B000A84GDN',
                'generated_at':datetime.now(timezone.utc).isoformat(),'requests':32,
                'http_statuses':{str(code):sum(r.status_code==code for r in responses) for code in sorted({r.status_code for r in responses})},
                'burst_seconds':round(elapsed,3),'burst_events':before,'warm_status':warm.status_code,'all_events':usage['events'],
                'unique_observation_times':len({p.get('observed_at') for p in data}),
                'all_same_gate':len(data)==32 and all(p.get('id')=='B000A84GDN' and p.get('name')=='故宫博物院-午门' for p in data),
                'provider_sample':data[0] if data else None,'saved_trip_writes':0,'model_calls':0}
            return report
    finally:await rest.close_client()


if __name__=='__main__':
    logging.getLogger('httpx').setLevel(logging.WARNING)
    report=asyncio.run(run())
    target=Path(__file__).parent/'results'/f'poi-fanout-{datetime.now():%Y%m%d-%H%M%S}.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='provider_sample'},ensure_ascii=False,indent=2));print(target.name)
