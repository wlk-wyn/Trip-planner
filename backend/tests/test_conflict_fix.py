"""Conflict repair proposals: validated, and never touching protected visits."""
import unittest

from app.models.schemas import DaySchedule, ReferenceCandidate, ReferencePlace, Meal
from app.services.schedule_validator import stops_for_day
from app.services.conflict_fix import propose_fixes
from test_travel_agent import REQUEST
from test_weather_and_arrange import two_day_plan

WINDOW = 300  # minutes available in the fake day


def schedule(day):
    used = sum(a.visit_duration for a in day.attractions) + 60
    conflicts = [f"超出{used - WINDOW}分钟"] if used > WINDOW else []
    return DaySchedule(status="conflict" if conflicts else "feasible", window_start="09:00", window_end="14:00",
                       minimum_minutes=used, available_minutes=WINDOW, conflicts=conflicts)


async def fake_validator(plan, request):
    for day in plan.days:
        day.schedule = schedule(day)


def conflicted_plan(durations=(200, 120)):
    plan = two_day_plan()
    day = plan.days[0]
    for place, minutes in zip(day.attractions, durations):
        place.visit_duration = minutes
    day.schedule = schedule(day)
    return plan


class ConflictFixTests(unittest.IsolatedAsyncioTestCase):
    async def test_removal_preserves_retained_meal_and_attraction_order(self):
        plan = conflicted_plan((60, 60))
        day = plan.days[0]
        day.date = '2026-10-19'
        day.attractions[0].open_hours = '周一闭馆'
        day.meals = [Meal(type='lunch', name='午餐', restaurant='合成午餐店', address='测试地址')]
        original_order = [s['id'] for s in stops_for_day(day)]
        removed = day.attractions[0].item_id
        before = plan.model_dump()
        async def validate(candidate, request):
            retained = candidate.days[0]
            self.assertEqual([s['id'] for s in stops_for_day(retained)], [i for i in original_order if i != removed])
            retained.schedule = schedule(retained)
        proposals = await propose_fixes(plan, REQUEST, 0, validate)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]['day']['visit_order'], [i for i in original_order if i != removed])
        self.assertEqual(plan.model_dump(), before)

    async def test_shortening_preserves_explicit_custom_order_without_reusing_old_clocks(self):
        plan = conflicted_plan()
        day = plan.days[0]
        day.identify_visits()
        day.visit_order = [a.item_id for a in reversed(day.attractions)]
        day.planned_start_times = {day.attractions[0].item_id: '10:00'}
        order = list(day.visit_order)
        async def validate(candidate, request):
            changed = candidate.days[0]
            expected = [i for i in order if i in {a.item_id for a in changed.attractions}]
            self.assertEqual(changed.visit_order, expected)
            self.assertEqual(changed.planned_start_times, {})
            changed.schedule = schedule(changed)
        proposals = await propose_fixes(plan, REQUEST, 0, validate)
        shortened = next(p for p in proposals if p['kind'] == 'shorten')
        self.assertEqual(shortened['day']['visit_order'], order)
        self.assertEqual(day.planned_start_times, {day.attractions[0].item_id: '10:00'})

    async def test_stale_feasible_schedule_can_offer_closed_place_removal(self):
        plan = conflicted_plan((60, 60))
        day = plan.days[0]
        day.date = '2026-10-19'
        day.attractions[0].open_hours = '周一闭馆；周二至周日09:00-17:00'
        before = plan.model_dump()
        proposals = await propose_fixes(plan, REQUEST, 0, fake_validator)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]['kind'], 'remove')
        self.assertIn('当天闭馆', proposals[0]['detail'])
        self.assertTrue(proposals[0]['feasible'])
        self.assertEqual(len(proposals[0]['day']['attractions']), 1)
        self.assertEqual(plan.model_dump(), before)

    async def test_closed_protected_place_is_not_removed_or_compensated_by_shorter_visits(self):
        plan = conflicted_plan()
        day = plan.days[0]
        day.date = '2026-10-19'
        day.attractions[0].open_hours = '周一闭馆'
        day.attractions[0].locked = True
        self.assertEqual(await propose_fixes(plan, REQUEST, 0, fake_validator), [])

    async def test_remaining_closure_prevents_false_feasible_proposal(self):
        plan = conflicted_plan((60, 60))
        day = plan.days[0]
        day.date = '2026-10-19'
        for place in day.attractions:
            place.open_hours = '周一闭馆'
        proposals = await propose_fixes(plan, REQUEST, 0, fake_validator)
        self.assertTrue(proposals)
        for proposal in proposals:
            self.assertFalse(proposal['feasible'])
            self.assertEqual(proposal['day']['schedule']['status'], 'conflict')
            self.assertTrue(proposal['conflicts'])

    async def test_feasible_proposals_come_first_and_are_validated(self):
        proposals = await propose_fixes(conflicted_plan(), REQUEST, 0, fake_validator)
        self.assertTrue(proposals)
        self.assertTrue(proposals[0]["feasible"])
        kinds = {p["kind"] for p in proposals}
        self.assertEqual(kinds, {"remove", "shorten"})
        shorten = next(p for p in proposals if p["kind"] == "shorten")
        self.assertTrue(all(a["visit_duration"] >= 45 for a in shorten["day"]["attractions"]))

    async def test_locked_fixed_and_required_visits_are_never_changed(self):
        plan = conflicted_plan()
        museum, park = plan.days[0].attractions
        museum.locked = True
        park.fixed_start_time = "10:00"
        self.assertEqual(await propose_fixes(plan, REQUEST, 0, fake_validator), [])
        plan = conflicted_plan()
        place = ReferencePlace(name="西湖博物馆", address="", longitude=120.15, latitude=30.24)
        request = REQUEST.model_copy(update={"reference_candidates": [ReferenceCandidate(id="c1", name="西湖博物馆", priority="required", status="matched", place=place, source_ids=["s"])]})
        removed = [p["title"] for p in await propose_fixes(plan, request, 0, fake_validator) if p["kind"] == "remove"]
        self.assertEqual(removed, ["去掉「太子湾公园」"])

    async def test_day_without_conflict_has_no_proposals(self):
        plan = conflicted_plan((60, 60))
        self.assertEqual(await propose_fixes(plan, REQUEST, 0, fake_validator), [])


if __name__ == "__main__":
    unittest.main()
