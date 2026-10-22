import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.models.schemas import Meal, Location, Hotel
from app.services.meal_windows import repair_meal_windows, repair_final_meals, protected_meal_ids, MAX_MEAL_REPAIRS
from app.services.schedule_validator import validate_schedule
from test_schedule import sample
from test_travel_agent import REQUEST


class MealWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_timing_timeout_keeps_completed_opening_hours_repair(self):
        plan=await self.fixture()
        real_wait_for=asyncio.wait_for
        def limited(awaitable,timeout):
            if timeout==15:
                awaitable.close()
                raise asyncio.TimeoutError()
            return real_wait_for(awaitable,timeout)
        with patch('app.services.meal_windows.search_nearby_restaurants',new=AsyncMock(return_value=[self.candidate()])), \
             patch('app.services.meal_windows.asyncio.wait_for',side_effect=limited):
            fixed,notes=await repair_final_meals(plan,REQUEST,self.validate)
        self.assertEqual(fixed.days[0].meals[0].poi_id,'new')
        self.assertEqual(fixed.days[0].schedule.status,'feasible')
        self.assertTrue(notes)

    async def late_lunch_fixture(self, duration=240, dinner=False):
        plan=sample(2 if dinner else 1);day=plan.days[0]
        day.attractions[0].visit_duration=duration
        if dinner: day.attractions[1].visit_duration=60
        day.meals=[Meal(name='',restaurant='原午餐店',poi_id='old-lunch',route_matched=True,type='lunch',
            location=Location(longitude=120.152,latitude=30.24),open_hours='09:00-21:00')]
        if dinner:
            day.meals.append(Meal(name='',restaurant='晚餐店',poi_id='dinner',type='dinner',
                location=Location(longitude=120.154,latitude=30.24),open_hours='09:00-21:00'))
        async def checker(p,r,progress=None):
            async def provider(a,b,city,mode):
                return {'duration_minutes':70 if b.longitude==120.152 else 10,'distance_meters':500}
            await validate_schedule(p,r,provider)
        await checker(plan,REQUEST)
        return plan,checker

    async def test_nearer_lunch_preserves_visit_time_and_uses_new_arrival_for_hours(self):
        plan,checker=await self.late_lunch_fixture();before=plan.model_dump()
        poi=self.candidate(name='附近午餐店',open_hours='09:00-14:30')
        with patch('app.services.meal_windows.search_nearby_restaurants',new=AsyncMock(return_value=[poi])) as query:
            fixed,_=await repair_meal_windows(plan,REQUEST,checker,include_timing=True)
        query.assert_awaited_once()
        lunch=next(i for i in fixed.days[0].schedule.items if i.kind=='meal')
        self.assertEqual(lunch.start_time,'13:25')
        self.assertEqual(fixed.days[0].attractions,plan.days[0].attractions)
        self.assertEqual(fixed.days[0].meals[0].poi_id,'new')
        self.assertEqual(plan.model_dump(),before)

    async def test_zero_travel_lower_bound_can_skip_impossible_lunch_search(self):
        plan,checker=await self.late_lunch_fixture(duration=300)
        with patch('app.services.meal_windows.search_nearby_restaurants',new=AsyncMock()) as query:
            fixed,attempts=await repair_meal_windows(plan,REQUEST,checker,include_timing=True)
        query.assert_not_awaited()
        self.assertEqual(attempts,[])
        self.assertEqual(fixed.model_dump(),plan.model_dump())

    async def test_nearer_lunch_cannot_introduce_an_early_dinner(self):
        plan,checker=await self.late_lunch_fixture(dinner=True)
        with patch('app.services.meal_windows.search_nearby_restaurants',new=AsyncMock(return_value=[self.candidate(open_hours='09:00-21:00')])):
            fixed,_=await repair_meal_windows(plan,REQUEST,checker,include_timing=True)
        self.assertEqual(fixed.model_dump(),plan.model_dump())

    async def test_explicit_late_lunch_does_not_trigger_default_window_replacement(self):
        plan,checker=await self.late_lunch_fixture()
        request=REQUEST.model_copy(update={'free_text_input':'午餐15点吃'})
        with patch('app.services.meal_windows.search_nearby_restaurants',new=AsyncMock()) as query:
            fixed,_=await repair_meal_windows(plan,request,checker,include_timing=True)
        query.assert_not_awaited()
        self.assertEqual(fixed.model_dump(),plan.model_dump())

    async def test_final_route_and_optional_visit_change_are_accepted_as_one_feasible_proposal(self):
        plan = await self.routing_fixture(); day = plan.days[0]
        day.attractions.append(day.attractions[0].model_copy(update={'item_id':'optional-stop','name':'可选散步点','visit_duration':60,
            'location':Location(longitude=120.153,latitude=30.24)}))
        async def unavailable(a,b,city,mode):
            if a.longitude == day.meals[0].location.longitude: raise RuntimeError('no route')
            return {'duration_minutes':10,'distance_meters':500}
        await validate_schedule(plan,REQUEST,unavailable)
        before=plan.model_dump()
        async def slower(partial,request,progress=None):
            await validate_schedule(partial,request,AsyncMock(return_value={'duration_minutes':30,'distance_meters':500}))
        with patch('app.services.meal_windows.search_nearby_restaurants',new=AsyncMock(return_value=[self.candidate()])):
            result,notes=await repair_final_meals(plan,REQUEST,slower)
        self.assertEqual(result.days[0].schedule.status,'feasible')
        self.assertEqual(len(result.days[0].attractions),1)
        self.assertEqual(result.days[0].attractions[0],day.attractions[0])
        self.assertTrue(any('可选散步点' in note for note in notes))
        self.assertEqual(plan.model_dump(),before)
        request=REQUEST.model_copy(update={'free_text_input':'想去可选散步点'})
        with patch('app.services.meal_windows.search_nearby_restaurants',new=AsyncMock(return_value=[self.candidate()])):
            rejected,notes=await repair_final_meals(plan,request,slower)
        self.assertEqual(rejected,plan)
        self.assertEqual(notes,[])
        calls=0
        async def failing_settle(partial,request,progress=None):
            nonlocal calls
            calls+=1
            if calls>1: raise RuntimeError('route service unavailable')
            await slower(partial,request,progress)
        with patch('app.services.meal_windows.search_nearby_restaurants',new=AsyncMock(return_value=[self.candidate()])):
            rejected,notes=await repair_final_meals(plan,REQUEST,failing_settle)
        self.assertEqual(rejected,plan)
        self.assertEqual(notes,[])

    async def routing_fixture(self, incoming=False, hours='17:00-23:00'):
        plan = await self.fixture(); day = plan.days[0]
        day.meals[0].open_hours = hours
        day.hotel = Hotel(name='原住宿', location=Location(longitude=120.155, latitude=30.24))
        day.identify_visits()
        old = day.meals[0].location.longitude
        async def unavailable(a, b, city, mode):
            if (b.longitude == old if incoming else a.longitude == old):
                raise RuntimeError('no usable route')
            return {'duration_minutes':10, 'distance_meters':500}
        await validate_schedule(plan, REQUEST, unavailable)
        return plan

    async def test_final_pass_resolves_missing_hotel_link_by_searching_near_endpoint(self):
        plan = await self.routing_fixture(); before = plan.model_dump()
        self.assertEqual(plan.days[0].schedule.status, 'unverified')
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate()])) as query:
            unchanged, _ = await repair_meal_windows(plan, REQUEST, self.validate)
            query.assert_not_awaited()
            result, _ = await repair_meal_windows(plan, REQUEST, self.validate, include_missing_routes=True, repair_limit=2)
        self.assertEqual(unchanged, plan)
        self.assertEqual(result.days[0].schedule.status, 'feasible')
        self.assertEqual(query.await_args.args[0], plan.days[0].hotel.location.longitude)
        self.assertEqual(result.days[0].hotel, plan.days[0].hotel)
        self.assertEqual(plan.model_dump(), before)

    async def test_missing_incoming_time_is_recomputed_before_accepting_new_meal(self):
        plan = await self.routing_fixture(incoming=True)
        item = next(i for i in plan.days[0].schedule.items if i.kind == 'meal')
        self.assertIsNone(item.start_time)
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate()])):
            result, _ = await repair_meal_windows(plan, REQUEST, self.validate, include_missing_routes=True)
        self.assertEqual(result.days[0].schedule.status, 'feasible')
        self.assertTrue(next(i for i in result.days[0].schedule.items if i.kind == 'meal').start_time)

    async def test_route_repair_does_not_invent_hours_or_replace_known_hours_with_unknown(self):
        plan = await self.routing_fixture(hours=None)
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate(open_hours=None)])):
            result, _ = await repair_meal_windows(plan, REQUEST, self.validate, include_missing_routes=True)
        self.assertEqual(result.days[0].schedule.status, 'feasible')
        self.assertIsNone(result.days[0].meals[0].open_hours)
        known = await self.routing_fixture()
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate(open_hours=None)])):
            check = AsyncMock()
            result, _ = await repair_meal_windows(known, REQUEST, check, include_missing_routes=True)
        self.assertEqual(result, known); check.assert_not_awaited()

    async def test_repair_must_not_lose_a_previously_verified_unrelated_leg(self):
        plan = await self.routing_fixture()
        async def degraded(candidate, request, progress=None):
            await self.validate(candidate, request)
            segment = candidate.days[0].schedule.segments[0]
            segment.status, segment.duration_minutes = 'unverified', None
            candidate.days[0].schedule.status = 'unverified'
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate()])):
            result, _ = await repair_meal_windows(plan, REQUEST, degraded, include_missing_routes=True)
        self.assertEqual(result, plan)

    async def test_failed_context_is_reused_only_after_arrival_conditions_change(self):
        plan = await self.fixture()
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[])):
            _, attempts = await repair_meal_windows(plan, REQUEST, self.validate)
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate()])) as query:
            same, _ = await repair_meal_windows(plan, REQUEST, self.validate, attempts)
            query.assert_not_awaited(); self.assertEqual(same, plan)
            plan.days[0].attractions[0].visit_duration -= 10
            await self.validate(plan, REQUEST)
            fixed, updated = await repair_meal_windows(plan, REQUEST, self.validate, attempts)
        self.assertEqual(len(updated), 2)
        self.assertEqual(fixed.days[0].schedule.status, 'feasible')

    async def fixture(self):
        plan = sample(1)
        day = plan.days[0]
        day.start_time = '17:00'; day.end_time = '23:00'
        day.attractions[0].visit_duration = 90
        day.meals = [Meal(type='dinner', name='', restaurant='早关门饭店', poi_id='old', route_matched=True,
                          location=Location(longitude=120.152, latitude=30.24), open_hours='09:00-19:00',
                          recommended_dish='原餐厅菜单', estimated_cost=88, cost_status='estimated', cost_source='旧参考')]
        await self.validate(plan, REQUEST)
        return plan

    async def validate(self, plan, request, progress=None):
        return await validate_schedule(plan, request, AsyncMock(return_value={'duration_minutes': 10, 'distance_meters': 500}), progress)

    def candidate(self, **fields):
        return {'poi_id': 'new', 'name': '晚间家常菜饭店', 'address': '合成测试地址', 'coordinates': [120.151, 30.24], 'open_hours': '17:00-23:00', **fields}

    async def test_full_meal_and_actual_routes_checked_before_replacement(self):
        plan = await self.fixture(); before = plan.model_dump()
        self.assertEqual(plan.days[0].schedule.status, 'conflict')
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate()])) as query:
            validator = AsyncMock(side_effect=self.validate)
            result, attempts = await repair_meal_windows(plan, REQUEST, validator)
        self.assertEqual(result.days[0].schedule.status, 'feasible')
        self.assertEqual(result.days[0].schedule.items[-1].duration_minutes, 60)
        self.assertEqual(result.days[0].meals[0].poi_id, 'new')
        self.assertEqual(result.days[0].meals[0].item_id, plan.days[0].meals[0].item_id)
        self.assertNotEqual(result.days[0].meals[0].place_id, plan.days[0].meals[0].place_id)
        self.assertEqual(result.days[0].meals[0].recommended_dish, '')
        self.assertIsNone(result.days[0].meals[0].estimated_cost)
        self.assertEqual(plan.model_dump(), before)
        self.assertEqual(len(attempts), 1)
        query.assert_awaited_once(); validator.assert_awaited_once()

    async def test_unknown_closed_or_short_hours_cannot_hide_known_conflict(self):
        plan = await self.fixture()
        values = [self.candidate(open_hours=None), self.candidate(poi_id='closed', open_hours='周六闭馆'), self.candidate(poi_id='short', open_hours='18:00-19:00')]
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=values)):
            check = AsyncMock()
            result, _ = await repair_meal_windows(plan, REQUEST, check)
        self.assertEqual(result.model_dump(), plan.model_dump())
        check.assert_not_awaited()

    async def test_changed_travel_must_not_push_candidate_after_closing(self):
        plan = await self.fixture()
        async def slow(candidate, request, progress=None):
            return await validate_schedule(candidate, request, AsyncMock(return_value={'duration_minutes': 80, 'distance_meters': 500}))
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate(open_hours='17:00-20:00')])):
            result, _ = await repair_meal_windows(plan, REQUEST, slow)
        self.assertEqual(result.model_dump(), plan.model_dump())

    async def test_restaurant_opening_wait_does_not_turn_lunch_into_dinner(self):
        plan = await self.fixture(); day = plan.days[0]
        day.start_time = '11:00'
        day.meals[0].type = 'lunch'; day.meals[0].open_hours = '17:00-23:00'
        await self.validate(plan, REQUEST)
        self.assertEqual(day.schedule.items[-1].start_time, '17:00')
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate(open_hours='10:00-21:00')])):
            result, _ = await repair_meal_windows(plan, REQUEST, self.validate)
        self.assertEqual(result.days[0].schedule.items[-1].start_time, '12:55')
        self.assertEqual(result.days[0].schedule.items[-1].duration_minutes, 60)

    async def test_intentionally_planned_meal_start_is_not_silently_advanced(self):
        plan = await self.fixture(); day = plan.days[0]
        day.start_time = '11:00'; day.meals[0].open_hours = '17:00-23:00'
        day.planned_start_times[day.meals[0].item_id] = '18:00'
        await self.validate(plan, REQUEST)
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock()) as search:
            result, _ = await repair_meal_windows(plan, REQUEST, self.validate)
        search.assert_not_awaited()
        self.assertEqual(result.days[0].schedule.items[-1].start_time, '18:00')

    async def test_failed_route_query_keeps_original_meal(self):
        plan = await self.fixture()
        async def failed(candidate, request, progress=None):
            return await validate_schedule(candidate, request, AsyncMock(side_effect=RuntimeError('offline')))
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate()])):
            result, _ = await repair_meal_windows(plan, REQUEST, failed)
        self.assertEqual(result.model_dump(), plan.model_dump())

    async def test_search_failure_or_no_candidates_keeps_original_meal(self):
        plan = await self.fixture()
        for search in [AsyncMock(return_value=[]), AsyncMock(side_effect=RuntimeError('offline'))]:
            with patch('app.services.meal_windows.search_nearby_restaurants', new=search):
                result, _ = await repair_meal_windows(plan, REQUEST, self.validate)
            self.assertEqual(result.model_dump(), plan.model_dump())

    async def test_locked_manual_named_brand_and_user_price_are_protected(self):
        for mode in ['locked', 'manual', 'named', 'price']:
            plan = await self.fixture(); meal = plan.days[0].meals[0]; request = REQUEST
            if mode == 'locked': meal.locked = True
            if mode == 'manual': meal.route_matched = False
            if mode == 'named':
                meal.restaurant = '楼外楼(西湖店)'
                request = REQUEST.model_copy(update={'free_text_input': '晚餐一定吃楼外楼'})
                self.assertIn(meal.item_id, protected_meal_ids(plan, request))
            if mode == 'price': meal.cost_status = 'confirmed'
            with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock()) as search:
                result, attempts = await repair_meal_windows(plan, request, self.validate)
            search.assert_not_awaited()
            self.assertFalse(attempts)
            self.assertEqual(result.model_dump(), plan.model_dump())

    async def test_budget_and_repeated_failure_do_not_repeat_queries(self):
        plan = await self.fixture()
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[])) as search:
            result, attempted = await repair_meal_windows(plan, REQUEST, self.validate)
            await repair_meal_windows(result, REQUEST, self.validate, attempted)
            await repair_meal_windows(plan, REQUEST, self.validate, ['used']*MAX_MEAL_REPAIRS)
        search.assert_awaited_once()

    async def test_new_fixed_appointment_conflict_is_not_accepted(self):
        plan = sample(2); day = plan.days[0]
        day.start_time = '17:00'; day.end_time = '23:00'
        day.attractions[0].visit_duration = 90
        day.attractions[1].visit_duration = 30; day.attractions[1].fixed_start_time = '20:30'
        day.meals = [Meal(type='dinner', name='', restaurant='早关门饭店', poi_id='old', route_matched=True,
                         location=Location(longitude=120.152, latitude=30.24), open_hours='09:00-19:00')]
        day.identify_visits()
        day.visit_order = [day.attractions[0].item_id, day.meals[0].item_id, day.attractions[1].item_id]
        await self.validate(plan, REQUEST)
        async def later(candidate, request, progress=None):
            return await validate_schedule(candidate, request, AsyncMock(return_value={'duration_minutes': 30, 'distance_meters': 500}))
        with patch('app.services.meal_windows.search_nearby_restaurants', new=AsyncMock(return_value=[self.candidate()])):
            result, _ = await repair_meal_windows(plan, REQUEST, later)
        self.assertEqual(result.model_dump(), plan.model_dump())
