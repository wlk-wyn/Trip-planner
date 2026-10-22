"""Real configured intake model, public synthetic messages, no trip/job writes.

Single samples measure extraction behavior only, not booking accuracy or a
production success rate. Expectations stay beside each original conversation.
"""
import asyncio
import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from app.agents.trip_intake import interpret_trip_intake
from app.models.schemas import ChatTurn


async def run():
    departure = (date.today() + timedelta(days=21)).isoformat()
    initial = f'西安{departure}出发玩3天，学生党，预算1500元'
    question = '请确认同行总人数、每晚房间数、预算是每人还是全体，以及不含往返大交通的目的地内预算。也可先规划，预算稍后再定。'
    clarified = '2人，每晚1间，目的地内全体总预算1500元，不含往返大交通'
    old_form = '\n已填写条件（本条新描述中的更改优先）：同行总人数：2人；每晚房间数：1间；目的地内全体总预算1500元，不含往返大交通'
    cases = [
        ('ambiguous', [('user', initial)], dict(ready=False, budget_clarification=True, party_size=None, room_count=None, destination_budget=None)),
        ('clarified', [('user', initial), ('assistant', question), ('user', clarified)], dict(ready=True, party_size=2, room_count=1, destination_budget=1500)),
        ('per_person', [('user', f'西安{departure}出发玩3天，2人1间，目的地内每人预算1500元，不含往返')], dict(ready=True, party_size=2, room_count=1, destination_budget=3000)),
        ('whole_trip', [('user', f'西安{departure}出发玩3天，2人1间，全体总预算1500元，包含往返机票')], dict(ready=False, budget_clarification=True, destination_budget=None)),
        ('deferred', [('user', initial), ('assistant', question), ('user', '先规划行程，预算稍后再定。'+old_form)], dict(ready=True, budget_mode='deferred', destination_budget=None)),
        ('corrected_counts', [('user', initial+'。'+clarified), ('assistant', '已了解。'), ('user', '改成3人2间，目的地内每人预算1000元，不含往返。'+old_form)], dict(ready=True, party_size=3, room_count=2, destination_budget=3000)),
        ('no_budget', [('user', f'西安{departure}出发玩3天，喜欢博物馆')], dict(ready=True, budget_clarification=False, party_size=None, room_count=None, destination_budget=None)),
        ('one_day', [('user', f'西安{departure}玩1天，我1人，目的地内预算300元，不含往返')], dict(ready=True, party_size=1, room_count=None, destination_budget=300)),
        ('partial_meal', [('user', f'西安{departure}出发玩3天，每餐预算100元')], dict(ready=True, budget_clarification=False, destination_budget=None)),
    ]
    results = []
    for identity, messages, expected in cases:
        days = 1 if identity=='one_day' else 3
        expected = {**expected, 'city': '西安', 'start_date': departure,
                    'end_date': (date.fromisoformat(departure)+timedelta(days=days-1)).isoformat(), 'travel_days': days}
        started = time.monotonic()
        row = dict(id=identity, messages=[dict(role=r, content=c) for r,c in messages], expected=expected)
        try:
            result = await asyncio.wait_for(interpret_trip_intake([ChatTurn(**m) for m in row['messages']]), timeout=40)
            actual = result.model_dump(mode='json')
            row.update(actual=actual, mismatches={key: dict(expected=value, actual=actual.get(key)) for key,value in expected.items() if actual.get(key)!=value})
            row['passed'] = not row['mismatches']
        except Exception as exc:
            row.update(passed=False, error=type(exc).__name__)
        row['seconds'] = round(time.monotonic()-started, 2)
        results.append(row)
        print(json.dumps({k:row[k] for k in ('id','passed','seconds')} | {'mismatches':row.get('mismatches'), 'error':row.get('error')},ensure_ascii=False),flush=True)
    output = Path('eval/results') / f'budget-intake-{datetime.now():%Y%m%d-%H%M%S}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'REPORT {output} {sum(r["passed"] for r in results)}/{len(results)}',flush=True)


if __name__ == '__main__':
    asyncio.run(run())
