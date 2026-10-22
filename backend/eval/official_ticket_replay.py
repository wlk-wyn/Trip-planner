"""Refresh official adult admission on public snapshots, without trip/job writes.

This is a price-only replay. It does not regenerate plans, query routes or prove
booking availability. Original reports remain untouched.
"""
import argparse
import asyncio
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

from app.models.schemas import TripPlan, TripRequest
from app.services.cost_breakdown import build_cost_report
from app.services.fact_evidence import fact_report
from app.services.official_tickets import refresh_official_tickets


async def run(report):
    original = report.read_bytes()
    source_hash = hashlib.sha256(original).hexdigest()
    rows = json.loads(original)
    results = []
    for identity in ('bj-history-2d', 'xa-budget-3d'):
        row = next(r for r in rows if r['id'] == identity)
        plan, request = TripPlan(**row['plan_snapshot']), TripRequest(**row['case_request'])
        before = build_cost_report(plan, request)
        started = time.monotonic()
        summary = await refresh_official_tickets(plan)
        result = dict(id=identity, source=str(report), source_sha256=source_hash,
            seconds=round(time.monotonic()-started, 3), summary=summary, before=before,
            cost_report=build_cost_report(plan, request), fact_report=fact_report(plan),
            request=request.model_dump(mode='json'), plan=plan.model_dump(mode='json'))
        results.append(result)
        print(json.dumps(dict(id=identity,seconds=result['seconds'],summary=summary,
            official_prices=[{'name':line['name'],'unit_price':line['unit_price'],'evidence':line['evidence']}
                for line in result['cost_report']['lines'] if line.get('evidence',{}).get('source')=='official']),ensure_ascii=False),flush=True)
    assert original == report.read_bytes()
    output=Path('eval/results')/f'official-ticket-{datetime.now():%Y%m%d-%H%M%S}.json'
    output.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'REPORT {output}',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    asyncio.run(run(parser.parse_args().report))
