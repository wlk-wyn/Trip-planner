"""Real model/map planning samples, concurrency two, isolated job/cache/checkpoint stores.

python -X utf8 -m eval.load_eval --repeat 2
Not an HTTP load test. Two samples/case cannot establish a production p95.
"""
import argparse
import asyncio
import json
import tempfile
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from eval.run_eval import run_one, aggregate, EXPECT_KEYS, case_request
from app.services.trip_repository import TripRepository

ROOT=Path(__file__).resolve().parents[1]


def load_cases(case_file=None):
    rows=json.loads((Path(case_file) if case_file else Path(__file__).parent/'load_cases.json').read_text(encoding='utf-8'))
    if len({r['id'] for r in rows})!=len(rows):raise ValueError('duplicate case IDs')
    for row in rows:
        case_request(row)
        if set(row.get('expect',{}))-EXPECT_KEYS:raise ValueError('unsupported case expectations')
    return rows


async def run(repeat, selected=None, case_file=None, capture_schedules=False):
    from app.services import planning_runs as jobs, schedule_validator as routes, amap_rest as amap, official_tickets as official
    from app.agents import travel_agent
    if not 1<=repeat<=20:raise ValueError('repeat must be between 1 and 20')
    cases=load_cases(case_file)
    if selected:
        if set(selected)-{c['id'] for c in cases}:raise ValueError('unknown case ID')
        cases=[c for c in cases if c['id'] in selected]
    settings=jobs.get_settings()
    if settings.planning_max_running<2:raise ValueError('requires at least two configured planning execution slots')
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    path=ROOT/'eval'/'results'/f'load-{stamp}.json';path.parent.mkdir(exist_ok=True)
    rows=[]
    report={'scope':'real configured model/map, persistent planning including price enrichment and save; up to two active tasks; driver waiting excluded from task latency; not HTTP load or production p95',
            'case_file':str(case_file or 'load_cases.json'),
            'cache_policy':'first wave starts with empty isolated route disk/memory and POI/official policy memory; later waves reuse these caches but stochastic plans may miss; remote model cache not controlled',
            'provider_metric_scope':'shared query HTTP costs belong to the initiating task; waiters count joins; event seconds are accumulated operation time, not additive wall time',
            'configured_limits':{'route_qps':settings.amap_route_qps,'poi_qps':settings.amap_poi_qps,'metadata_qps':getattr(settings,'amap_metadata_qps',1),'planning_max_running':settings.planning_max_running},'rows':rows}
    if capture_schedules:
        report['validation_snapshots']=[]
        report['density_review_snapshots']=[]
    original_check=travel_agent.TravelAgent._check_schedule
    async def capture(agent,plan,request,progress=None):
        result=await original_check(agent,plan,request,progress)
        report['validation_snapshots'].append({'city':request.city,'case_request':request.model_dump(mode='json'),
            'plan_snapshot':plan.model_dump(mode='json')})
        return result
    original_review=travel_agent.TravelAgent._review_draft_density
    async def capture_review(agent,plan,request,progress=None):
        from app.services.observability import _usage
        usage=_usage.get();events=dict(usage['events']) if usage else {}
        before=plan.model_dump(mode='json');started=time.monotonic()
        result=await original_review(agent,plan,request,progress)
        after_events=usage['events'] if usage else {}
        report['density_review_snapshots'].append({'city':request.city,'seconds':round(time.monotonic()-started,3),
            'case_request':request.model_dump(mode='json'),'before':before,'after':result.model_dump(mode='json'),
            'event_delta':{key:round(value-events.get(key,0),6) for key,value in after_events.items() if value!=events.get(key,0)}})
        return result
    def save():
        report['aggregate']=aggregate(rows)
        path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    try:
        with tempfile.TemporaryDirectory(prefix='planning-load-') as folder:
            repo=TripRepository(Path(folder)/'trips.sqlite3')
            with patch.object(routes,'ROUTE_CACHE_PATH',Path(folder)/'route-cache.sqlite3'),patch.object(travel_agent,'checkpoint_file',return_value=Path(folder)/'checkpoints.sqlite3'),\
                 (patch.object(travel_agent.TravelAgent,'_check_schedule',capture) if capture_schedules else nullcontext()),\
                 (patch.object(travel_agent.TravelAgent,'_review_draft_density',capture_review) if capture_schedules else nullcontext()):
                routes._route_cache.clear();amap._poi_cache.clear();amap._region_cache.clear();amap._geocode_cache.clear();official._cache.clear()
                semaphore=asyncio.Semaphore(2)
                for wave in range(1,repeat+1):
                    async def one(case):
                        waiting=time.monotonic()
                        async with semaphore:
                            driver_wait=time.monotonic()-waiting
                            row=await run_one(case,wave,360,repo,str(uuid4()))
                            row.update(cache_policy='isolated_cache_initial_wave' if wave==1 else 'reuse_previous_wave_cache_not_guaranteed_hit',driver_wait_seconds=round(driver_wait,2))
                            rows.append(row);save()
                            print(f"LOAD RESULT {row['id']} wave {wave}: {row['status']} {row['seconds']}s schedule={row.get('schedule_passed')} reference={row.get('reference_checks_passed')}",flush=True)
                    await asyncio.gather(*(one(case) for case in cases))
                with repo.connect() as db:
                    report['saved_trips']=db.execute('SELECT count(*) FROM trips').fetchone()[0]
                    report['saved_versions']=db.execute('SELECT count(*) FROM versions').fetchone()[0]
                save()
    finally:
        await jobs.shutdown_runs();await routes.close_route_queries();await amap.close_client()
    print('report:',path,flush=True)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--repeat',type=int,default=2);parser.add_argument('--cases',default='');parser.add_argument('--case-file');parser.add_argument('--capture-schedules',action='store_true');args=parser.parse_args()
    asyncio.run(run(args.repeat,[v for v in args.cases.split(',') if v] or None,args.case_file,args.capture_schedules))


if __name__=='__main__':main()
