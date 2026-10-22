import unittest
from unittest.mock import AsyncMock
from app.models.schemas import Meal

from app.services.evening_planning import wants_evening, evening_coverage, evening_issues, repair_evening, prepare_evening
from app.services.plan_checks import run_checks
from app.services.schedule_validator import validate_schedule
from test_schedule import sample
from test_travel_agent import REQUEST


class EveningTests(unittest.IsolatedAsyncioTestCase):
    def request(self, text='想看夜景，少爬坡'):
        return REQUEST.model_copy(update={'free_text_input': text, 'preferences': []})

    async def plan(self):
        plan = sample(2)
        place = plan.days[0].attractions[-1]
        place.name = '滨江观景平台'
        place.category = '风景名胜'
        place.description = '沿江散步，看城市夜景'
        await self.check(plan, self.request())
        return plan

    async def check(self, plan, request, progress=None):
        return await validate_schedule(plan, request, AsyncMock(return_value={'duration_minutes': 10, 'distance_meters': 500}))

    def test_request_only_with_negations_and_optional_choices(self):
        for text in ('不想看夜景', '不要安排夜游', '有时间再逛夜市', '不看夜景也不去夜市', '不用安排夜景', '取消夜游'):
            self.assertFalse(wants_evening(self.request(text)), text)
        self.assertTrue(wants_evening(self.request('不看夜景，但想逛夜市')))
        request = self.request('喜欢历史').model_copy(update={'reference_content': '推荐夜景', 'preferences': []})
        self.assertFalse(wants_evening(request))

    async def test_description_does_not_prove_evening_and_rule_exposes_it(self):
        plan = await self.plan()
        self.assertFalse(evening_coverage(plan, self.request()))
        self.assertIn('evening_preference_unmet', [i['code'] for i in run_checks(plan, self.request())])
        self.assertEqual(evening_issues(plan, self.request('不想看夜景')), [])

    async def test_repair_preserves_places_and_windows_and_checks_actual_routes(self):
        plan = await self.plan(); before = plan.model_dump()
        fixed, notes = await repair_evening(plan, self.request(), self.check)
        self.assertTrue(notes)
        self.assertTrue(evening_coverage(fixed, self.request()))
        self.assertEqual(fixed.days[0].schedule.status, 'feasible')
        self.assertEqual(fixed.days[0].schedule.items[-1].start_time, '18:00')
        self.assertEqual([p.item_id for p in fixed.days[0].attractions], [p.item_id for p in plan.days[0].attractions])
        self.assertEqual(fixed.days[0].end_time, plan.days[0].end_time)
        self.assertEqual(plan.model_dump(), before)

    async def test_failed_or_closed_or_overtime_candidate_is_not_adopted(self):
        for mode in ('closed', 'short_window', 'network', 'unknown_route'):
            plan = await self.plan()
            if mode == 'closed': plan.days[0].attractions[-1].open_hours = '09:00-17:00'
            if mode == 'short_window': plan.days[0].end_time = '18:15'
            before = plan.model_dump()
            validator = self.check
            if mode == 'network': validator = AsyncMock(side_effect=RuntimeError('offline'))
            if mode == 'unknown_route':
                async def validator(p, r, progress):
                    await validate_schedule(p, r, AsyncMock(side_effect=RuntimeError('offline')))
            fixed, notes = await repair_evening(plan, self.request(), validator)
            self.assertEqual(notes, [], mode)
            self.assertEqual(fixed.model_dump(), before, mode)

    async def test_locked_fixed_and_explicit_order_are_preserved(self):
        for mode in ('locked', 'fixed', 'order', 'user_order'):
            plan = await self.plan(); request = self.request()
            if mode == 'locked': plan.days[0].attractions[0].locked = True
            if mode == 'fixed': plan.days[0].attractions[0].fixed_start_time = '10:00'
            if mode == 'order': plan.days[0].visit_order = [s.id for s in plan.days[0].schedule.items]
            if mode == 'user_order': request = self.request('先看夜景，再去吃晚饭')
            validator = AsyncMock()
            fixed, notes = await repair_evening(plan, request, validator)
            validator.assert_not_called()
            self.assertEqual(notes, [])
            self.assertEqual(fixed.model_dump(), plan.model_dump())

    async def test_missing_or_stale_timeline_cannot_prove_coverage(self):
        plan = await self.plan()
        fixed, _ = await repair_evening(plan, self.request(), self.check)
        fixed.days[0].attractions[-1].visit_duration += 10
        self.assertIsNone(evening_coverage(fixed, self.request()))
        fixed.days[0].schedule = None
        self.assertIsNone(evening_coverage(fixed, self.request()))

    async def test_no_night_candidate_means_no_invented_museum_night_visit(self):
        plan = await self.plan()
        plan.days[0].attractions[-1].name = '城市博物馆'
        validator = AsyncMock()
        _, notes = await repair_evening(plan, self.request(), validator)
        validator.assert_not_called()
        self.assertEqual(notes, [])

    async def test_success_is_idempotent(self):
        plan = await self.plan()
        fixed, _ = await repair_evening(plan, self.request(), self.check)
        validator = AsyncMock()
        again, notes = await repair_evening(fixed, self.request(), validator)
        validator.assert_not_called()
        self.assertEqual(notes, [])
        self.assertEqual(again.model_dump(), fixed.model_dump())

    async def test_night_visit_cannot_be_achieved_by_serving_dinner_mid_afternoon(self):
        plan = await self.plan()
        day = plan.days[0]
        day.meals = [Meal(name='江畔餐厅', restaurant='江畔餐厅', type='dinner', location=day.attractions[0].location)]
        await self.check(plan, self.request())
        fixed, notes = await repair_evening(plan, self.request(), self.check)
        self.assertTrue(notes)
        dinner = next(i for i in fixed.days[0].schedule.items if i.kind == 'meal')
        self.assertEqual(dinner.start_time, '17:00')
        self.assertTrue(evening_coverage(fixed, self.request()))

    async def test_initial_layout_sets_night_anchor_before_meals_and_encodes_actual_clock(self):
        plan = await self.plan()
        day = plan.days[0]
        day.attractions.reverse()
        night_id = day.attractions[0].item_id
        day.schedule = None
        before = plan.model_dump()
        prepared = prepare_evening(plan, self.request())
        self.assertEqual(prepared.days[0].attractions[-1].item_id, night_id)
        self.assertEqual(prepared.days[0].planned_start_times, {})
        prepared.days[0].meals = [Meal(name='晚餐', restaurant='晚餐', type='dinner', location=day.attractions[0].location)]
        prepared = prepare_evening(prepared, self.request(), with_meals=True)
        await self.check(prepared, self.request())
        self.assertTrue(evening_coverage(prepared, self.request()))
        items = prepared.days[0].schedule.items
        self.assertLess(next(i for i,s in enumerate(items) if s.kind == 'meal'), next(i for i,s in enumerate(items) if s.id == night_id))
        self.assertEqual(plan.model_dump(), before)

    async def test_preparation_does_not_touch_explicit_day_instructions_or_locked_visits(self):
        plan = await self.plan()
        for request in (self.request('第一天晚上看夜景'), self.request('先看夜景，再吃饭')):
            self.assertEqual(prepare_evening(plan, request).model_dump(), plan.model_dump())
        plan.days[0].attractions[0].locked = True
        self.assertEqual(prepare_evening(plan, self.request()).model_dump(), plan.model_dump())

    async def test_multiple_candidates_keep_the_same_dinner_anchor_between_preparation_phases(self):
        plan = await self.plan()
        plan.days[0].attractions[0].description = '江边夜景'
        plan.days[0].attractions[0].category = '风景名胜'
        plan.days[0].attractions[0].name = '江边步道'
        prepared = prepare_evening(plan, self.request())
        anchor = prepared.days[0].attractions[-1].item_id
        timed = prepare_evening(prepared, self.request(), with_meals=True)
        self.assertEqual(timed.days[0].attractions[-1].item_id, anchor)
        self.assertIn(anchor, timed.days[0].planned_start_times)

    async def test_invalid_clock_in_a_conflicted_timeline_does_not_crash_checks(self):
        plan = await self.plan()
        plan.days[0].schedule.status = 'conflict'
        plan.days[0].schedule.items[-1].start_time = 'bad clock'
        self.assertIsNone(evening_coverage(plan, self.request()))
