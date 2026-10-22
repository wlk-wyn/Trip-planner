import asyncio
import threading
import unittest
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.models.schemas import Meal
from app.services.meal_timing import meal_timing_issues, prepare_meal_times, repair_meal_timing, fit_meal_durations
from app.services.schedule_validator import validate_schedule
from app.services.evening_planning import evening_coverage
from test_schedule import sample
from test_travel_agent import REQUEST


class MealTimingTests(unittest.IsolatedAsyncioTestCase):
    async def test_lunch_reorder_can_fit_suggested_visit_inside_closing_time(self):
        from app.services.meal_timing import prepare_meal_times
        plan = sample(1)
        day = plan.days[0]
        day.attractions[0].visit_duration = 240
        day.attractions[0].open_hours = '09:00-17:00'
        day.meals = [Meal(type=slot, name=slot, restaurant=slot, location=day.attractions[0].location)
                     for slot in ('breakfast', 'lunch', 'dinner')]
        plan = prepare_meal_times(plan, REQUEST)
        async def validator(candidate, request, progress=None):
            await validate_schedule(candidate, request, AsyncMock(return_value={'duration_minutes':30, 'distance_meters':1000}))
        await validator(plan, REQUEST)
        before = plan.model_dump()
        self.assertTrue(meal_timing_issues(plan, REQUEST))
        fixed, notes = await repair_meal_timing(plan, REQUEST, validator, allow_duration_fit=True)
        self.assertTrue(notes)
        self.assertFalse(meal_timing_issues(fixed, REQUEST))
        self.assertEqual(fixed.days[0].schedule.status, 'feasible')
        self.assertGreaterEqual(fixed.days[0].attractions[0].visit_duration, 180)
        self.assertLess(fixed.days[0].attractions[0].visit_duration, 240)
        self.assertEqual(plan.model_dump(), before)
        self.assertTrue(any('240→' in note for note in notes))
        # Without permission to fit suggested duration, return the original.
        unchanged, _ = await repair_meal_timing(plan, REQUEST, validator, allow_duration_fit=False)
        self.assertEqual(unchanged.model_dump(), before)
        explicit = REQUEST.model_copy(update={'free_text_input': '这个博物馆参观4小时'})
        protected, _ = await repair_meal_timing(plan, explicit, validator, allow_duration_fit=True)
        self.assertEqual(protected.model_dump(), before)
        locked = plan.model_copy(deep=True)
        locked.days[0].attractions[0].locked = True
        protected, _ = await repair_meal_timing(locked, REQUEST, validator, allow_duration_fit=True)
        self.assertEqual(protected.model_dump(), locked.model_dump())

    async def check(self, plan, request, progress=None):
        await validate_schedule(plan, request, AsyncMock(return_value={'duration_minutes':10, 'distance_meters':400}))

    async def plan(self):
        plan = sample(3)
        day = plan.days[0]
        for p in day.attractions:
            p.visit_duration = 150
        day.meals = [Meal(restaurant='午餐店', name='午餐店', type='lunch', location=day.attractions[0].location, open_hours='07:00-14:30')]
        await self.check(plan, REQUEST)
        return plan

    async def test_late_lunch_is_visible_as_a_reference_warning(self):
        plan = await self.plan()
        issues = meal_timing_issues(plan, REQUEST)
        self.assertEqual([i['meal_type'] for i in issues], ['lunch'])
        self.assertEqual(issues[0]['severity'], 'warning')
        self.assertIn('11:30—14:00', issues[0]['message'])

    async def test_reordering_lunch_fixes_closed_restaurant_without_deleting_visits(self):
        plan = await self.plan(); before = plan.model_dump()
        fixed, notes = await repair_meal_timing(plan, REQUEST, self.check)
        self.assertTrue(notes)
        self.assertEqual(meal_timing_issues(fixed, REQUEST), [])
        self.assertEqual(fixed.days[0].schedule.status, 'feasible')
        self.assertEqual([(p.item_id,p.visit_duration) for p in fixed.days[0].attractions], [(p.item_id,p.visit_duration) for p in plan.days[0].attractions])
        self.assertEqual(fixed.days[0].meals[0].poi_id, plan.days[0].meals[0].poi_id)
        self.assertEqual(plan.model_dump(), before)

    async def test_new_draft_lower_bounds_do_not_mean_opening_hours_are_verified(self):
        plan = sample(1)
        day = plan.days[0]
        day.meals = [Meal(name='晚餐', restaurant='晚餐', type='dinner', location=day.attractions[0].location)]
        prepared = prepare_meal_times(plan, REQUEST)
        await self.check(prepared, REQUEST)
        item = next(i for i in prepared.days[0].schedule.items if i.kind == 'meal')
        self.assertEqual(item.start_time, '17:00')
        self.assertIsNone(prepared.days[0].meals[0].open_hours)

    async def test_custom_time_and_explicit_order_not_overridden(self):
        plan = await self.plan()
        for text in ('午餐15点吃', '15:00吃午饭', '晚餐晚上九点', '先去两个景点再吃午饭'):
            request = REQUEST.model_copy(update={'free_text_input':text})
            validator = AsyncMock()
            fixed, notes = await repair_meal_timing(plan, request, validator)
            validator.assert_not_called()
            self.assertEqual(fixed.model_dump(), plan.model_dump())
            self.assertEqual(notes, [])
            if '先' not in text:
                self.assertEqual(meal_timing_issues(plan, request)[0]['code'], 'meal_time_custom_unverified')

    async def test_locked_appointments_and_planned_late_meals_preserved(self):
        for mode in ('locked', 'appointment', 'planned'):
            plan = await self.plan()
            day = plan.days[0]
            if mode == 'locked': day.meals[0].locked = True
            if mode == 'appointment': day.attractions[0].fixed_start_time = '09:00'
            if mode == 'planned': day.planned_start_times[day.meals[0].item_id] = '15:00'
            validator = AsyncMock()
            _, notes = await repair_meal_timing(plan, REQUEST, validator)
            validator.assert_not_called()
            self.assertEqual(notes, [])

    async def test_unknown_routes_or_timeout_cannot_be_accepted(self):
        for failure in ('unknown', 'timeout'):
            plan = await self.plan(); before = plan.model_dump()
            async def checker(p, r, progress):
                if failure == 'timeout': raise asyncio.TimeoutError()
                await validate_schedule(p,r,AsyncMock(side_effect=RuntimeError('offline')))
            fixed, notes = await repair_meal_timing(plan, REQUEST, checker)
            self.assertEqual(fixed.model_dump(), before)
            self.assertEqual(notes, [])

    async def test_cancellation_propagates_and_is_not_treated_as_a_candidate_failure(self):
        plan = await self.plan()
        with self.assertRaises(asyncio.CancelledError):
            await repair_meal_timing(plan, REQUEST, AsyncMock(side_effect=asyncio.CancelledError()))

    async def test_repair_is_idempotent(self):
        plan, _ = await repair_meal_timing(await self.plan(), REQUEST, self.check)
        validator = AsyncMock()
        _, notes = await repair_meal_timing(plan, REQUEST, validator)
        validator.assert_not_called()
        self.assertEqual(notes, [])

    async def test_night_schedule_and_unrelated_day_preserved(self):
        plan = await self.plan()
        day = plan.days[0]
        night = day.attractions[-1]
        night.name, night.category, night.description, night.visit_duration = '江畔夜景', '风景', '看夜景', 60
        day.planned_start_times[night.item_id] = '18:00'
        await self.check(plan, REQUEST)
        request = REQUEST.model_copy(update={'free_text_input':'想看夜景'})
        self.assertTrue(evening_coverage(plan, request))
        fixed, notes = await repair_meal_timing(plan, request, self.check)
        self.assertTrue(notes)
        self.assertTrue(evening_coverage(fixed, request))

    async def test_stale_schedule_not_used_to_rearrange(self):
        plan = await self.plan()
        plan.days[0].attractions[0].visit_duration += 5
        validator = AsyncMock()
        _, notes = await repair_meal_timing(plan, REQUEST, validator)
        validator.assert_not_called()
        self.assertEqual(notes, [])

    async def test_early_dinner_waits_without_unnecessary_reordering(self):
        plan = sample(1)
        plan.days[0].meals = [Meal(name='餐厅', restaurant='餐厅', type='dinner', location=plan.days[0].attractions[0].location)]
        await self.check(plan, REQUEST)
        original_ids = [i.id for i in plan.days[0].schedule.items]
        fixed, notes = await repair_meal_timing(plan, REQUEST, self.check)
        self.assertTrue(notes)
        self.assertEqual([i.id for i in fixed.days[0].schedule.items], original_ids)
        self.assertEqual(next(i.start_time for i in fixed.days[0].schedule.items if i.kind == 'meal'), '17:00')

    async def test_duration_adjustment_is_atomic_bounded_and_user_duration_is_protected(self):
        plan = await self.plan()
        plan.days[0].end_time = '18:30'
        await self.check(plan, REQUEST)
        original = plan.model_dump()
        unchanged, notes = await repair_meal_timing(plan, REQUEST, self.check)
        self.assertEqual(unchanged.model_dump(), original)
        self.assertEqual(notes, [])
        fixed, notes = await repair_meal_timing(plan, REQUEST, self.check, allow_duration_fit=True)
        self.assertEqual(fixed.days[0].schedule.status, 'feasible')
        self.assertTrue(any('分钟' in note and '→' in note for note in notes))
        self.assertEqual(len(fixed.days[0].attractions), len(plan.days[0].attractions))
        for old, new in zip(plan.days[0].attractions, fixed.days[0].attractions):
            self.assertGreaterEqual(new.visit_duration, old.visit_duration * .75)
        request = REQUEST.model_copy(update={'free_text_input':'每个景点至少150分钟'})
        unchanged, notes = await repair_meal_timing(plan, request, self.check, allow_duration_fit=True)
        self.assertEqual(unchanged.model_dump(), original)
        self.assertEqual(notes, [])


