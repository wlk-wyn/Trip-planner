"""Historical public timing replay; no new model, route query or gate evidence.

The before/after figures describe this snapshot's stored traffic only. They do
not establish today's route validity, production speed, admission or bookings.
"""
import asyncio
import argparse
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from app.models.schemas import TripPlan, TripRequest
from app.services.meal_timing import repair_meal_timing, meal_timing_issues
from app.services.venue_routing import gate_issues


async def run(source=None,snapshot_index=None,day_index=None):
    root=Path(__file__).resolve().parents[1]
    source=Path(source).resolve() if source else root/'tests/fixtures/late_meal_duration_failure.json'
    raw=source.read_bytes();record=json.loads(raw)
    if snapshot_index is not None:
        record=record['validation_snapshots'][snapshot_index]
    plan,request=TripPlan(**record['plan_snapshot']),TripRequest(**record['case_request'])
    if day_index is not None:
        plan=plan.model_copy(update={'days':[day for day in plan.days if day.day_index==day_index]})
        if len(plan.days)!=1:raise ValueError('expected one selected public day')
    before=plan.model_dump(mode='json');calls=0
    async def no_provider(*_):
        nonlocal calls
        calls+=1
        raise RuntimeError('Offline replay does not query or invent new route edges')
    started=time.perf_counter()
    fixed,notes=await repair_meal_timing(plan,request,no_provider,allow_duration_fit=True,local_first=True)
    elapsed=time.perf_counter()-started
    before_edges=[s.model_dump() for d in plan.days if d.schedule for s in d.schedule.segments]
    after_edges=[s.model_dump() for d in fixed.days if d.schedule for s in d.schedule.segments]
    report={'scope':__doc__.splitlines()[0],'source':str(source.relative_to(root)),
        'snapshot_index':snapshot_index,'day_index':day_index,
        'source_sha256':hashlib.sha256(raw).hexdigest(),'seconds':round(elapsed,4),
        'model_calls':0,'network_calls':0,'validation_calls':calls,'notes':notes,
        'before_meal_issues':meal_timing_issues(plan,request),'after_meal_issues':meal_timing_issues(fixed,request),
        'original_preserved':before==plan.model_dump(mode='json') and raw==source.read_bytes(),
        'queried_edges_preserved':before_edges==after_edges,'gate_issues_retained':gate_issues(plan)==gate_issues(fixed),
        'gate_issues':gate_issues(fixed),'execution_ready':False,'plan_snapshot':fixed.model_dump(mode='json')}
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source');parser.add_argument('--snapshot-index',type=int);parser.add_argument('--day-index',type=int)
    args=parser.parse_args();report=asyncio.run(run(args.source,args.snapshot_index,args.day_index))
    target=Path(__file__).parent/'results'/f'meal-first-replay-{datetime.now():%Y%m%d-%H%M%S}.json'
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='plan_snapshot'},ensure_ascii=False,indent=2));print(target.name)
