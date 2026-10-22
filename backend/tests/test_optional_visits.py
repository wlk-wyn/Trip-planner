"""A real-route feasible fallback must preserve intent and retained visits."""
import unittest
from unittest.mock import AsyncMock

from app.models.schemas import ReferenceCandidate, Meal, Hotel
from app.services.initial_planning import fit_optional_visits
from app.services.schedule_validator import validate_schedule
from test_schedule import sample
from test_travel_agent import REQUEST


class OptionalVisitTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self, count=3):
        plan = sample(count)
        day = plan.days[0]
        day.start_time, day.end_time, day.buffer_minutes = '09:00', '13:00', 15
        async def validator(partial, request, progress=None):
            return await validate_schedule(partial, request,
                AsyncMock(return_value={'duration_minutes': 20, 'distance_meters': 1000}))
        await validator(plan, REQUEST)
        return plan, validator

    async def test_only_accepts_a_feasible_checked_copy_and_explains_removed_place(self):
        plan, validator = await self.fixture()
        before = plan.model_dump()
        result, notes = await fit_optional_visits(plan, REQUEST, validator)
        self.assertEqual(plan.model_dump(), before)
        day = result.days[0]
        self.assertEqual(day.schedule.status, 'feasible')
        self.assertEqual(len(day.attractions), 2)
        self.assertEqual((day.start_time, day.end_time, day.buffer_minutes), ('09:00', '13:00', 15))
        self.assertTrue(all(p in plan.days[0].attractions for p in day.attractions))
        removed = next(p for p in plan.days[0].attractions if p not in day.attractions)
        self.assertIn(removed.name, notes[0])
        self.assertIn('备选', notes[0])

    async def test_named_required_locked_fixed_and_planned_visits_are_preserved(self):
        for mode in ('named', 'required', 'locked', 'fixed', 'planned'):
            with self.subTest(mode=mode):
                plan, validator = await self.fixture()
                protected = plan.days[0].attractions[0]
                request = REQUEST.model_copy(deep=True)
                if mode == 'named': request.free_text_input = f'想去{protected.name}'
                if mode == 'required': request.reference_candidates = [ReferenceCandidate(id='required-visit', name=protected.name, priority='required')]
                if mode == 'locked': protected.locked = True
                if mode == 'fixed': protected.fixed_start_time = '09:00'
                if mode == 'planned': plan.days[0].planned_start_times[protected.item_id] = '09:00'
                result, _ = await fit_optional_visits(plan, request, validator)
                self.assertIn(protected, result.days[0].attractions)

    async def test_meals_hotel_and_retained_route_order_are_unchanged(self):
        plan, validator = await self.fixture()
        day = plan.days[0]
        location = day.attractions[0].location
        day.meals = [Meal(type='lunch', name='', restaurant='指定午餐', location=location)]
        day.hotel = Hotel(name='原住宿', location=location)
        day.end_time = '15:30'
        day.identify_visits()
        await validator(plan, REQUEST)
        day.visit_order = [i.id for i in day.schedule.items]
        before = day.model_dump()
        result, notes = await fit_optional_visits(plan, REQUEST, validator)
        self.assertTrue(notes)
        fitted = result.days[0]
        self.assertEqual(fitted.meals, day.meals)
        self.assertEqual(fitted.hotel, day.hotel)
        removed = next(p.item_id for p in day.attractions if p not in fitted.attractions)
        self.assertEqual(fitted.visit_order, [i for i in day.visit_order if i != removed])
        self.assertEqual(day.model_dump(), before)

    async def test_main_visit_is_not_discarded_to_erase_its_long_commute(self):
        plan, validator = await self.fixture()
        main = plan.days[0].attractions[0]
        main.visit_duration = 120
        plan.days[0].end_time = '13:10'
        await validator(plan, REQUEST)
        result, notes = await fit_optional_visits(plan, REQUEST, validator)
        self.assertTrue(notes)
        self.assertIn(main, result.days[0].attractions)
        # If the main visit alone cannot fit, retain the honest conflict.
        main.visit_duration = 400
        await validator(plan, REQUEST)
        result, notes = await fit_optional_visits(plan, REQUEST, validator)
        self.assertEqual(result, plan)
        self.assertEqual(notes, [])

    async def test_never_removes_only_visit_or_reinterprets_explicit_day_or_count(self):
        plan, validator = await self.fixture()
        for text in ('第一天去公园', '每天至少3个景点', '所有景点都保留'):
            checked = AsyncMock()
            result, notes = await fit_optional_visits(plan, REQUEST.model_copy(update={'free_text_input': text}), checked)
            self.assertEqual(result, plan)
            self.assertEqual(notes, [])
            checked.assert_not_awaited()
        plan.days[0].attractions = plan.days[0].attractions[:1]
        checked = AsyncMock()
        await fit_optional_visits(plan, REQUEST, checked)
        checked.assert_not_awaited()

    async def test_unknown_new_route_failure_or_remaining_conflict_keeps_original(self):
        plan, _ = await self.fixture()
        async def unknown(partial, request, progress=None):
            return await validate_schedule(partial, request, AsyncMock(side_effect=RuntimeError('offline')))
        async def slow(partial, request, progress=None):
            return await validate_schedule(partial, request,
                AsyncMock(return_value={'duration_minutes': 300, 'distance_meters': 1000}))
        for validator in (unknown, slow, AsyncMock(side_effect=RuntimeError('unavailable'))):
            result, notes = await fit_optional_visits(plan, REQUEST, validator)
            self.assertEqual(result, plan)
            self.assertEqual(notes, [])

    async def test_bounded_checks_and_no_tampered_timeline_acceptance(self):
        plan, _ = await self.fixture()
        second = plan.days[0].model_copy(deep=True)
        second.date, second.day_index = '2026-10-04', 1
        plan.days.append(second)
        async def lie(partial, request, progress=None):
            partial.days[0].schedule = plan.days[0].schedule.model_copy(update={'status': 'feasible', 'conflicts': []})
        validator = AsyncMock(side_effect=lie)
        result, notes = await fit_optional_visits(plan, REQUEST, validator)
        self.assertEqual(validator.await_count, 3)
        self.assertEqual(result, plan)
        self.assertEqual(notes, [])


if __name__ == '__main__':
    unittest.main()
