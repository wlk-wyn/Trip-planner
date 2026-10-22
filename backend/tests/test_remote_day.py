import asyncio
import unittest
from unittest.mock import AsyncMock,patch
from app.models.schemas import Hotel,Meal,Location
from app.services.remote_day import repair_remote_day
from app.services.schedule_validator import validate_schedule
from app.services.meal_timing import meal_timing_issues
from test_schedule import sample
from test_travel_agent import REQUEST


class RemoteDayTests(unittest.IsolatedAsyncioTestCase):
    async def check(self,plan,request,progress=None):
        async def provider(a,b,*args):
            return {'duration_minutes':150 if abs(a.longitude-b.longitude)>.2 else 10,'distance_meters':1000}
        await validate_schedule(plan,request,provider)

    async def fixture(self):
        plan=sample(1);day=plan.days[0]
        day.attractions[0].name='远郊历史景区';day.attractions[0].poi_id='far'
        day.attractions[0].location=Location(longitude=120.65,latitude=30.24);day.attractions[0].visit_duration=150
        day.hotel=Hotel(name='城内住宿',poi_id='hotel',location=Location(longitude=120.15,latitude=30.24))
        day.meals=[Meal(name=slot,restaurant=slot,type=slot,poi_id=slot,route_matched=True,open_hours='07:00-23:00',location=Location(longitude=120.15 if slot=='breakfast' else 120.651,latitude=30.24)) for slot in ('breakfast','lunch','dinner')]
        await self.check(plan,REQUEST)
        self.assertEqual(day.schedule.status,'conflict')
        return plan

    def poi(self,**updates):
        return dict({'name':'城市历史博物馆','poi_id':'near','address':'城内地址','category':'科教文化服务;博物馆','coordinates':[120.16,30.24],'open_hours':'07:00-23:00'},**updates)

    async def align(self,plan,repair=False,protected_ids=None):
        for day in plan.days:
            for meal in day.meals:
                if meal.item_id not in (protected_ids or set()) and meal.type!='breakfast':
                    meal.location=day.attractions[0].location;meal.poi_id='near-'+meal.type
                    meal.restaurant='新'+meal.type

    async def test_complete_replacement_keeps_counts_durations_meals_and_original(self):
        plan=await self.fixture();before=plan.model_dump()
        with patch('app.services.remote_day.search_pois',new=AsyncMock(return_value=[self.poi()])):
            fixed,notes=await repair_remote_day(plan,REQUEST,self.check,self.align)
        self.assertTrue(notes);self.assertIn('远郊历史景区',notes[0]);self.assertIn('城市历史博物馆',notes[0])
        day=fixed.days[0]
        self.assertEqual(day.schedule.status,'feasible');self.assertEqual(meal_timing_issues(fixed,REQUEST),[])
        self.assertEqual(len(day.attractions),1);self.assertEqual(day.attractions[0].visit_duration,150)
        self.assertEqual((day.start_time,day.end_time,day.buffer_minutes),(plan.days[0].start_time,plan.days[0].end_time,plan.days[0].buffer_minutes))
        self.assertEqual({m.type for m in day.meals},{'breakfast','lunch','dinner'})
        self.assertEqual(plan.model_dump(),before)

    async def test_named_locked_appointment_confirmed_and_strict_requests_skip_search(self):
        for mode in ('named','locked','fixed','confirmed','strict','custom','named_meal','planned_meal'):
            plan=await self.fixture();request=REQUEST
            if mode=='named':request=REQUEST.model_copy(update={'free_text_input':'想去远郊历史景区'})
            if mode=='locked':plan.days[0].attractions[0].locked=True
            if mode=='fixed':plan.days[0].attractions[0].fixed_start_time='12:00'
            if mode=='confirmed':plan.days[0].attractions[0].cost_status='confirmed'
            if mode=='strict':request=REQUEST.model_copy(update={'reference_mode':'strict'})
            if mode=='custom':request=REQUEST.model_copy(update={'free_text_input':'午餐15点吃'})
            if mode=='named_meal':plan.days[0].meals[1].locked=True
            if mode=='planned_meal':plan.days[0].planned_start_times[plan.days[0].meals[1].item_id]='13:30'
            query=AsyncMock()
            with patch('app.services.remote_day.search_pois',new=query):
                fixed,notes=await repair_remote_day(plan,request,self.check,self.align)
            query.assert_not_awaited();self.assertEqual(notes,[]);self.assertEqual(fixed.model_dump(),plan.model_dump())

    async def test_abbreviated_great_wall_request_protects_badaling(self):
        plan=await self.fixture();plan.days[0].attractions[0].name='八达岭长城'
        await self.check(plan,REQUEST)
        query=AsyncMock()
        with patch('app.services.remote_day.search_pois',new=query):
            await repair_remote_day(plan,REQUEST.model_copy(update={'free_text_input':'想看长城'}),self.check,self.align)
        query.assert_not_awaited()

    async def test_missing_routes_closed_or_non_meal_complete_candidates_never_replace(self):
        for mode in ('unknown','closed','missing_meal','exception'):
            plan=await self.fixture();before=plan.model_dump()
            async def check(candidate,request,progress=None):
                if mode=='exception':raise RuntimeError('offline')
                if mode=='unknown':await validate_schedule(candidate,request,AsyncMock(side_effect=RuntimeError('offline')))
                else:await self.check(candidate,request,progress)
            async def align(candidate,**kwargs):
                await self.align(candidate,**kwargs)
                if mode=='missing_meal':candidate.days[0].meals=[m for m in candidate.days[0].meals if m.type!='lunch']
            poi=self.poi(open_hours='暂停营业') if mode=='closed' else self.poi()
            with patch('app.services.remote_day.search_pois',new=AsyncMock(return_value=[poi])):
                fixed,notes=await repair_remote_day(plan,REQUEST,check,align)
            self.assertEqual(fixed.model_dump(),before,mode);self.assertEqual(notes,[],mode)

    async def test_duplicate_subvenue_or_far_candidates_are_not_scheduled(self):
        plan=await self.fixture();query=[self.poi(poi_id='far'),self.poi(name='城市博物馆停车场'),self.poi(coordinates=[120.66,30.24]),self.poi(coordinates=[0,0])]
        checker=AsyncMock()
        with patch('app.services.remote_day.search_pois',new=AsyncMock(return_value=query)):
            _,notes=await repair_remote_day(plan,REQUEST,checker,self.align)
        checker.assert_not_awaited();self.assertEqual(notes,[])

    async def test_cancellation_propagates(self):
        plan=await self.fixture()
        with patch('app.services.remote_day.search_pois',new=AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):await repair_remote_day(plan,REQUEST,self.check,self.align)

    async def test_nearest_commercial_museum_is_not_a_history_substitute(self):
        plan=await self.fixture()
        pois=[self.poi(name='巧克力博物馆',poi_id='chocolate',coordinates=[120.151,30.24]),self.poi()]
        with patch('app.services.remote_day.search_pois',new=AsyncMock(return_value=pois)):
            fixed,notes=await repair_remote_day(plan,REQUEST,self.check,self.align)
        self.assertTrue(notes);self.assertEqual(fixed.days[0].attractions[0].poi_id,'near')

    async def test_failed_candidates_are_bounded_and_other_days_do_not_change(self):
        plan=await self.fixture();extra=sample(1).days[0];extra.day_index=1;extra.date='2026-10-04'
        plan.days.append(extra)
        checker=AsyncMock(side_effect=RuntimeError('unavailable'))
        pois=[self.poi(poi_id=f'near-{i}',name=f'历史博物馆{i}') for i in range(5)]
        with patch('app.services.remote_day.search_pois',new=AsyncMock(return_value=pois)):
            fixed,notes=await repair_remote_day(plan,REQUEST,checker,self.align)
        self.assertEqual(checker.await_count,2);self.assertEqual(notes,[])
        self.assertEqual(fixed.model_dump(),plan.model_dump())
        with patch('app.services.remote_day.search_pois',new=AsyncMock(return_value=[self.poi()])):
            fixed,notes=await repair_remote_day(plan,REQUEST,self.check,self.align)
        self.assertTrue(notes);self.assertEqual(fixed.days[1].model_dump(),extra.model_dump())
        self.assertIn('远郊历史景区',fixed.adjustment_notes[0])
