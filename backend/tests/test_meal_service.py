import unittest
from unittest.mock import AsyncMock, patch

from app.agents.langgraph_planner import LangGraphTripPlanner
from app.models.schemas import Meal
from app.services.meal_service import meal_service
from test_schedule import sample
from test_travel_agent import REQUEST


def candidate(name, hours, lon=116.4):
    return dict(name=name,poi_id=name,address='合成地址',coordinates=[lon,39.9],
                category='餐饮服务;中餐厅',open_hours=hours)


class MealServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plan=sample(1);self.day=self.plan.days[0];self.day.date='2026-10-20'
        self.day.attractions[0].location.longitude=116.4
        self.day.attractions[0].location.latitude=39.9
        self.day.meals=[];self.day.hotel=None
        self.day.start_time='09:00';self.day.end_time='20:00'
        self.request=REQUEST.model_copy(update={'free_text_input':'每天正常三餐'})

    def service(self,hours,slot='lunch',request=None):
        return meal_service(candidate('测试餐厅',hours),self.day,slot,request or self.request)

    def test_whole_meal_must_fit_one_registered_window(self):
        self.assertEqual(self.service('11:30-12:30'),'possible')
        self.assertEqual(self.service('11:30-12:00;13:30-14:00'),'excluded')
        self.assertEqual(self.service('11:00-11:45'),'excluded')
        self.assertEqual(self.service('14:01-15:01'),'excluded')
        self.assertEqual(self.service('14:00-15:00'),'possible')

    def test_breakfast_is_thirty_minutes_and_lunch_dinner_are_sixty(self):
        self.assertEqual(self.service('09:00-09:30','breakfast'),'possible')
        self.assertEqual(self.service('09:00-09:29','breakfast'),'excluded')
        self.assertEqual(self.service('17:00-17:59','dinner'),'excluded')
        self.assertEqual(self.service('17:00-18:00','dinner'),'possible')

    def test_day_start_end_and_weekday_are_respected(self):
        self.day.start_time='10:00'
        self.assertEqual(self.service('07:00-10:15','breakfast'),'excluded')
        self.day.end_time='17:45'
        self.assertEqual(self.service('17:00-21:00','dinner'),'excluded')
        self.assertEqual(self.service('周一11:00-20:00'),'excluded')
        self.assertEqual(self.service('周二11:00-20:00'),'possible')

    def test_seasonal_window_uses_the_actual_visit_date(self):
        hours='5月1日至10月31日11:30-14:30;11月1日至次年4月30日17:00-21:00'
        self.assertEqual(self.service(hours),'possible')
        self.day.date='2026-11-01'
        self.assertEqual(self.service(hours),'excluded')

    def test_unknown_incomplete_overnight_and_unsupported_hours_remain_unknown(self):
        for hours in (None,'','11:00至打烊','18:00-次日02:00','节假日另行通知'):
            self.assertEqual(self.service(hours),'unknown',hours)

    def test_custom_meal_times_and_explicit_sequence_do_not_get_default_slot_filter(self):
        for text in ('午餐15点吃','先逛完两个景点再吃午饭'):
            request=self.request.model_copy(update={'free_text_input':text})
            self.assertEqual(self.service('15:00-17:00',request=request),'possible')
        self.assertEqual(self.service('15:00-17:00'),'excluded')

    def test_no_request_uses_only_explicit_day_window(self):
        self.assertEqual(meal_service(candidate('餐厅','15:00-17:00'),self.day,'lunch'),'possible')
        self.assertEqual(meal_service(candidate('餐厅','06:00-08:00'),self.day,'breakfast'),'excluded')

    def test_candidate_selection_skips_impossible_hours_and_preserves_source(self):
        choices=[candidate('甲餐厅','09:00-11:00'),candidate('乙餐厅','11:30-15:00')]
        pending=LangGraphTripPlanner._place_nearby_meals([(self.day,'lunch',self.day.attractions[0].location)],
            [choices],set(),2,request=self.request)
        self.assertEqual(pending,[]);self.assertEqual(self.day.meals[0].poi_id,'乙餐厅')
        self.assertEqual(self.day.meals[0].open_hours,'11:30-15:00')

    def test_unknown_hours_are_kept_and_cannot_force_a_large_detour(self):
        for known_lon,expected in ((116.401,'乙餐厅'),(116.42,'甲餐厅')):
            self.day.meals=[]
            choices=[candidate('甲餐厅',None),candidate('乙餐厅','11:30-15:00',known_lon)]
            LangGraphTripPlanner._place_nearby_meals([(self.day,'lunch',self.day.attractions[0].location)],
                [choices],set(),3,request=self.request)
            self.assertEqual(self.day.meals[0].poi_id,expected)
        self.day.meals=[]
        LangGraphTripPlanner._place_nearby_meals([(self.day,'lunch',self.day.attractions[0].location)],
            [[candidate('甲餐厅',None)]],set(),2,request=self.request)
        self.assertIsNone(self.day.meals[0].open_hours)

    async def test_explicit_or_locked_restaurant_is_not_dropped_for_default_hours(self):
        for mode in ('named','locked','confirmed'):
            current=Meal(name='自选餐厅',restaurant='自选餐厅',type='lunch',poi_id='selected',
                location=self.day.attractions[0].location,open_hours='15:00-17:00',locked=mode=='locked')
            if mode=='confirmed':current.cost_status='confirmed'
            self.day.meals=[current]
            request=self.request.model_copy(update={'free_text_input':'午餐去自选餐厅' if mode=='named' else ''})
            planner=LangGraphTripPlanner.__new__(LangGraphTripPlanner)
            with patch('app.agents.langgraph_planner.search_nearby_restaurants',new=AsyncMock(return_value=[])):
                await planner._align_meals_to_route(self.plan,request=request)
            self.assertTrue(any(m.poi_id=='selected' for m in self.day.meals),mode)
