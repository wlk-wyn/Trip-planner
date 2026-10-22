import unittest
import json
from unittest.mock import AsyncMock,patch
from langchain_core.messages import AIMessage
from app.models.schemas import TripPlan
from app.services.daily_limits import daily_attraction_limit,daily_limit_issues
from app.services.initial_planning import initial_repair_days,planning_advice,check_initial_repair
from app.services.plan_quality import evaluate_plan
from app.services.plan_patch import PlanPatch,apply_patch
from app.services.schedule_validator import validate_schedule
from app.agents.travel_agent import TravelAgent,MAX_REPAIRS
from test_travel_agent import REQUEST
from test_schedule import sample
from test_travel_agent import make_agent,FakeModel


class DailyLimitTests(unittest.IsolatedAsyncioTestCase):
    def request(self,text):return REQUEST.model_copy(update={'free_text_input':text})

    def test_explicit_daily_maxima_and_latest_correction(self):
        for text,expected in [('每天最多4个景点',4),('每日安排不超过三个景点',3),('单日最多游览两处景点',2),('一天至多十个地点',10),('每天最多3个景点，改为每天最多4个景点',4),('每天最多0个景点，只想吃饭',0)]:
            with self.subTest(text=text):self.assertEqual(daily_attraction_limit(self.request(text)),expected)

    def test_vague_subset_total_and_specific_day_counts_are_not_general_caps(self):
        for text in ['节奏轻松一点','三天最多4个景点','每天最多3个博物馆','第一天最多2个景点','第十一天最多2个景点','每天最多3个景点左右','不用限制每天最多3个景点','每天最多三十一个景点']:
            with self.subTest(text=text):self.assertIsNone(daily_attraction_limit(self.request(text)))
        request=self.request('自由安排').model_copy(update={'reference_content':'每天最多1个景点'})
        self.assertIsNone(daily_attraction_limit(request))

    async def test_feasible_time_cannot_hide_an_explicit_count_violation(self):
        plan=sample(3);request=self.request('每天最多2个景点')
        await validate_schedule(plan,request,AsyncMock(return_value={'duration_minutes':10,'distance_meters':500}))
        self.assertEqual(plan.days[0].schedule.status,'feasible')
        before=plan.model_dump()
        issues=daily_limit_issues(plan,request)
        self.assertEqual(issues[0]['severity'],'error')
        self.assertEqual(initial_repair_days(plan,request),{0})
        self.assertFalse(evaluate_plan(plan,request)['schedule_passed'])
        self.assertEqual(plan.model_dump(),before)

    async def test_bounded_repair_keeps_a_flagged_draft_instead_of_claiming_success(self):
        plan=sample(3);request=self.request('每天最多2个景点')
        before=plan.model_dump()
        result=TravelAgent._settle_conflicts({},plan,plan,'全部满足',request)
        self.assertEqual(result['status'],'retry')
        self.assertIn('最多 2 个',result['messages'][0].content)
        final=TravelAgent._settle_conflicts({'repairs':MAX_REPAIRS},plan,plan,'全部满足',request)
        self.assertEqual(final['status'],'done')
        self.assertIn('景点数量要求尚未满足',final['reply'])
        self.assertNotIn('全部满足',final['reply'])
        self.assertEqual(plan.model_dump(),before)
        self.assertEqual(planning_advice(request)['explicit_max_attractions_per_day'],2)

    async def test_count_repair_does_not_allow_removing_locked_visits(self):
        plan=sample(3);request=self.request('每天最多2个景点')
        plan.days[0].identify_visits();place=plan.days[0].attractions[0];place.locked=True
        patch=PlanPatch(operations=[{'action':'remove','date':plan.days[0].date,'kind':'attraction','item_id':place.item_id}])
        check_initial_repair(patch,plan,request)
        with self.assertRaises(ValueError):apply_patch(plan,patch,{})

    async def test_full_agent_loop_repairs_count_even_when_time_was_already_feasible(self):
        request=self.request('每天最多2个景点')
        pois=[{'poi_id':f'poi-{i}','name':f'测试景点{i}','address':'测试地址','coordinates':[120.15+i*.001,30.24]} for i in range(3)]
        raw={'days':[{'date':request.start_date,'attractions':[{'poi_id':p['poi_id'],'item_id':f'visit-{i}','visit_duration':90,'description':'测试'} for i,p in enumerate(pois)]}]}
        model=FakeModel([
            AIMessage(content='',tool_calls=[{'name':'search_attractions','args':{'keyword':'测试'},'id':'search-count'}]),
            AIMessage(content=json.dumps({'changed':True,'reply':'全部满足','plan':raw})),
            AIMessage(content=json.dumps({'changed':True,'reply':'保留两处','patch':{'operations':[{'action':'remove','date':request.start_date,'kind':'attraction','item_id':'visit-2'}]}}))])
        agent=make_agent(model)
        async def checked(plan,request,progress=None):
            return await validate_schedule(plan,request,AsyncMock(return_value={'duration_minutes':10,'distance_meters':500}))
        agent._check_schedule=checked
        with patch('app.agents.travel_agent.search_pois',new=AsyncMock(return_value=pois)):
            plan,reply,_,_=await agent.run(request)
        self.assertEqual(len(plan.days[0].attractions),2)
        self.assertEqual(model.calls,3)
        self.assertEqual([p.visit_duration for p in plan.days[0].attractions],[90,90])
        self.assertEqual(daily_limit_issues(plan,request),[])
