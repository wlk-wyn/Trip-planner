import copy
import json
import unittest
import asyncio
from unittest.mock import AsyncMock
from unittest.mock import patch

from langchain_core.messages import AIMessage
from app.agents.travel_agent import TravelAgent
from app.models.schemas import Meal, ReferenceCandidate
from app.services.draft_density import density_rows,apply_density_review,minimum_suggestion,order_before_review
from app.services.observability import usage_scope
from test_schedule import sample
from test_travel_agent import REQUEST,FakeModel,make_agent


def dense_plan():
    plan=sample(2)
    for i,p in enumerate(plan.days[0].attractions):p.poi_id=f'p{i}';p.visit_duration=240
    day=plan.days[0]
    day.start_time='09:00';day.end_time='20:00';day.buffer_minutes=15
    day.meals=[Meal(type=slot,name=f'{slot}餐厅',restaurant=f'{slot}餐厅',route_matched=True,
        location=day.attractions[0].location) for slot in ('breakfast','lunch','dinner')]
    return plan


def proposal(plan):
    return {'days':[{'date':d.date,'attractions':[{'poi_id':p.poi_id,'visit_duration':minimum_suggestion(p)}
        for p in d.attractions]} for d in plan.days]}


class DraftDensityTests(unittest.IsolatedAsyncioTestCase):
    def test_advice_counts_real_draft_stops_and_keeps_reserve_distinct_from_traffic(self):
        plan=dense_plan();before=plan.model_dump();rows=density_rows(plan,REQUEST)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['suggested_stop_minutes'],630)
        self.assertEqual(rows[0]['planning_traffic_reserve_minutes'],132)
        self.assertEqual(rows[0]['advisory_excess_minutes'],162)
        self.assertEqual(plan.model_dump(),before)

    def test_partial_or_already_checked_drafts_are_not_reviewed(self):
        for mode in ('meals','automatic','poi','count','duplicate','order','clock'):
            plan=dense_plan();d=plan.days[0]
            if mode=='meals':d.meals.pop()
            elif mode=='automatic':d.meals[0].route_matched=False
            elif mode=='poi':d.attractions[0].poi_id=''
            elif mode=='count':d.attractions=[]
            elif mode=='duplicate':d.attractions[1].poi_id=d.attractions[0].poi_id
            elif mode=='order':d.visit_order=[p.item_id for p in d.attractions]
            else:d.planned_start_times[d.attractions[0].item_id]='11:00'
            self.assertFalse(density_rows(plan,REQUEST),mode)

    def test_protected_preferences_do_not_trigger_density_rewriting(self):
        for text in ('博物馆停留4个小时','第一天去博物馆','午饭15点吃','想看夜景','保留全部景点'):
            self.assertFalse(density_rows(dense_plan(),REQUEST.model_copy(update={'free_text_input':text})),text)
        for mode in ('locked','fixed','required','meal'):
            plan=dense_plan();request=REQUEST.model_copy(deep=True)
            if mode=='locked':plan.days[0].attractions[0].locked=True
            elif mode=='fixed':plan.days[0].attractions[0].fixed_start_time='10:00'
            elif mode=='required':request.reference_candidates=[ReferenceCandidate(id='r',name='必去',priority='required')]
            else:plan.days[0].meals[0].locked=True
            self.assertFalse(density_rows(plan,request),mode)
            with self.assertRaises(ValueError):apply_density_review(plan,request,proposal(plan))

    def test_accepted_proposal_retains_places_counts_meals_windows_and_facts(self):
        plan=dense_plan();before=plan.model_dump()
        result=apply_density_review(plan,REQUEST,proposal(plan))
        self.assertEqual(plan.model_dump(),before)
        for a,b in zip(plan.days,result.days):
            self.assertEqual(a.meals,b.meals);self.assertEqual(a.hotel,b.hotel)
            self.assertEqual((a.start_time,a.end_time,a.buffer_minutes),(b.start_time,b.end_time,b.buffer_minutes))
            self.assertEqual([p.poi_id for p in a.attractions],[p.poi_id for p in b.attractions])
            for x,y in zip(a.attractions,b.attractions):
                self.assertEqual(x.model_dump(exclude={'visit_duration'}),y.model_dump(exclude={'visit_duration'}))
                self.assertGreaterEqual(y.visit_duration,x.visit_duration*.75)
        self.assertIsNone(result.days[0].schedule)
        self.assertLess(density_rows(result,REQUEST)[0]['advisory_excess_minutes'],density_rows(plan,REQUEST)[0]['advisory_excess_minutes'])

    def test_removed_added_duplicate_dates_extra_fields_and_illegal_durations_are_rejected(self):
        plan=dense_plan();valid=proposal(plan)
        for mode in ('remove','add','duplicate','date','extra','short','long','bool','unchanged'):
            data=copy.deepcopy(valid);visits=data['days'][0]['attractions']
            if mode=='remove':visits.pop()
            elif mode=='add':visits.append({'poi_id':'invented','visit_duration':90})
            elif mode=='duplicate':visits[1]['poi_id']=visits[0]['poi_id']
            elif mode=='date':data['days'][0]['date']='2030-01-01'
            elif mode=='extra':data['hotel']='invented'
            elif mode=='short':visits[0]['visit_duration']=100
            elif mode=='long':visits[0]['visit_duration']=300
            elif mode=='bool':visits[0]['visit_duration']=True
            else:
                for visit in visits:visit['visit_duration']=240
            with self.subTest(mode=mode),self.assertRaises(ValueError):apply_density_review(plan,REQUEST,data)

    async def test_review_makes_one_model_call_without_route_validation(self):
        plan=dense_plan();before=plan.model_dump()
        agent=TravelAgent.__new__(TravelAgent);agent.llm=AsyncMock()
        agent.llm.ainvoke.return_value=AIMessage(content=json.dumps(proposal(plan)))
        agent.planner=type('Planner',(),{'_align_meals_to_route':AsyncMock()})()
        agent._check_schedule=AsyncMock(side_effect=AssertionError('not before density review'))
        with usage_scope() as usage:result=await agent._review_draft_density(plan,REQUEST)
        self.assertEqual(agent.llm.ainvoke.await_count,1);agent._check_schedule.assert_not_awaited()
        self.assertEqual(usage['events']['draft_density_review_adopted'],1)
        self.assertEqual(plan.model_dump(),before);self.assertNotEqual(result,plan)

    async def test_invalid_model_or_timeout_keeps_the_original_draft(self):
        for mode in ('invalid','timeout','failure'):
            plan=dense_plan();agent=TravelAgent.__new__(TravelAgent);agent.llm=AsyncMock()
            agent.planner=type('Planner',(),{'_align_meals_to_route':AsyncMock()})()
            if mode=='invalid':agent.llm.ainvoke.return_value=AIMessage(content='{"days":[]}')
            else:agent.llm.ainvoke.side_effect=asyncio.TimeoutError() if mode=='timeout' else RuntimeError('synthetic failure')
            before=plan.model_dump();result=await agent._review_draft_density(plan,REQUEST)
            self.assertEqual(result.model_dump(),before);self.assertEqual(agent.llm.ainvoke.await_count,1)

    async def test_review_cancellation_propagates_without_adopting_changes(self):
        plan=dense_plan();before=plan.model_dump();agent=TravelAgent.__new__(TravelAgent)
        agent.llm=AsyncMock();agent.llm.ainvoke.side_effect=asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):await agent._review_draft_density(plan,REQUEST)
        self.assertEqual(plan.model_dump(),before)

    async def test_nondense_plan_does_not_buy_an_extra_model_turn(self):
        plan=dense_plan()
        for p in plan.days[0].attractions:p.visit_duration=90
        agent=TravelAgent.__new__(TravelAgent);agent.llm=AsyncMock()
        self.assertEqual(await agent._review_draft_density(plan,REQUEST),plan)
        agent.llm.ainvoke.assert_not_awaited()

    def test_cross_day_review_keeps_global_places_and_each_day_count(self):
        plan=dense_plan();second=plan.days[0].model_copy(deep=True)
        second.date='2026-10-04';second.day_index=1
        for i,p in enumerate(second.attractions):p.poi_id=f'p{i+2}';p.visit_duration=180;p.item_id=''
        second.identify_visits()
        plan.days.append(second);plan.end_date=second.date
        data={'days':[
            {'date':plan.days[0].date,'attractions':[{'poi_id':'p0','visit_duration':240},{'poi_id':'p2','visit_duration':180}]},
            {'date':second.date,'attractions':[{'poi_id':'p1','visit_duration':240},{'poi_id':'p3','visit_duration':180}]}]}
        result=apply_density_review(plan,REQUEST,data)
        self.assertEqual([p.poi_id for d in result.days for p in d.attractions],['p0','p2','p1','p3'])
        self.assertEqual([len(d.attractions) for d in result.days],[2,2])
        self.assertLess(max(r['advisory_excess_minutes'] for r in density_rows(result,REQUEST)),
                        max(r['advisory_excess_minutes'] for r in density_rows(plan,REQUEST)))

    def test_optimistic_registered_hours_can_trigger_review_without_dense_total(self):
        plan=dense_plan()
        for p in plan.days[0].attractions:p.visit_duration=120
        plan.days[0].attractions[1].open_hours='09:00-12:00'
        rows=density_rows(plan,REQUEST)
        self.assertTrue(rows)
        self.assertLess(rows[0]['advisory_excess_minutes'],0)
        self.assertIn(plan.days[0].attractions[1].name,rows[0]['optimistic_hours_conflicts'])
        self.assertIsNone(plan.days[0].schedule)

    def test_optimistic_bound_honors_opening_waits_and_unavoidable_buffers(self):
        plan=dense_plan()
        for p in plan.days[0].attractions:p.visit_duration=90
        plan.days[0].attractions[0].open_hours='15:00-19:00'
        plan.days[0].attractions[1].open_hours='09:00-17:00'
        rows=density_rows(plan,REQUEST)
        self.assertIn(plan.days[0].attractions[1].name,rows[0]['optimistic_hours_conflicts'])
        self.assertGreater(rows[0]['optimistic_end_overrun_minutes'],0)

    def test_unnecessary_cuts_to_an_uncongested_day_are_restored(self):
        plan=dense_plan()
        for p in plan.days[0].attractions:p.visit_duration=120
        second=dense_plan().days[0];second.date='2026-10-04';second.day_index=1
        for i,p in enumerate(second.attractions):p.poi_id=f'p{i+2}';p.item_id=''
        second.identify_visits();plan.days.append(second);plan.end_date=second.date
        result=apply_density_review(plan,REQUEST,proposal(plan))
        self.assertEqual([p.visit_duration for p in result.days[0].attractions],[120,120])
        self.assertEqual([p.visit_duration for p in result.days[1].attractions],[180,180])

    async def test_calendar_order_is_tried_without_shortening_or_buying_an_extra_model_turn(self):
        plan=dense_plan()
        for p in plan.days[0].attractions:p.visit_duration=120
        plan.days[0].attractions[1].open_hours='09:00-12:00'
        before=plan.model_dump();agent=TravelAgent.__new__(TravelAgent);agent.llm=AsyncMock()
        with usage_scope() as usage:result=await agent._review_draft_density(plan,REQUEST)
        agent.llm.ainvoke.assert_not_awaited()
        self.assertEqual(usage['events']['draft_order_screen_adopted'],1)
        self.assertEqual([p.poi_id for p in result.days[0].attractions],['p1','p0'])
        self.assertEqual([p.visit_duration for p in result.days[0].attractions],[120,120])
        self.assertEqual(result.days[0].meals,plan.days[0].meals)
        self.assertEqual(plan.model_dump(),before);self.assertIsNone(result.days[0].schedule)

    def test_calendar_order_budget_exhaustion_keeps_the_original(self):
        plan=dense_plan()
        with patch('app.services.draft_density.time.monotonic',side_effect=[1,2,2,2,2]):
            result=order_before_review(plan,REQUEST)
        self.assertEqual(result.model_dump(),plan.model_dump())

    async def test_generation_reviews_density_once_before_first_route_check(self):
        original=dense_plan();data=original.model_dump()
        pois=[dict(poi_id=p.poi_id,name=p.name,address=p.address,coordinates=[p.location.longitude,p.location.latitude])
              for p in original.days[0].attractions]
        model=FakeModel([AIMessage(content='',tool_calls=[{'name':'search_attractions','args':{'keyword':'博物馆'},'id':'s'}]),
            AIMessage(content=json.dumps({'changed':True,'reply':'初稿','plan':data},ensure_ascii=False)),
            AIMessage(content=json.dumps(proposal(original)))])
        agent=make_agent(model)
        async def align(plan,**kwargs):
            for day in plan.days:
                if not day.meals:day.meals=[m.model_copy(deep=True) for m in original.days[0].meals]
        agent.planner._align_meals_to_route=align
        async def check(plan,request,progress=None):
            self.assertEqual(model.calls,3)
            self.assertEqual([p.visit_duration for p in plan.days[0].attractions],[180,180])
        agent._check_schedule=check
        with patch('app.agents.travel_agent.search_pois',new=AsyncMock(return_value=pois)):
            result,*_=await agent.run(REQUEST)
        self.assertEqual(model.calls,3);self.assertEqual(len(result.days[0].attractions),2)
