"""New-trip preparation: spatial screening, time budgets and protected visits."""
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

from langchain_core.messages import AIMessage
from app.models.schemas import TripPlan, PlanPatch, Meal, ReferenceCandidate, ReferencePlace
from app.services.initial_planning import (prepare_regions, planning_advice, fit_suggested_durations,
                                          enforce_initial_windows, check_initial_repair, normalize_initial_repair, _cost)
from app.services.schedule_validator import validate_schedule
from app.agents.travel_agent import TravelAgent
from test_travel_agent import PLAN, REQUEST, FakeModel, make_agent
from test_schedule import sample


def regional_plan():
    raw = copy.deepcopy(PLAN)
    raw['end_date'] = '2026-10-04'
    names = [('西区甲', 120.00), ('东区甲', 120.40), ('西区乙', 120.01), ('东区乙', 120.41)]
    places = []
    for index, (name, lng) in enumerate(names):
        place = copy.deepcopy(raw['days'][0]['attractions'][0])
        place.update(name=name, poi_id=f'p{index}', visit_duration=120,
                     location={'longitude': lng, 'latitude': 30.24})
        places.append(place)
    raw['days'][0]['attractions'] = places[:2]
    second = copy.deepcopy(raw['days'][0])
    second.update(date='2026-10-04', day_index=1, attractions=places[2:])
    raw['days'].append(second)
    return TripPlan(**raw)


class RegionTests(unittest.TestCase):
    def test_grouping_preserves_all_visits_and_reduces_spatial_backtracking(self):
        plan = regional_plan()
        before = plan.model_dump()
        prepared, changed = prepare_regions(plan, REQUEST)
        self.assertEqual(changed, [0, 1])
        self.assertLess(sum(_cost(d.attractions) for d in prepared.days), sum(_cost(d.attractions) for d in plan.days) * .1)
        self.assertEqual([len(d.attractions) for d in prepared.days], [2, 2])
        self.assertEqual(sorted(p.model_dump_json() for d in plan.days for p in d.attractions),
                         sorted(p.model_dump_json() for d in prepared.days for p in d.attractions))
        self.assertEqual(plan.model_dump(), before)
        self.assertTrue(all(d.schedule is None for d in prepared.days))

    def test_specific_day_sequence_and_strict_reference_are_not_reassigned(self):
        plan = regional_plan()
        for text in ['第一天去西区甲', '第1日去西区甲', 'Day 1 去西区甲', '长城单独安排一天', '10月3日去东区甲', '先西区甲再东区甲', '上午去西区甲']:
            request = REQUEST.model_copy(update={'free_text_input': text})
            prepared, changed = prepare_regions(plan, request)
            self.assertEqual(changed, [])
            self.assertEqual(prepared.model_dump(), plan.model_dump())
        request = REQUEST.model_copy(update={'reference_mode': 'strict'})
        self.assertEqual(prepare_regions(plan, request)[1], [])

    def test_missing_coordinates_and_protected_day_are_not_moved(self):
        plan = regional_plan()
        plan.days[0].attractions[0].locked = True
        prepared, changed = prepare_regions(plan, REQUEST)
        self.assertEqual(prepared.days[0], plan.days[0])
        self.assertNotIn(0, changed)
        plan.days[1].attractions[0].location = None
        self.assertEqual(prepare_regions(plan, REQUEST)[1], [])

    def test_named_meals_anchor_the_day_and_mixed_durations_do_not_swap(self):
        plan = regional_plan()
        plan.days[0].meals = [Meal(type='lunch', name='', restaurant='指定午餐')]
        prepared, _ = prepare_regions(plan, REQUEST)
        self.assertEqual(prepared.days[0], plan.days[0])
        plan = regional_plan()
        for place in plan.days[1].attractions:
            place.visit_duration = 180
        self.assertEqual(prepare_regions(plan, REQUEST)[1], [])

    def test_budget_reserves_meals_buffers_and_unverified_travel(self):
        advice = planning_advice(REQUEST)
        self.assertEqual(advice['available_minutes'], 660)
        self.assertEqual(advice['visit_budgets'][1]['suggested_total_visit_minutes'], 288)
        self.assertIn('不是真实耗时', advice['note'])
        plan = regional_plan()
        plan.days[0].end_time, plan.days[0].buffer_minutes = '23:00', 0
        enforce_initial_windows(plan, REQUEST)
        self.assertEqual((plan.days[0].end_time, plan.days[0].buffer_minutes), ('20:00', 15))


