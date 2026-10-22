"""Replay public generated prose without model/map calls or user data changes."""
import json
import hashlib
import time
from datetime import datetime
from pathlib import Path
from app.models.schemas import TripPlan
from app.services.visit_advice import normalize_generated_advice

ROOT=Path(__file__).resolve().parents[1]


def run():
    source=ROOT/'eval/results/load-20261018-061309.json'
    original=source.read_bytes();data=json.loads(original)
    rows=[];comparisons=[];started=time.perf_counter()
    for row in data['rows']:
        before=TripPlan.model_validate(row['plan_snapshot']);after=before.model_copy(deep=True)
        changes=normalize_generated_advice(after)
        exclude={'days':{'__all__':{'attractions':{'__all__':{'description'}}}}}
        unchanged=before.model_dump(exclude=exclude)==after.model_dump(exclude=exclude)
        assert unchanged
        comparisons.append({'source_case_id':row['id'],**changes,'structured_facts_unchanged':unchanged,
            'visits':[{'name':a.name,'before':a.description,'after':b.description}
                for da,db in zip(before.days,after.days) for a,b in zip(da.attractions,db.attractions)]})
        for kind,plan in (('before',before),('after',after)):
            rows.append({'id':row['id']+'-'+kind,'source_case_id':row['id'],'case_request':row['case_request'],
                         'fixture_scope':'public frozen candidate prose replay, not new planning', 'plan_snapshot':plan.model_dump(mode='json')})
    preserved=source.read_bytes()==original;assert preserved
    report={'scope':'public frozen generated advice replay; no model/map calls; not semantic fact verification or new planning latency',
        'source':source.name,'source_sha256':hashlib.sha256(original).hexdigest(),'source_preserved':preserved,
        'seconds':round(time.perf_counter()-started,6),'model_calls':0,'map_calls':0,
        'comparisons':comparisons,'rows':rows}
    path=ROOT/'eval/results'/f'visit-advice-{datetime.now():%Y%m%d-%H%M%S}.json'
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('report:',path)
    print(json.dumps({k:report[k] for k in ('seconds','model_calls','map_calls','source_preserved')},ensure_ascii=False))
    for comparison in comparisons:print(json.dumps(comparison,ensure_ascii=False))
    return path


if __name__=='__main__':run()
