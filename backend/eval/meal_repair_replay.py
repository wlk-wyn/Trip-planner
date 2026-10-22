"""Recheck a public evaluation snapshot's final meal repair, without a model.

python -m eval.meal_repair_replay --report eval/results/run-....json --cases bj-history-2d,hz-slow-elder-2d
Only map queries and local reports; never writes user trips or the source report.
"""
import argparse
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from app.models.schemas import TripPlan, TripRequest
from app.agents.travel_agent import TravelAgent
from app.services.meal_windows import repair_final_meals
from app.services.initial_planning import fit_optional_visits
from app.services.schedule_validator import close_route_queries
from app.services.amap_rest import close_client
from .run_eval import summarize


async def run(path, selected):
    rows = json.loads(path.read_text(encoding='utf-8'))
    output = []
    try:
        for row in rows:
            if row['id'] not in selected: continue
            plan, request = TripPlan(**row['plan_snapshot']), TripRequest(**row['case_request'])
            checker = TravelAgent.__new__(TravelAgent)._check_schedule
            started = time.monotonic()
            fixed, final_notes = await repair_final_meals(plan, request, checker)
            fixed, notes = await fit_optional_visits(fixed, request, checker)
            notes = [*final_notes, *notes]
            changes=[]
            for before, after in zip(plan.days, fixed.days):
                for old, new in zip(before.meals, after.meals):
                    if old.poi_id != new.poi_id:
                        changes.append({'date':after.date,'from':old.restaurant or old.name,'to':new.restaurant or new.name})
            result={'id':row['id'],'seconds':round(time.monotonic()-started,2),'critical_meal_context_limit':2,'timing_meal_context_limit':1,
                    'meal_changes':changes,'visit_notes':notes,'before':summarize(plan,request,row.get('case_expect',{})),
                    'after':summarize(fixed,request,row.get('case_expect',{})), 'plan_snapshot':fixed.model_dump(mode='json')}
            output.append(result)
            print(json.dumps({k:result[k] for k in ('id','seconds','critical_meal_context_limit','timing_meal_context_limit','meal_changes','visit_notes')},ensure_ascii=False),flush=True)
            print('schedule:',result['before']['schedule_passed'],'->',result['after']['schedule_passed'],flush=True)
    finally:
        await close_route_queries(); await close_client()
    target=Path(__file__).parent/'results'/f'meal-replay-{datetime.now():%Y%m%d-%H%M%S}.json'
    target.write_text(json.dumps({'source_report':str(path),'scope':'final repair of snapshots, not new end-to-end generation','results':output},ensure_ascii=False,indent=2),encoding='utf-8')
    print('report:',target)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--report',type=Path,required=True);parser.add_argument('--cases',required=True)
    args=parser.parse_args();asyncio.run(run(args.report,set(args.cases.split(','))))