class FitTests(unittest.IsolatedAsyncioTestCase):
    async def test_opted_in_opening_fit_handles_closing_without_a_daily_overrun(self):
        plan = sample(1)
        day = plan.days[0]
        day.start_time, day.end_time = '15:30', '20:00'
        day.attractions[0].visit_duration = 120
        day.attractions[0].open_hours = '09:00-17:00'
        async def validator(candidate, request, progress=None):
            return await validate_schedule(candidate, request, AsyncMock(return_value={'duration_minutes': 0, 'distance_meters': 0}))
        await validator(plan, REQUEST)
        self.assertEqual(day.schedule.status, 'conflict')
        self.assertLess(day.schedule.minimum_minutes, day.schedule.available_minutes)
        before = plan.model_dump()
        unchanged, _ = await fit_suggested_durations(plan, REQUEST, validator)
        self.assertEqual(unchanged.model_dump(), before)
        fixed, notes = await fit_suggested_durations(plan, REQUEST, validator, allow_opening_fit=True)
        self.assertEqual(fixed.days[0].schedule.status, 'feasible')
        self.assertEqual(fixed.days[0].attractions[0].visit_duration, 90)
        self.assertTrue(notes)
        self.assertEqual(plan.model_dump(), before)
        day.attractions[0].locked = True
        protected, _ = await fit_suggested_durations(plan, REQUEST, validator, allow_opening_fit=True)
        self.assertEqual(protected.model_dump(), plan.model_dump())

    async def conflicted(self):
        plan = sample()
        for attraction in plan.days[0].attractions:
            attraction.visit_duration = 120
        plan.days[0].end_time = '13:00'
        provider = AsyncMock(return_value={'duration_minutes': 20, 'distance_meters': 1200})
        async def validator(partial, request, progress=None):
            return await validate_schedule(partial, request, provider, progress)
        await validator(plan, REQUEST)
        return plan, validator

    async def test_small_overrun_can_fit_without_dropping_places_or_window_changes(self):
        plan, validator = await self.conflicted()
        before = plan.model_dump()
        result, notes = await fit_suggested_durations(plan, REQUEST, validator)
        self.assertEqual(result.days[0].schedule.status, 'feasible')
        self.assertEqual(len(result.days[0].attractions), 2)
        self.assertEqual(result.days[0].end_time, '13:00')
        self.assertEqual(result.days[0].buffer_minutes, 15)
        self.assertTrue(all(p.visit_duration >= 90 for p in result.days[0].attractions))
        self.assertEqual(result.days[0].schedule.minimum_minutes, 230)
        self.assertIn('120→', notes[0])
        self.assertEqual(plan.model_dump(), before)

    async def test_required_locked_and_fixed_visits_never_shorten(self):
        plan, validator = await self.conflicted()
        a, b = plan.days[0].attractions
        a.locked, b.fixed_start_time = True, '12:00'
        self.assertEqual((await fit_suggested_durations(plan, REQUEST, validator))[1], [])
        a.locked, b.fixed_start_time = False, None
        request = REQUEST.model_copy(update={'reference_candidates': [ReferenceCandidate(
            id='must', name=a.name, priority='required', status='matched', source_ids=['s'],
            place=ReferencePlace(name=a.name, address='', longitude=120.15, latitude=30.24))]})
        self.assertEqual((await fit_suggested_durations(plan, request, validator))[1], [])

    async def test_unknown_traffic_explicit_duration_and_large_overrun_do_not_shorten(self):
        plan, validator = await self.conflicted()
        request = REQUEST.model_copy(update={'free_text_input': '每个景点参观2小时'})
        self.assertEqual((await fit_suggested_durations(plan, request, validator))[1], [])
        request = REQUEST.model_copy(update={'reference_mode': 'strict'})
        self.assertEqual((await fit_suggested_durations(plan, request, validator))[1], [])
        plan.days[0].schedule.segments[0].status = 'unverified'
        self.assertEqual((await fit_suggested_durations(plan, REQUEST, validator))[1], [])
        plan.days[0].schedule.segments[0].status = 'verified'
        plan.days[0].schedule.minimum_minutes = 1000
        self.assertEqual((await fit_suggested_durations(plan, REQUEST, validator))[1], [])

    async def test_failed_revalidation_keeps_original_durations(self):
        plan, _ = await self.conflicted()
        async def fail(candidate, request, progress=None):
            await validate_schedule(candidate, request, AsyncMock(side_effect=RuntimeError('offline')))
        result, notes = await fit_suggested_durations(plan, REQUEST, fail)
        self.assertEqual(notes, [])
        self.assertEqual(result, plan)

    async def test_new_repair_cannot_extend_window_drop_meals_or_touch_other_dates(self):
        plan, _ = await self.conflicted()
        for operation in [dict(action='update_day', date=plan.days[0].date, end_time='23:00'),
                          dict(action='remove', kind='meal', date=plan.days[0].date, item_id='meal'),
                          dict(action='remove', kind='attraction', date='2026-10-04', item_id='x')]:
            with self.assertRaises(ValueError):
                check_initial_repair(PlanPatch(operations=[operation]), plan)

    async def test_partial_repair_does_not_hide_another_days_conflict(self):
        plan, _ = await self.conflicted()
        second = plan.days[0].model_copy(deep=True)
        second.date, second.day_index = '2026-10-04', 1
        plan.days.append(second)
        plan.days[0].schedule.conflicts = []
        partial = plan.model_copy(update={'days': [plan.days[0]]})
        result = TravelAgent._settle_conflicts({}, plan, partial, '已修好', REQUEST)
        self.assertEqual(result['status'], 'retry')
        self.assertIn('第2天', result['messages'][0].content)

    async def test_existing_trip_question_does_not_invoke_new_trip_preparation(self):
        model = FakeModel([AIMessage(content=json.dumps({'changed': False, 'reply': '现有安排', 'plan': None}))])
        with patch('app.agents.travel_agent.prepare_regions', side_effect=AssertionError('existing trip')), \
             patch('app.agents.travel_agent.repair_meal_windows', side_effect=AssertionError('existing trip')), \
             patch('app.agents.travel_agent.fit_suggested_durations', side_effect=AssertionError('existing trip')):
            plan, _, changed, _ = await make_agent(model).run(REQUEST, current_plan=regional_plan(), user_message='有什么建议')
        self.assertFalse(changed)

    async def test_new_generation_fits_with_two_model_calls_and_no_rewrite(self):
        draft = sample().model_dump()
        for attraction in draft['days'][0]['attractions']:
            attraction['visit_duration'] = 120
        # The model cannot silently extend the user's four-hour window.
        draft['days'][0]['end_time'] = '23:00'
        request = REQUEST.model_copy(update={'daily_end_time': '13:00'})
        pois = [{'poi_id': f'p{i}', 'name': a['name'], 'address': a['address'],
                 'coordinates': [a['location']['longitude'], a['location']['latitude']]}
                for i, a in enumerate(draft['days'][0]['attractions'])]
        model = FakeModel([
            AIMessage(content='', tool_calls=[{'name': 'search_attractions', 'args': {'keyword': '公园'}, 'id': 'search'}]),
            AIMessage(content=json.dumps({'changed': True, 'reply': '初稿', 'plan': draft}, ensure_ascii=False)),
        ])
        agent = make_agent(model)
        async def checker(plan, request, progress=None):
            return await validate_schedule(plan, request, AsyncMock(return_value={'duration_minutes': 20, 'distance_meters': 1200}))
        agent._check_schedule = checker
        with patch('app.agents.travel_agent.search_pois', new=AsyncMock(return_value=pois)):
            plan, reply, _, _ = await agent.run(request)
        self.assertEqual(model.calls, 2)
        self.assertEqual(plan.days[0].end_time, '13:00')
        self.assertEqual(plan.days[0].schedule.status, 'feasible')
        self.assertEqual(len(plan.days[0].attractions), 2)
        self.assertIn('调整建议停留', reply)

    async def test_region_assignment_happens_before_matching_meals(self):
        draft = regional_plan()
        pois = [{'poi_id': p.poi_id, 'name': p.name, 'address': p.address,
                 'coordinates': [p.location.longitude, p.location.latitude]}
                for d in draft.days for p in d.attractions]
        model = FakeModel([
            AIMessage(content='', tool_calls=[{'name': 'search_attractions', 'args': {'keyword': '公园'}, 'id': 'search'}]),
            AIMessage(content=json.dumps({'changed': True, 'reply': '初稿', 'plan': draft.model_dump()}, ensure_ascii=False)),
        ])
        agent = make_agent(model)
        async def match_meals(plan, **kwargs):
            # Meal search sees grouped regions, not the original cross-town pair.
            self.assertTrue(all(abs(d.attractions[0].location.longitude - d.attractions[1].location.longitude) < .02 for d in plan.days))
        agent.planner._align_meals_to_route = match_meals
        request = REQUEST.model_copy(update={'travel_days': 2, 'end_date': draft.end_date})
        with patch('app.agents.travel_agent.search_pois', new=AsyncMock(return_value=pois)):
            plan, *_ = await agent.run(request)
        self.assertEqual(model.calls, 2)
        self.assertEqual(len(plan.days), 2)

    async def test_reorder_filters_only_explicit_prior_removals(self):
        from app.services.plan_patch import apply_patch
        plan = regional_plan()
        a, b = plan.days[0].attractions
        patch_ = PlanPatch(operations=[{'action': 'remove', 'date': plan.days[0].date, 'item_id': a.item_id},
                                      {'action': 'reorder', 'date': plan.days[0].date, 'order': [a.item_id, b.item_id]}])
        result, _ = apply_patch(plan, normalize_initial_repair(patch_), {})
        self.assertEqual([p.item_id for p in result.days[0].attractions], [b.item_id])
        self.assertEqual(patch_.operations[1].order, [a.item_id, b.item_id])
        patch_.operations[1].order.append('invented')
        with self.assertRaises(ValueError):
            apply_patch(plan, normalize_initial_repair(patch_), {})

    async def test_invalid_repairs_return_last_checked_draft_without_applying_changes(self):
        draft = sample().model_dump()
        pois = [{'poi_id': f'p{i}', 'name': a['name'], 'address': a['address'],
                 'coordinates': [a['location']['longitude'], a['location']['latitude']]}
                for i, a in enumerate(draft['days'][0]['attractions'])]
        invalid = AIMessage(content=json.dumps({'changed': True, 'reply': '修好了', 'patch': {'operations': [
            {'action': 'reorder', 'date': draft['days'][0]['date'], 'order': ['invented']}]}}))
        model = FakeModel([
            AIMessage(content='', tool_calls=[{'name': 'search_attractions', 'args': {'keyword': '公园'}, 'id': 'search'}]),
            AIMessage(content=json.dumps({'changed': True, 'reply': '初稿', 'plan': draft}, ensure_ascii=False)), invalid, invalid])
        agent = make_agent(model)
        async def checker(plan, request, progress=None):
            return await validate_schedule(plan, request, AsyncMock(return_value={'duration_minutes': 400, 'distance_meters': 1200}))
        agent._check_schedule = checker
        request = REQUEST.model_copy(update={'daily_end_time': '13:00', 'free_text_input': '保留全部景点'})
        with patch('app.agents.travel_agent.search_pois', new=AsyncMock(return_value=pois)):
            plan, reply, *_ = await agent.run(request)
        self.assertEqual(plan.days[0].schedule.status, 'conflict')
        self.assertEqual(len(plan.days[0].attractions), 2)
        self.assertIn('自动修复未通过', reply)
        self.assertEqual(model.calls, 4)

    async def test_even_day_lunch_search_uses_last_visited_attraction(self):
        from app.agents.langgraph_planner import LangGraphTripPlanner
        planner = LangGraphTripPlanner.__new__(LangGraphTripPlanner)
        for count in (2, 4):
            plan = sample(count)
            with patch('app.agents.langgraph_planner.search_nearby_restaurants', new=AsyncMock(return_value=[])) as nearby:
                await planner._align_meals_to_route(plan)
            longitude = nearby.await_args_list[1].args[0]  # first round: breakfast, lunch, dinner
            self.assertEqual(longitude, plan.days[0].attractions[(count-1)//2].location.longitude)

    async def test_final_fallback_returns_the_checked_plan_after_model_repair_failure(self):
        draft = sample().model_dump()
        pois = [{'poi_id': f'p{i}', 'name': a['name'], 'address': a['address'],
                 'coordinates': [a['location']['longitude'], a['location']['latitude']]}
                for i, a in enumerate(draft['days'][0]['attractions'])]
        invalid = AIMessage(content=json.dumps({'changed': True, 'reply': '修好了', 'patch': {'operations': [
            {'action': 'reorder', 'date': draft['days'][0]['date'], 'order': ['invented']}]}}))
        model = FakeModel([
            AIMessage(content='', tool_calls=[{'name': 'search_attractions', 'args': {'keyword': '公园'}, 'id': 'search'}]),
            AIMessage(content=json.dumps({'changed': True, 'reply': '初稿', 'plan': draft}, ensure_ascii=False)), invalid, invalid])
        agent = make_agent(model)
        async def checker(plan, request, progress=None):
            return await validate_schedule(plan, request, AsyncMock(return_value={'duration_minutes': 400, 'distance_meters': 1200}))
        agent._check_schedule = checker
        request = REQUEST.model_copy(update={'daily_end_time': '13:00'})
        with patch('app.agents.travel_agent.search_pois', new=AsyncMock(return_value=pois)):
            plan, reply, *_ = await agent.run(request)
        self.assertEqual(plan.days[0].schedule.status, 'feasible')
        self.assertEqual(len(plan.days[0].attractions), 1)
        self.assertEqual(plan.days[0].attractions[0].name, draft['days'][0]['attractions'][0]['name'])
        self.assertIn('备选', reply)
        self.assertIn('备选', plan.overall_suggestions)
        self.assertNotIn('仍有时间冲突', reply)
        self.assertEqual(model.calls, 4)


if __name__ == '__main__':
    unittest.main()
