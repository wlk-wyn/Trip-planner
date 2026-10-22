import unittest
import json
from unittest.mock import AsyncMock
from langchain_core.messages import AIMessage
from app.agents.travel_agent import TravelAgent
from app.services.current_itinerary import saved_places_answer
from app.services.schedule_validator import validate_schedule
from app.services.observability import usage_scope
from test_schedule import sample
from test_travel_agent import REQUEST,FakeModel,make_agent


class SavedPlacesTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_attraction_clocks_are_not_the_hotel_departure_window(self):
        from app.models.schemas import Hotel
        plan=sample(1);day=plan.days[0]
        day.hotel=Hotel(name='合成酒店',location=day.attractions[0].location)
        await validate_schedule(plan,REQUEST,AsyncMock(return_value={'duration_minutes':30,'distance_meters':1000}))
        item=next(i for i in day.schedule.items if i.kind=='attraction')
        text=saved_places_answer(plan,'当前每天实际安排了哪些景点？')
        self.assertIn(item.start_time,text);self.assertNotEqual(item.start_time,'09:00')
        self.assertNotIn('09:00入园',text);self.assertIn('已保存计划',text)

    def test_unknown_and_stale_schedules_do_not_get_invented_clock_times(self):
        plan=sample(1);text=saved_places_answer(plan,'当前行程有哪些景点？')
        self.assertIn('时间待校验',text);self.assertNotIn('09:00',text)

    async def test_modified_place_invalidates_old_saved_clock_in_the_answer(self):
        plan=sample(1)
        await validate_schedule(plan,REQUEST,AsyncMock(return_value={'duration_minutes':10,'distance_meters':400}))
        plan.days[0].attractions[0].name='用户新地点'
        text=saved_places_answer(plan,'现在行程有哪些景点？')
        self.assertIn('用户新地点',text);self.assertIn('时间待校验',text)

    async def test_malformed_conflict_clocks_are_not_echoed_as_arrival_times(self):
        plan=sample(1)
        await validate_schedule(plan,REQUEST,AsyncMock(return_value={'duration_minutes':10,'distance_meters':400}))
        plan.days[0].schedule.status='conflict'
        for start,end in (('99:99','10:30'),('10:00','09:00')):
            plan.days[0].schedule.items[0].start_time=start;plan.days[0].schedule.items[0].end_time=end
            text=saved_places_answer(plan,'当前行程有哪些景点？')
            self.assertIn('时间待校验',text);self.assertNotIn(f'{start}—{end}',text)

    def test_qualifiers_recommendations_changes_and_booking_questions_stay_with_agent(self):
        plan=sample(1)
        for text in ('推荐第二天有哪些景点','帮我增加一个景点，当前有哪些景点？','明天的行程有哪些景点',
                     '第二天下午有哪些景点','当前有哪些免费景点','哪些景点需要预约','北京有哪些景点',
                     '行程哪些景点不在市区','当前有哪些室内景点','我行程有哪些景点，门票多少钱'):
            self.assertIsNone(saved_places_answer(plan,text),text)

    def test_day_selection_is_explicit_and_out_of_range_is_not_fabricated(self):
        plan=sample(1);second=plan.days[0].model_copy(deep=True)
        second.day_index=1;second.date='2026-10-04';second.attractions[0].name='第二天的地点';plan.days.append(second)
        for text in ('第二天有哪些景点','Day 2 有哪些景点','D2 有哪些景点'):
            answer=saved_places_answer(plan,text)
            self.assertIn('第二天的地点',answer);self.assertNotIn('西湖博物馆',answer)
        self.assertIn('共2天',saved_places_answer(plan,'第3天有哪些景点'))

    async def test_saved_lookup_does_not_initialize_tools_call_model_or_mutate_the_plan(self):
        plan=sample(1);plan.overall_suggestions='旧文提到已移除的国博'
        before=plan.model_dump();agent=TravelAgent.__new__(TravelAgent)
        agent._tools=AsyncMock(side_effect=AssertionError('no tools for saved lookup'))
        with usage_scope() as usage:
            result,answer,changed,actions=await agent.run(REQUEST,current_plan=plan,
                user_message='现在行程里还有国博吗？每天实际安排了哪些景点？')
        self.assertEqual(result.model_dump(),before);self.assertFalse(changed);self.assertEqual(actions,[])
        self.assertNotIn('国博',answer);self.assertEqual(usage['calls'],0)
        self.assertEqual(usage['events']['saved_itinerary_fast_answers'],1);agent._tools.assert_not_called()

    async def test_other_questions_give_the_model_real_timeline_and_conflicts(self):
        plan=sample(2);plan.days[0].end_time='10:00'
        await validate_schedule(plan,REQUEST,AsyncMock(return_value={'duration_minutes':10,'distance_meters':400}))
        outer=self
        class InspectModel(FakeModel):
            async def ainvoke(self,messages):
                content=next(m.content for m in messages if '当前行程及访问标识：' in str(m.content))
                context=json.loads(content.split('当前行程及访问标识：',1)[1].split('\n用户最新要求：',1)[0])
                day=context['days'][0]
                outer.assertEqual(day['time_status'],'conflict')
                outer.assertEqual(day['time_conflicts'],plan.days[0].schedule.conflicts)
                outer.assertEqual(day['timeline_reference'][0]['start_time'],plan.days[0].schedule.items[0].start_time)
                return await super().ainvoke(messages)
        agent=make_agent(InspectModel([AIMessage(content='{"changed":false,"reply":"当前有时间冲突","plan":null}')]))
        result,_,changed,*_=await agent.run(REQUEST,current_plan=plan,user_message='当前行程是否太赶？')
        self.assertFalse(changed);self.assertEqual(result,plan)
