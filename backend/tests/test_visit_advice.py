import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from app.services.visit_advice import clean_generated_advice,normalize_generated_advice,FALLBACK
from app.services.observability import usage_scope
from app.services.plan_checks import run_checks
from app.services import planning_runs as jobs
from app.services.trip_repository import TripRepository
from test_schedule import sample
from test_travel_agent import REQUEST,PLAN,POI,FakeModel,make_agent


class VisitAdviceTests(unittest.IsolatedAsyncioTestCase):
    def test_actual_style_advice_keeps_focus_without_made_up_times_prices_or_policy(self):
        text='上午按预约时段入馆，重点看感兴趣的展区，免费但需实名预约。'
        self.assertEqual(clean_generated_advice(text),('重点看感兴趣的展区。',2))
        text='下午乘地铁前往，沿昆明湖东岸与长廊慢走，10月开放至20:00、19:00停止入园。'
        self.assertEqual(clean_generated_advice(text),('沿昆明湖东岸与长廊慢走。',2))

    def test_known_operational_claim_forms_cannot_survive_as_recommendations(self):
        for text in ('无需预约，就能随时进馆。','门票五十元，全天开放。','步行3.5公里约四十分钟。',
                     '已确认免费，保证有票。','Open until 8pm; free admission.',
                     '票价￥60，坐地铁4号线直达。','九点入园，每日开放。'):
            with self.subTest(text=text):self.assertEqual(clean_generated_advice(text)[0],FALLBACK)

    def test_nonoperational_advice_is_unchanged_and_cleaning_is_idempotent(self):
        text='建议放慢脚步，把时间留给感兴趣的区域。'
        self.assertEqual(clean_generated_advice(text),(text,0))
        result,_=clean_generated_advice('上午前往，建议放慢脚步。')
        self.assertEqual(clean_generated_advice(result),(result,0))

    def test_temporal_lead_in_does_not_erase_focus_or_leave_weather_promises(self):
        text='上午在涌金公园沿湖慢走，地势平缓、全天开放，雨天也方便随时避雨'
        self.assertEqual(clean_generated_advice(text)[0],'在涌金公园沿湖慢走。')
        text='下午从苏堤南入口公园沿堤慢走，视野开阔，多云天气体感舒适'
        self.assertEqual(clean_generated_advice(text)[0],'从苏堤南入口公园沿堤慢走；视野开阔。')
        self.assertEqual(clean_generated_advice('午后游览感兴趣的区域。')[0],'游览感兴趣的区域。')
        self.assertEqual(clean_generated_advice('主体景区一次逛完，不拆分园内点位。')[0],'不拆分园内点位。')
        self.assertEqual(clean_generated_advice('建议在附近喝下午茶。'),('建议在附近喝下午茶。',0))

    def test_existing_notes_and_verbatim_requested_note_edits_are_preserved(self):
        previous=sample(1);previous.days[0].attractions[0].description='用户备注：门票已购买，预约09:00。'
        proposed=previous.model_copy(deep=True);proposed.days[0].attractions[0].visit_duration+=10
        self.assertEqual(normalize_generated_advice(proposed,previous)['fields_rewritten'],0)
        text='我已经买票，请在备注中写为：用户记录：已预约09:30。'
        proposed.days[0].attractions[0].description='用户记录：已预约09:30。'
        normalize_generated_advice(proposed,previous,user_text=text)
        self.assertIn('09:30',proposed.days[0].attractions[0].description)
        # A model's extra, unquoted assertion is not authorized by that note edit.
        proposed.days[0].attractions[0].description='用户记录：已预约09:30。保证有票。'
        normalize_generated_advice(proposed,previous,user_text=text)
        self.assertEqual(proposed.days[0].attractions[0].description,FALLBACK)

    def test_new_model_rewrite_is_filtered_without_modifying_sources_or_plan_facts(self):
        previous=sample(1);before=previous.model_dump()
        proposed=previous.model_copy(deep=True);proposed.days[0].attractions[0].description='上午入馆，挑选喜欢的展区。'
        facts=proposed.model_dump(exclude={'days':{'__all__':{'attractions':{'__all__':{'description'}}}}})
        with usage_scope() as usage:result=normalize_generated_advice(proposed,previous)
        self.assertEqual(result,{'fields_rewritten':1,'claim_clauses_removed':1})
        self.assertEqual(usage['calls'],0)
        self.assertEqual(usage['events']['generated_advice_claim_clauses_removed'],1)
        self.assertEqual(proposed.model_dump(exclude={'days':{'__all__':{'attractions':{'__all__':{'description'}}}}}),facts)
        self.assertEqual(previous.model_dump(),before)

    def test_read_checks_flag_existing_claims_without_rewriting_a_user_note(self):
        plan=sample(1);place=plan.days[0].attractions[0];place.description='用户记录：预约09:00。'
        before=plan.model_dump()
        issues=run_checks(plan,REQUEST)
        issue=next(i for i in issues if i['code']=='visit_advice_claim')
        self.assertEqual(issue['item_id'],place.item_id)
        self.assertEqual(issue['day_index'],0);self.assertEqual(issue['severity'],'info')
        self.assertIn('未凭此文字认定已核实',issue['message'])
        self.assertEqual(plan.model_dump(),before)

    async def test_agent_normalizes_new_generated_description_without_an_extra_model_call(self):
        raw=json.loads(json.dumps(PLAN));raw['days'][0]['attractions'][0]['description']='上午09:00入馆，选择感兴趣的展区，免费免预约。'
        model=FakeModel([AIMessage(content='',tool_calls=[{'name':'search_attractions','args':{'keyword':'博物馆'},'id':'s'}]),
            AIMessage(content=json.dumps({'changed':True,'reply':'已规划','plan':raw},ensure_ascii=False))])
        with patch('app.agents.travel_agent.search_pois',AsyncMock(return_value=[POI])):
            plan,*_=await make_agent(model).run(REQUEST)
        self.assertEqual(plan.days[0].attractions[0].description,'选择感兴趣的展区。')
        self.assertEqual(model.calls,2)

    async def test_persistent_creation_covers_a_generator_that_bypasses_the_agent(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=TripRepository(Path(folder)/'trips.sqlite3');owner=str(uuid4());run_id=str(uuid4())
            plan=sample(1);plan.days[0].attractions[0].description='保证免费免预约，重点看感兴趣的展区。'
            jobs.start_create_run(repo,owner,REQUEST.model_dump(),run_id,schedule=False)
            with patch.object(jobs,'_generate',AsyncMock(return_value=plan)),patch.object(jobs,'_estimate_tickets',AsyncMock()):
                await jobs.execute(repo,owner,run_id)
            run=jobs.read_run(repo,owner,run_id)
            self.assertEqual(run['status'],'succeeded',run)
            saved=repo.list(owner)
            self.assertEqual(len(saved),1)
            actual=repo.read(owner,saved[0]['id'])['plan']['days'][0]['attractions'][0]['description']
            self.assertEqual(actual,'重点看感兴趣的展区。')
