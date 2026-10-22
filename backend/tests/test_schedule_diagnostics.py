"""Budget explanations must agree with the timeline and preserve unknowns."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from app.services.schedule_diagnostics import diagnose_day
from app.services.schedule_validator import validate_schedule
from app.agents.travel_agent import TravelAgent
from test_schedule import sample
from test_travel_agent import REQUEST


NOW = datetime(2026, 10, 16, 4, tzinfo=timezone.utc)


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def checked(self):
        plan = sample()
        await validate_schedule(plan, REQUEST, AsyncMock(return_value={"duration_minutes": 25, "distance_meters": 1800}))
        return plan

    async def test_breakdown_adds_up_to_validated_time(self):
        plan = await self.checked()
        before = plan.model_dump()
        report = diagnose_day(plan.days[0], REQUEST, NOW)
        self.assertEqual(sum(report[key] for key in ('visit_minutes', 'meal_minutes', 'travel_minutes', 'buffer_minutes', 'waiting_minutes')), 220)
        self.assertEqual(report['remaining_minutes'], 440)
        self.assertEqual(report['longest_leg']['duration_minutes'], 25)
        self.assertEqual(plan.model_dump(), before)

    async def test_missing_route_has_no_free_time_or_zero_duration_claim(self):
        plan = sample()
        await validate_schedule(plan, REQUEST, AsyncMock(side_effect=RuntimeError('offline')))
        report = diagnose_day(plan.days[0], REQUEST, NOW)
        self.assertIsNone(report['remaining_minutes'])
        self.assertIsNone(report['longest_leg'])
        self.assertEqual(report['missing_routes'], 1)
        self.assertIn('暂不能', report['headline'])

    async def test_wait_for_fixed_appointment_is_not_free_time(self):
        plan = sample()
        plan.days[0].attractions[1].fixed_start_time = '14:00'
        await validate_schedule(plan, REQUEST, AsyncMock(return_value={"duration_minutes": 25, "distance_meters": 1800}))
        report = diagnose_day(plan.days[0], REQUEST, NOW)
        self.assertEqual(report['waiting_minutes'], 170)
        self.assertEqual(report['remaining_minutes'], 270)

    async def test_appointment_conflict_does_not_claim_remaining_free_time(self):
        plan = sample()
        plan.days[0].attractions[1].fixed_start_time = '10:00'
        await validate_schedule(plan, REQUEST, AsyncMock(return_value={"duration_minutes": 25, "distance_meters": 1800}))
        report = diagnose_day(plan.days[0], REQUEST, NOW)
        self.assertEqual(report['overrun_minutes'], 0)
        self.assertIsNone(report['remaining_minutes'])
        self.assertIn('预约', report['headline'])

    async def test_stale_missing_invalid_and_recent_timestamps(self):
        plan = await self.checked()
        leg = plan.days[0].schedule.segments[0]
        leg.checked_at = (NOW - timedelta(hours=25)).isoformat()
        report = diagnose_day(plan.days[0], REQUEST, NOW)
        self.assertEqual((report['stale_routes'], report['unknown_age_routes']), (1, 0))
        for stamp in [None, 'invalid', '2026-10-15T10:00:00']:
            leg.checked_at = stamp
            report = diagnose_day(plan.days[0], REQUEST, NOW)
            self.assertEqual((report['stale_routes'], report['unknown_age_routes']), (0, 1))
        leg.checked_at = '2026-10-16T11:00:00+08:00'
        self.assertEqual(diagnose_day(plan.days[0], REQUEST, NOW)['stale_routes'], 0)

    async def test_overtime_and_repair_prompt_use_the_same_budget(self):
        plan = sample()
        plan.days[0].end_time = '11:00'
        await validate_schedule(plan, REQUEST, AsyncMock(return_value={"duration_minutes": 25, "distance_meters": 1800}))
        report = diagnose_day(plan.days[0], REQUEST, NOW)
        self.assertEqual(report['overrun_minutes'], 100)
        result = TravelAgent._settle_conflicts({}, plan, plan, '节奏轻松', REQUEST)
        text = result['messages'][0].content
        self.assertIn('"overrun_minutes": 100', text)
        self.assertIn('不要通过编造交通耗时', text)
        self.assertEqual(len(plan.days[0].attractions), 2)

    def test_unchecked_day_has_no_invented_budget(self):
        report = diagnose_day(sample().days[0], REQUEST, NOW)
        self.assertEqual(report['status'], 'unchecked')
        self.assertNotIn('remaining_minutes', report)
