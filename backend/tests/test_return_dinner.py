import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.models.schemas import Hotel, Meal, Location
from app.services.return_dinner import repair_return_dinner
from app.services.meal_timing import meal_timing_issues
from app.services.schedule_validator import validate_schedule
from test_schedule import sample
from test_travel_agent import REQUEST


class ReturnDinnerTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, plan, request, progress=None):
        async def provider(a,b,city,mode):
            return {'duration_minutes':120 if abs(a.longitude-b.longitude) > .2 else 10,'distance_meters':1000}
        await validate_schedule(plan,request,provider)

    async def fixture(self):
        plan = sample(1); day = plan.days[0]
        day.attractions[0].location = Location(longitude=120.65,latitude=30.24)
        day.attractions[0].visit_duration = 150
        day.hotel = Hotel(name='城内住宿',poi_id='hotel',location=Location(longitude=120.15,latitude=30.24))
        day.meals = [Meal(name='午餐',restaurant='午餐',type='lunch',poi_id='lunch',route_matched=True,
                         open_hours='10:00-17:00',location=Location(longitude=120.651,latitude=30.24)),
                     Meal(name='远郊餐厅',restaurant='远郊餐厅',type='dinner',poi_id='old-dinner',route_matched=True,
                         open_hours='12:00-23:00',estimated_cost=100,recommended_dish='旧菜品',
                         location=Location(longitude=120.652,latitude=30.24))]
        await self.check(plan,REQUEST)
        return plan

    def poi(self,**updates):
        return dict(name='城内家常菜',poi_id='city-dinner',address='住宿附近',coordinates=[120.151,30.24],
                    open_hours='17:00-22:00',**updates)

    async def test_replacement_and_lunch_reorder_are_adopted_together(self):
        plan = await self.fixture(); before = plan.model_dump()
        with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock(return_value=[self.poi()])) as query:
            fixed, notes = await repair_return_dinner(plan,REQUEST,self.check)
        self.assertTrue(notes)
        self.assertEqual(query.await_args.args[:2],(120.15,30.24))
        self.assertEqual(fixed.days[0].schedule.status,'feasible')
        self.assertEqual(meal_timing_issues(fixed,REQUEST),[])
        self.assertEqual(fixed.days[0].attractions,plan.days[0].attractions)
        old, new = plan.days[0].meals[-1], fixed.days[0].meals[-1]
        self.assertEqual(new.item_id,old.item_id)
        self.assertEqual(new.poi_id,'city-dinner')
        self.assertNotEqual(new.place_id,old.place_id)
        self.assertEqual(new.recommended_dish,'')
        self.assertIsNone(new.estimated_cost)
        self.assertEqual(plan.model_dump(),before)

    async def test_locked_named_or_confirmed_dinner_is_never_replaced(self):
        for mode in ('locked','named','confirmed','fixed','custom'):
            plan = await self.fixture(); req = REQUEST
            if mode=='locked': plan.days[0].meals[-1].locked=True
            if mode=='confirmed': plan.days[0].meals[-1].cost_status='confirmed'
            if mode=='named': req=REQUEST.model_copy(update={'free_text_input':'想去远郊餐厅'})
            if mode=='fixed': plan.days[0].attractions[0].fixed_start_time='11:15'
            if mode=='custom': req=REQUEST.model_copy(update={'free_text_input':'晚餐16点吃'})
            with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock()) as query:
                fixed, notes=await repair_return_dinner(plan,req,self.check)
            query.assert_not_awaited()
            self.assertEqual(fixed.model_dump(),plan.model_dump())
            self.assertEqual(notes,[])

    async def test_unknown_hours_cannot_replace_a_known_hours_restaurant(self):
        plan=await self.fixture(); poi=self.poi(); poi['open_hours']=None
        with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock(return_value=[poi])):
            fixed, notes=await repair_return_dinner(plan,REQUEST,self.check)
        self.assertEqual(fixed.model_dump(),plan.model_dump())
        self.assertEqual(notes,[])

    async def test_inaccessible_duplicate_or_far_candidates_not_selected(self):
        plan=await self.fixture()
        pois=[]
        for changes in ({'name':'机关食堂'},{'poi_id':'lunch'},{'coordinates':[120.65,30.24]},{'open_hours':'暂停营业'}):
            poi=self.poi(); poi.update(changes); pois.append(poi)
        validator=AsyncMock()
        with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock(return_value=pois)):
            _,notes=await repair_return_dinner(plan,REQUEST,validator)
        validator.assert_not_called()
        self.assertEqual(notes,[])

    async def test_missing_new_routes_and_exceptions_leave_original_unchanged(self):
        for mode in ('unknown','exception'):
            plan=await self.fixture(); before=plan.model_dump()
            async def checker(p,r,progress):
                if mode=='exception': raise RuntimeError('offline')
                await validate_schedule(p,r,AsyncMock(side_effect=RuntimeError('offline')))
            with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock(return_value=[self.poi()])):
                fixed,notes=await repair_return_dinner(plan,REQUEST,checker)
            self.assertEqual(fixed.model_dump(),before)
            self.assertEqual(notes,[])

    async def test_closed_candidate_cannot_pass_even_if_traffic_is_known(self):
        plan=await self.fixture(); poi=self.poi(); poi['open_hours']='09:00-16:00'
        with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock(return_value=[poi])):
            fixed,notes=await repair_return_dinner(plan,REQUEST,self.check)
        self.assertEqual(fixed.model_dump(),plan.model_dump())
        self.assertEqual(notes,[])

    async def test_cancellation_propagates(self):
        plan=await self.fixture()
        with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await repair_return_dinner(plan,REQUEST,self.check)

    async def test_success_does_not_trigger_a_second_search(self):
        plan=await self.fixture()
        with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock(return_value=[self.poi()])):
            fixed,_=await repair_return_dinner(plan,REQUEST,self.check)
        with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock()) as query:
            again,notes=await repair_return_dinner(fixed,REQUEST,self.check)
        query.assert_not_awaited()
        self.assertEqual(again.model_dump(),fixed.model_dump())
        self.assertEqual(notes,[])

    async def test_a_satisfactory_remote_dinner_is_preserved(self):
        plan=await self.fixture(); day=plan.days[0]
        day.attractions[0].visit_duration=90
        day.end_time='22:00'
        day.planned_start_times[day.meals[-1].item_id]='17:00'
        await self.check(plan,REQUEST)
        self.assertEqual(meal_timing_issues(plan,REQUEST),[])
        with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock()) as query:
            fixed,notes=await repair_return_dinner(plan,REQUEST,self.check)
        query.assert_not_awaited()
        self.assertEqual(fixed.model_dump(),plan.model_dump())
        self.assertEqual(notes,[])

    async def test_search_timeout_retains_original(self):
        plan=await self.fixture()
        with patch('app.services.return_dinner.search_nearby_restaurants',new=AsyncMock(side_effect=asyncio.TimeoutError())):
            fixed,notes=await repair_return_dinner(plan,REQUEST,self.check)
        self.assertEqual(fixed.model_dump(),plan.model_dump())
        self.assertEqual(notes,[])
