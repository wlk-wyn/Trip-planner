"""Replay the public seven-day failure without altering its source or user trips."""
import asyncio
import argparse
import hashlib
import json
import tempfile
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import TripPlan,TripRequest
from app.services import schedule_validator as routes
from app.services.amap_rest import close_client
from app.services.meal_timing import repair_meal_timing,meal_timing_issues
from app.services.return_dinner import repair_return_dinner
from app.services.meal_windows import repair_final_meals
from eval.run_eval import summarize

ROOT=Path(__file__).resolve().parents[1]


async def run(remote=False):
    source=ROOT/'eval/results/load-20261017-180219.json';raw=source.read_bytes()
    row=next(r for r in json.loads(raw)['rows'] if r['id']=='load-bj-deep-7d' and r['repeat']==1)
    plan,request=TripPlan(**row['plan_snapshot']),TripRequest(**row['case_request'])
    validations=[];started=time.monotonic()
    async def progress(stage,*args):print(stage,round(time.monotonic()-started,2),flush=True)
    async def check(candidate,request,progress=None):
        await routes.validate_schedule(candidate,request,progress=progress)
        validations.append([{'day':d.day_index+1,'status':d.schedule.status,'conflicts':d.schedule.conflicts,
            'meals':[{'name':i.name,'start':i.start_time} for i in d.schedule.items if i.kind=='meal']} for d in candidate.days])
    try:
        with tempfile.TemporaryDirectory(prefix='suburban-replay-') as folder,patch.object(routes,'ROUTE_CACHE_PATH',Path(folder)/'routes.sqlite3'):
            routes._route_cache.clear()
            async with asyncio.timeout(120):
                fixed,notes=await repair_meal_timing(plan,request,check,progress,allow_duration_fit=True)
                fixed,more=await repair_return_dinner(fixed,request,check,progress);notes.extend(more)
                fixed,more=await repair_final_meals(fixed,request,check,progress=progress);notes.extend(more)
                if remote:
                    from app.services.remote_day import repair_remote_day
                    from app.agents.langgraph_planner import get_langgraph_planner
                    fixed,more=await repair_remote_day(fixed,request,check,get_langgraph_planner()._align_meals_to_route,progress);notes.extend(more)
            report={'scope':'same public failed snapshot; existing meal repair sequence, isolated cache; real map, no model generation',
                'remote_replacement':remote,'source':str(source),'source_sha256':hashlib.sha256(raw).hexdigest(),'seconds':round(time.monotonic()-started,2),
                'notes':notes,'before':summarize(plan,request,row['case_expect']),'after':summarize(fixed,request,row['case_expect']),
                'meal_issues':meal_timing_issues(fixed,request),'validations':validations,'plan_snapshot':fixed.model_dump(),'case_request':request.model_dump(),'case_expect':row['case_expect']}
            assert raw==source.read_bytes()
            path=ROOT/'eval/results'/f'suburban-replay-{datetime.now():%Y%m%d-%H%M%S}.json';path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'report':str(path),'seconds':report['seconds'],'notes':notes,'passed':report['after']['reference_checks_passed'],'meal_issues':report['meal_issues']},ensure_ascii=False),flush=True)
    finally:await routes.close_route_queries();await close_client()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--remote',action='store_true');args=parser.parse_args()
    asyncio.run(run(args.remote))
