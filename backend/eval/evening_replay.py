"""Replay public evaluation snapshots with real routes; never saves user trips."""
import argparse
import asyncio
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from app.models.schemas import TripPlan, TripRequest
from app.services.amap_rest import close_client
from app.services.evening_planning import evening_coverage, repair_evening
from app.services.schedule_validator import close_route_queries, validate_schedule
from .run_eval import summarize


async def run(path, selected):
    original = path.read_bytes()
    results = []
    async def checker(plan, request, progress=None):
        await validate_schedule(plan, request, progress=progress)
    try:
        for row in json.loads(original):
            if row['id'] not in selected:
                continue
            plan, request = TripPlan(**row['plan_snapshot']), TripRequest(**row['case_request'])
            started = time.monotonic()
            fixed, notes = await repair_evening(plan, request, checker)
            result = dict(id=row['id'], seconds=round(time.monotonic()-started, 2), notes=notes,
                          coverage_before=evening_coverage(plan, request), coverage_after=evening_coverage(fixed, request),
                          before=summarize(plan, request, row.get('case_expect', {})),
                          after=summarize(fixed, request, {**row.get('case_expect', {}), 'activity_after': '18:00'}),
                          plan_snapshot=fixed.model_dump(mode='json'))
            results.append(result)
            print(json.dumps({key:result[key] for key in ('id','seconds','notes','coverage_before','coverage_after')}, ensure_ascii=False), flush=True)
    finally:
        await close_route_queries()
        await close_client()
    if len(results) != len(selected):
        raise ValueError('Missing selected cases')
    assert path.read_bytes() == original
    target = Path(__file__).parent/'results'/f'evening-replay-{datetime.now():%Y%m%d-%H%M%S}.json'
    target.write_text(json.dumps(dict(source=str(path), source_sha256=hashlib.sha256(original).hexdigest(),
        scope='same snapshot repair with queried/cached routes; not fresh model generation or lighting verification',
        results=results), ensure_ascii=False, indent=2), encoding='utf-8')
    print('report:', target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--cases', required=True)
    args = parser.parse_args()
    asyncio.run(run(args.report, set(args.cases.split(','))))
