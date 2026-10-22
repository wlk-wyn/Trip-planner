import json
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage
from app.agents.travel_agent import TravelAgent
from app.models.schemas import PlanPatch, ReferenceCandidate, ReferencePlace
from app.services.initial_planning import initial_repair_days, restore_initial_timing, check_initial_repair
from app.services.evening_planning import evening_coverage, prepare_evening
from app.services.schedule_validator import validate_schedule
from test_schedule import sample
from test_travel_agent import REQUEST, FakeModel, make_agent


class TimingLoopTests(unittest.IsolatedAsyncioTestCase):
    def request(self):
        return REQUEST.model_copy(update={'free_text_input':'想看夜景'})

    def draft(self):
        plan=sample(2)
        day=plan.days[0]
        for i,p in enumerate(day.attractions): p.poi_id=f'poi-{i}'
        night=day.attractions[0]
        night.name,night.category,night.description,night.visit_duration='江畔观景台','风景名胜','看城市夜景',150
        day.identify_visits()
        return plan

    async def check(self,plan,request,progress=None):
        await validate_schedule(plan,request,AsyncMock(return_value={'duration_minutes':10,'distance_meters':500}))

    async def test_patch_round_keeps_night_constraint_in_actual_time(self):
        draft=self.draft(); night=draft.days[0].attractions[0]
        pois=[dict(poi_id=p.poi_id,name=p.name,address=p.address,coordinates=[p.location.longitude,p.location.latitude]) for p in draft.days[0].attractions]
        model=FakeModel([
            AIMessage(content='',tool_calls=[{'name':'search_attractions','args':{'keyword':'观景台'},'id':'search'}]),
            AIMessage(content=json.dumps(dict(changed=True,reply='初稿',plan=draft.model_dump()),ensure_ascii=False)),
            AIMessage(content=json.dumps(dict(changed=True,reply='缩短建议夜游',patch=dict(operations=[dict(action='update_visit',kind='attraction',date=draft.days[0].date,item_id=night.item_id,visit_duration=90)])),ensure_ascii=False))])
        agent=make_agent(model); agent._check_schedule=self.check
        with patch('app.agents.travel_agent.search_pois',new=AsyncMock(return_value=pois)):
            fixed,_,_,_=await agent.run(self.request())
        self.assertEqual(model.calls,3)
        self.assertTrue(evening_coverage(fixed,self.request()))
        item=next(i for i in fixed.days[0].schedule.items if i.id==night.item_id)
        self.assertEqual((item.start_time,item.end_time),('18:00','19:30'))
        self.assertEqual(fixed.days[0].schedule.status,'feasible')

    async def test_preference_only_failure_triggers_retry_even_if_day_fits(self):
        plan=self.draft()
        await self.check(plan,self.request())
        self.assertEqual(plan.days[0].schedule.status,'feasible')
        result=TravelAgent._settle_conflicts({},plan,plan,'全部完成',self.request())
        self.assertEqual(result['status'],'retry')
        self.assertIn('夜景',result['messages'][0].content)
        self.assertEqual(initial_repair_days(plan,self.request()),{0})
        patch_=PlanPatch(operations=[dict(action='update_visit',kind='attraction',date=plan.days[0].date,item_id=plan.days[0].attractions[0].item_id,visit_duration=90)])
        check_initial_repair(patch_,plan,self.request())
        with self.assertRaises(ValueError): check_initial_repair(patch_,plan)

    async def test_user_stated_duration_cannot_be_shortened_by_model_patch(self):
        plan=self.draft()
        plan=prepare_evening(plan,self.request(),with_meals=True)
        await self.check(plan,self.request())
        night=next(p for p in plan.days[0].attractions if p.name=='江畔观景台')
        patch_=PlanPatch(operations=[dict(action='update_visit',kind='attraction',date=plan.days[0].date,item_id=night.item_id,visit_duration=60)])
        request=self.request().model_copy(update={'free_text_input':'想看夜景，每处参观两小时'})
        with self.assertRaises(ValueError): check_initial_repair(patch_,plan,request)

    async def test_retained_clock_does_not_transfer_to_a_replaced_place(self):
        old=self.draft()
        old.days[0].planned_start_times={old.days[0].attractions[0].item_id:'18:30',old.days[0].attractions[1].item_id:'12:00','removed':'10:00'}
        partial=old.model_copy(deep=True)
        partial.days[0].planned_start_times={}
        partial.days[0].attractions[0].name='替换的公园'
        partial.days[0].attractions[0].poi_id='different'
        partial.days[0].attractions[0].description='散步'
        restored=restore_initial_timing(partial,old,REQUEST)
        self.assertEqual(restored.days[0].planned_start_times,{old.days[0].attractions[1].item_id:'12:00'})
        self.assertEqual(partial.days[0].planned_start_times,{})

    async def test_later_planned_night_start_is_not_reset_to_eighteen(self):
        plan=self.draft()
        night=plan.days[0].attractions[0]
        plan.days[0].planned_start_times[night.item_id]='18:30'
        restored=prepare_evening(plan,self.request(),with_meals=True,reapply=True)
        self.assertEqual(restored.days[0].planned_start_times[night.item_id],'18:30')

    async def test_existing_trip_question_bypasses_new_draft_timing_restoration(self):
        model=FakeModel([AIMessage(content=json.dumps(dict(changed=False,reply='现有建议',plan=None)))])
        with patch('app.agents.travel_agent.restore_initial_timing',side_effect=AssertionError('user revision')):
            _,_,changed,_=await make_agent(model).run(self.request(),current_plan=self.draft(),user_message='有什么建议')
        self.assertFalse(changed)

    async def test_required_poi_alias_cannot_bypass_duration_protection(self):
        plan=self.draft()
        await self.check(plan,self.request())
        night=plan.days[0].attractions[0]
        request=self.request().model_copy(update={'reference_candidates':[ReferenceCandidate(id='required',name='别名',priority='required',status='matched',
            place=ReferencePlace(poi_id=night.poi_id,name='地点别名',longitude=night.location.longitude,latitude=night.location.latitude))]})
        patch_=PlanPatch(operations=[dict(action='update_visit',kind='attraction',date=plan.days[0].date,item_id=night.item_id,visit_duration=60)])
        with self.assertRaises(ValueError): check_initial_repair(patch_,plan,request)

    async def test_initial_patch_preserves_unaffected_day_exactly(self):
        draft=self.draft(); night=draft.days[0].attractions[0]
        second=sample(1).days[0]
        second.date,second.day_index='2026-10-04',1
        second.attractions[0].item_id='day-two-visit'
        second.attractions[0].poi_id='day-two-poi'
        draft.days.append(second)
        draft.end_date='2026-10-04'
        pois=[dict(poi_id=p.poi_id,name=p.name,address=p.address,coordinates=[p.location.longitude,p.location.latitude]) for d in draft.days for p in d.attractions]
        model=FakeModel([
            AIMessage(content='',tool_calls=[{'name':'search_attractions','args':{'keyword':'景点'},'id':'search'}]),
            AIMessage(content=json.dumps(dict(changed=True,reply='初稿',plan=draft.model_dump()),ensure_ascii=False)),
            AIMessage(content=json.dumps(dict(changed=True,reply='修复第一天',patch=dict(operations=[dict(action='update_visit',kind='attraction',date=draft.days[0].date,item_id=night.item_id,visit_duration=90)])),ensure_ascii=False))])
        checked_second=[]
        async def checker(plan,request,progress=None):
            await self.check(plan,request)
            if len(plan.days)==2: checked_second.append(plan.days[1].model_dump())
        agent=make_agent(model);agent._check_schedule=checker
        request=self.request().model_copy(update={'travel_days':2,'end_date':'2026-10-04'})
        with patch('app.agents.travel_agent.search_pois',new=AsyncMock(return_value=pois)):
            fixed,*_=await agent.run(request)
        self.assertEqual(model.calls,3)
        self.assertTrue(checked_second)
        self.assertEqual(fixed.days[1].model_dump(),checked_second[0])

    async def test_multiple_night_candidates_do_not_accumulate_eighteen_o_clock_bounds(self):
        old=self.draft()
        other=old.days[0].attractions[1]
        other.name,other.description,other.category='第二观景台','可看夜景','风景'
        anchor=old.days[0].attractions[0].item_id
        old.days[0].planned_start_times[anchor]='18:00'
        original=old.model_dump()
        patched=old.model_copy(deep=True)
        patched.days[0].planned_start_times={}
        patched.days[0].attractions.reverse()
        before=restore_initial_timing(patched,old,self.request(),with_meals=False)
        self.assertEqual(before.days[0].attractions[-1].item_id,anchor)
        after=restore_initial_timing(before,old,self.request())
        self.assertEqual({k for k,v in after.days[0].planned_start_times.items() if v>='18:00'},{anchor})
        self.assertEqual(after.days[0].attractions[-1].item_id,anchor)
        self.assertEqual(old.model_dump(),original)

    async def test_exhausted_preference_repairs_do_not_repeat_a_model_success_claim(self):
        plan=self.draft()
        await self.check(plan,self.request())
        result=TravelAgent._settle_conflicts({'repairs':2},plan,plan,'全部完成并满足夜游',self.request())
        self.assertEqual(result['status'],'done')
        self.assertIn('夜游时段需求尚未落实',result['reply'])
        self.assertNotIn('全部完成',result['reply'])
        self.assertNotIn('时间冲突',result['reply'])
