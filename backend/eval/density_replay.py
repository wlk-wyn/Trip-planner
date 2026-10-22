"""Replay one real public unvalidated draft with no model/network/store writes.

The original receipt remains unchanged. A better optimistic order is a routing
candidate, never evidence of actual traffic, admission or execution readiness.
"""
import asyncio
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

from app.models.schemas import TripPlan,TripRequest
from app.agents.travel_agent import TravelAgent
from app.services.draft_density import density_rows
from app.services.observability import usage_scope


async def run():
    source=Path(__file__).parent/'results/load-20261018-060309.json';raw=source.read_bytes()
    record=next(s for s in json.loads(raw)['density_review_snapshots'] if s['city']=='杭州')
    plan,request=TripPlan(**record['before']),TripRequest(**record['case_request'])
    original=plan.model_dump(mode='json');agent=TravelAgent.__new__(TravelAgent)
    agent.llm=AsyncMock();agent.llm.ainvoke.side_effect=AssertionError('no paid model needed for this order')
    started=time.perf_counter()
    with usage_scope() as usage:fixed=await agent._review_draft_density(plan,request)
    report={'scope':__doc__.splitlines()[0],'source':source.name,'source_sha256':hashlib.sha256(raw).hexdigest(),
        'seconds':round(time.perf_counter()-started,4),'events':usage['events'],'model_calls':agent.llm.ainvoke.await_count,
        'network_calls':0,'before_advice':density_rows(plan,request),'after_advice':density_rows(fixed,request),
        'before_order':[[p.name for p in d.attractions] for d in plan.days],
        'after_order':[[p.name for p in d.attractions] for d in fixed.days],
        'all_places_and_durations_preserved':sorted((p.poi_id,p.visit_duration) for d in plan.days for p in d.attractions)==sorted((p.poi_id,p.visit_duration) for d in fixed.days for p in d.attractions),
        'meals_preserved':[m.model_dump() for d in plan.days for m in d.meals]==[m.model_dump() for d in fixed.days for m in d.meals],
        'source_preserved':original==plan.model_dump(mode='json') and raw==source.read_bytes(),
        'no_fabricated_schedule':all(d.schedule is None for d in fixed.days),'execution_ready':False,
        'case_request':request.model_dump(mode='json'),'plan_snapshot':fixed.model_dump(mode='json')}
    return report


if __name__=='__main__':
    report=asyncio.run(run());target=Path(__file__).parent/'results'/f'density-replay-{datetime.now():%Y%m%d-%H%M%S}.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('plan_snapshot','case_request')},ensure_ascii=False,indent=2));print(target.name)
