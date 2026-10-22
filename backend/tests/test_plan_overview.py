import json
import unittest
from unittest.mock import AsyncMock,patch
from langchain_core.messages import AIMessage
from app.services.plan_overview import plan_overview
from test_schedule import sample
from test_travel_agent import REQUEST,PLAN,POI,FakeModel,make_agent


class PlanOverviewTests(unittest.IsolatedAsyncioTestCase):
    def test_current_outline_ignores_stale_model_claims_and_keeps_alternatives_labelled(self):
        plan=sample(1);plan.overall_suggestions='去已移除的国博，预约成功，保证有余房。'
        plan.adjustment_notes=['国博已移出当天路线，保留为备选。']
        before=plan.model_dump();text=plan_overview(plan)
        self.assertIn(plan.days[0].attractions[0].name,text.splitlines()[0])
        self.assertNotIn('国博',text.splitlines()[0]);self.assertNotIn('预约成功',text);self.assertNotIn('保证有余房',text)
        self.assertIn('备选',text);self.assertEqual(plan.model_dump(),before)

    async def test_new_generation_returns_an_outline_of_its_final_places(self):
        raw=json.loads(json.dumps(PLAN));raw['overall_suggestions']='去虚构的第二景点，已经预约成功。'
        raw['days'][0]['description']='下午去已经移除的第二景点'
        model=FakeModel([AIMessage(content='',tool_calls=[{'name':'search_attractions','args':{'keyword':'博物馆'},'id':'s'}]),
            AIMessage(content=json.dumps({'changed':True,'reply':'已规划','plan':raw},ensure_ascii=False))])
        agent=make_agent(model)
        with patch('app.agents.travel_agent.search_pois',new=AsyncMock(return_value=[POI])):
            plan,*_=await agent.run(REQUEST)
        self.assertIn(POI['name'],plan.overall_suggestions)
        self.assertNotIn('虚构',plan.overall_suggestions);self.assertNotIn('预约成功',plan.overall_suggestions)
        self.assertEqual(plan.days[0].description,POI['name'])

    async def test_question_does_not_rewrite_user_notes_or_create_a_revision(self):
        plan=sample(1);plan.overall_suggestions='用户自己的备注'
        before=plan.model_dump();agent=make_agent(FakeModel([AIMessage(content='{"changed":false,"reply":"可以","plan":null}')]))
        result,reply,changed,*_=await agent.run(REQUEST,current_plan=plan,user_message='可以看看第一天吗')
        self.assertFalse(changed);self.assertEqual(result.model_dump(),before)