class LateMealDurationFitTests(unittest.TestCase):
    def fixture(self):
        from app.models.schemas import TripPlan,TripRequest
        data=json.loads((Path(__file__).parent/'fixtures/late_meal_duration_failure.json').read_text(encoding='utf-8'))
        return TripPlan(**data['plan_snapshot']),TripRequest(**data['case_request'])

    def test_real_failed_snapshot_recomputes_without_changing_places_order_or_queried_routes(self):
        from app.services.schedule_integrity import check_integrity
        plan,request=self.fixture();before=plan.model_dump()
        self.assertTrue(meal_timing_issues(plan,request))
        fixed,notes=fit_meal_durations(plan,request)
        self.assertTrue(notes)
        self.assertEqual(plan.model_dump(),before)
        self.assertEqual(meal_timing_issues(fixed,request),[])
        for old,new in zip(plan.days,fixed.days):
            self.assertEqual([p.item_id for p in old.attractions],[p.item_id for p in new.attractions])
            self.assertEqual([m.model_dump() for m in old.meals],[m.model_dump() for m in new.meals])
            self.assertEqual([s.model_dump() for s in old.schedule.segments],[s.model_dump() for s in new.schedule.segments])
            self.assertEqual(old.visit_order,new.visit_order)
            self.assertEqual((old.start_time,old.end_time,old.buffer_minutes),(new.start_time,new.end_time,new.buffer_minutes))
            self.assertEqual(check_integrity(new,request),[])
            for a,b in zip(old.attractions,new.attractions):
                self.assertGreaterEqual(b.visit_duration,a.visit_duration*.75)
                self.assertEqual(a.location,b.location)
        self.assertIn('162→146',notes[0])

    def test_explicit_duration_custom_meal_clock_strict_mode_and_day_instructions_are_preserved(self):
        plan,request=self.fixture()
        for changes in ({'free_text_input':'故宫停留3小时'}, {'free_text_input':'故宫停留3个小时'}, {'free_text_input':'故宫至少半天'}, {'free_text_input':'午餐15点吃'},
                        {'reference_mode':'strict'}, {'free_text_input':'先吃午餐再去故宫'}):
            fixed,notes=fit_meal_durations(plan,request.model_copy(update=changes))
            self.assertEqual(fixed.model_dump(),plan.model_dump());self.assertEqual(notes,[])

    def test_duration_protection_detects_units_without_confusing_the_attraction_count(self):
        from app.services.duration_fit import explicit_duration_instructions
        for text in ('故宫3个小时','停留三十分钟','玩半天','每处90min左右','参观1.5h后吃饭'):
            self.assertTrue(explicit_duration_instructions(text))
        self.assertFalse(explicit_duration_instructions('每天最多2个景点，正常吃早午晚三餐'))

    def test_required_or_locked_morning_visit_is_not_shortened(self):
        from app.models.schemas import ReferenceCandidate
        for locked in (False,True):
            plan,request=self.fixture()
            if locked:plan.days[0].attractions[0].locked=True
            else:request.reference_candidates=[ReferenceCandidate(id='required',name='故宫博物院',priority='required')]
            fixed,notes=fit_meal_durations(plan,request)
            self.assertEqual(fixed.model_dump(),plan.model_dump());self.assertEqual(notes,[])

    def test_unknown_or_stale_route_structure_is_not_used_to_claim_a_fixed_time(self):
        for mode in ('unknown','changed_place'):
            plan,request=self.fixture()
            if mode=='unknown':plan.days[0].schedule.segments[0].status='unverified'
            else:plan.days[0].attractions[0].name='其他地点'
            fixed,notes=fit_meal_durations(plan,request)
            self.assertEqual(fixed.model_dump(),plan.model_dump());self.assertEqual(notes,[])

    def test_local_search_budget_exhaustion_keeps_the_original(self):
        plan,request=self.fixture()
        with patch('app.services.duration_fit.MAX_SECONDS',0):
            fixed,notes=fit_meal_durations(plan,request)
        self.assertEqual(fixed.model_dump(),plan.model_dump());self.assertEqual(notes,[])


    def test_more_than_thirty_minutes_late_is_not_hidden_by_shortening_a_visit(self):
        from app.services.schedule_validator import build_schedule,stops_for_day,minutes
        plan,request=self.fixture();day=plan.days[0]
        day.attractions[0].visit_duration=180;day.end_time='21:00'
        day.schedule=build_schedule(day,stops_for_day(day),day.schedule.segments,request)
        lunch=next(i for i in day.schedule.items if i.kind=='meal' and i.name=='楚粤楼')
        self.assertGreater(minutes(lunch.start_time),minutes('14:30'))
        self.assertEqual(day.schedule.status,'feasible')
        fixed,notes=fit_meal_durations(plan,request)
        self.assertEqual(fixed.model_dump(),plan.model_dump());self.assertEqual(notes,[])


class LocalFirstMealTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        data=json.loads((Path(__file__).parent/'fixtures/late_meal_duration_failure.json').read_text(encoding='utf-8'))
        from app.models.schemas import TripPlan,TripRequest
        return TripPlan(**data['plan_snapshot']),TripRequest(**data['case_request'])

    async def test_opted_in_top_level_fit_uses_existing_edges_without_querying_new_routes(self):
        from app.services.observability import usage_scope
        from app.services.venue_routing import gate_issues
        plan,request=self.fixture();before=plan.model_dump()
        check=AsyncMock(side_effect=AssertionError('no new route query should be needed'))
        with usage_scope() as usage:
            fixed,notes=await repair_meal_timing(plan,request,check,allow_duration_fit=True,local_first=True)
        check.assert_not_awaited();self.assertTrue(notes)
        self.assertFalse(meal_timing_issues(fixed,request));self.assertEqual(plan.model_dump(),before)
        self.assertEqual(usage['events']['meal_local_fit_days'],1)
        self.assertEqual([s.model_dump() for s in fixed.days[0].schedule.segments],
                         [s.model_dump() for s in plan.days[0].schedule.segments])
        # Numeric historical replay cannot create gate evidence absent at source.
        self.assertEqual(gate_issues(fixed),gate_issues(plan))

    async def test_nested_repair_does_not_shorten_visits_without_local_first_opt_in(self):
        plan,request=self.fixture()
        checker=AsyncMock(side_effect=asyncio.TimeoutError())
        fixed,notes=await repair_meal_timing(plan,request,checker,allow_duration_fit=True)
        self.assertGreater(checker.await_count,0)
        self.assertEqual(fixed.model_dump(),plan.model_dump());self.assertEqual(notes,[])

    async def test_no_duration_permission_or_exhausted_local_search_falls_back_to_route_repair(self):
        for allow,seconds in ((False,1),(True,0)):
            plan,request=self.fixture()
            checker=AsyncMock(side_effect=asyncio.TimeoutError())
            with patch('app.services.duration_fit.MAX_SECONDS',seconds):
                fixed,notes=await repair_meal_timing(plan,request,checker,allow_duration_fit=allow,local_first=True)
            self.assertGreater(checker.await_count,0)
            self.assertEqual(fixed.model_dump(),plan.model_dump());self.assertEqual(notes,[])

    async def test_explicit_user_clocks_and_duration_are_not_shortened_by_local_first(self):
        for text in ('故宫停留3个小时','午餐15点吃','先吃饭再去故宫'):
            plan,request=self.fixture();request.free_text_input=text
            checker=AsyncMock(side_effect=asyncio.TimeoutError())
            fixed,notes=await repair_meal_timing(plan,request,checker,allow_duration_fit=True,local_first=True)
            self.assertEqual(fixed.model_dump(),plan.model_dump());self.assertEqual(notes,[])

    async def test_local_search_does_not_block_other_requests_and_cancellation_does_not_mutate_input(self):
        plan,request=self.fixture();before=plan.model_dump()
        entered,release,finished=threading.Event(),threading.Event(),threading.Event()
        def calculation(p,r):
            entered.set()
            try:release.wait(2);return p.model_copy(deep=True),[]
            finally:finished.set()
        with patch('app.services.meal_timing.fit_meal_durations',side_effect=calculation):
            job=asyncio.create_task(repair_meal_timing(plan,request,AsyncMock(),allow_duration_fit=True,local_first=True))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,1))
                self.assertFalse(job.done())
                # The event loop is still free to serve another request callback.
                response=asyncio.get_running_loop().create_future()
                asyncio.get_running_loop().call_soon(response.set_result,'ready')
                self.assertEqual(await asyncio.wait_for(response,1),'ready')
                job.cancel()
                with self.assertRaises(asyncio.CancelledError):await job
            finally:
                release.set();await asyncio.to_thread(finished.wait,2)
        self.assertEqual(plan.model_dump(),before)
