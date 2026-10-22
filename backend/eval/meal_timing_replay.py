"""Replay public snapshots to validate meal ordering; no user-trip writes."""
import argparse
import asyncio
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from app.models.schemas import TripPlan, TripRequest
from app.services.amap_rest import close_client
from app.services.meal_timing import meal_timing_issues, repair_meal_timing
from app.services.return_dinner import repair_return_dinner
from app.services.meal_windows import repair_final_meals
from app.services.schedule_validator import close_route_queries, validate_schedule
from .run_eval import summarize


async def run(sources, fit_durations=False, return_dinner=False, late_meals=False):
    results = []
    validations = []
    async def checker(plan, request, progress=None):
        await validate_schedule(plan, request, progress=progress)
        validations.append([dict(date=d.date, status=d.schedule.status, conflicts=d.schedule.conflicts,
            items=[dict(id=i.id, name=i.name, start=i.start_time, end=i.end_time) for i in d.schedule.items]) for d in plan.days])
    try:
        for specification in sources:
            filename, case = specification.rsplit('::', 1)
            path = Path(filename)
            original = path.read_bytes()
            row = next(r for r in json.loads(original) if r['id'] == case)
            plan, request = TripPlan(**row['plan_snapshot']), TripRequest(**row['case_request'])
            validations.clear()
            started = time.monotonic()
            fixed, notes = await repair_meal_timing(plan, request, checker, allow_duration_fit=fit_durations)
            if return_dinner:
                fixed, return_notes = await repair_return_dinner(fixed, request, checker)
                notes.extend(return_notes)
            if late_meals:
                fixed, meal_notes = await repair_final_meals(fixed, request, checker)
                notes.extend(meal_notes)
            result = dict(id=case, source=str(path), source_sha256=hashlib.sha256(original).hexdigest(),
                          seconds=round(time.monotonic()-started, 2), notes=notes,
                          before=summarize(plan,request,row.get('case_expect',{})),
                          after=summarize(fixed,request,row.get('case_expect',{})),
                          meal_issues=meal_timing_issues(fixed,request), validation_attempts=list(validations), plan_snapshot=fixed.model_dump(mode='json'))
            assert original == path.read_bytes()
            results.append(result)
            print(json.dumps(dict(id=case, seconds=result['seconds'], notes=notes, meal_issues=result['meal_issues'], schedule=result['after']['schedule_passed']),ensure_ascii=False),flush=True)
    finally:
        await close_route_queries()
        await close_client()
    target = Path(__file__).parent/'results'/f'meal-timing-{datetime.now():%Y%m%d-%H%M%S}.json'
    target.write_text(json.dumps(dict(scope='same snapshot, real/cached routes, no new model generation', optional_duration_fit=fit_durations, return_dinner=return_dinner, late_meals=late_meals, results=results),ensure_ascii=False,indent=2),encoding='utf-8')
    print('report:', target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', action='append', required=True, help='report.json::case-id')
    parser.add_argument('--fit-durations', action='store_true')
    parser.add_argument('--return-dinner', action='store_true')
    parser.add_argument('--late-meals', action='store_true')
    args = parser.parse_args()
    asyncio.run(run(args.source, args.fit_durations, args.return_dinner, args.late_meals))
