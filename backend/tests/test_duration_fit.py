import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.initial_planning import fit_suggested_durations
from app.services.schedule_validator import validate_schedule
from test_schedule import sample
from test_travel_agent import REQUEST


class DurationFitTests(unittest.IsolatedAsyncioTestCase):
    async def check(self,plan,request,progress=None):
        await validate_schedule(plan,request,AsyncMock(return_value={'duration_minutes':10,'distance_meters':500}))

    async def fixture(self):
        plan=sample(3);day=plan.days[0]
        for i,p in enumerate(day.attractions):
            p.name=f'公园{i}';p.category='风景';p.visit_duration=120
        day.identify_visits()
        day.planned_start_times[day.attractions[-1].item_id]='18:00'
        day.end_time='19:45'
        await self.check(plan,REQUEST)
        return plan

    async def test_only_cuts_that_improve_the_end_are_retained(self):
        plan=await self.fixture(); before=plan.model_dump()
        validator=AsyncMock(side_effect=self.check)
        fixed,notes=await fit_suggested_durations(plan,REQUEST,validator)
        validator.assert_awaited_once()
        self.assertEqual([p.visit_duration for p in fixed.days[0].attractions],[120,120,95])
        self.assertEqual(fixed.days[0].schedule.items[-1].end_time,'19:35')
        self.assertEqual(fixed.days[0].end_time,'19:45')
        self.assertEqual(fixed.days[0].buffer_minutes,plan.days[0].buffer_minutes)
        self.assertEqual(plan.model_dump(),before)
        self.assertEqual(len(notes),1)

    async def test_cuts_that_only_add_waiting_make_no_provider_call(self):
        plan=await self.fixture()
        plan.days[0].attractions[-1].locked=True
        await self.check(plan,REQUEST)
        validator=AsyncMock()
        fixed,notes=await fit_suggested_durations(plan,REQUEST,validator)
        validator.assert_not_called()
        self.assertEqual(fixed.model_dump(),plan.model_dump())
        self.assertEqual(notes,[])

    async def test_joint_cuts_can_remove_an_opening_wait_larger_than_the_cuts(self):
        plan=sample(3);day=plan.days[0]
        for i,p in enumerate(day.attractions):
            p.name=f'公园{i}';p.category='风景';p.visit_duration=90
        last=day.attractions[-1]
        last.locked=True
        last.open_hours='11:00-13:45；15:00-18:00'
        day.end_time='16:00'
        await self.check(plan,REQUEST)
        self.assertEqual(day.schedule.items[-1].start_time,'15:00')
        fixed,notes=await fit_suggested_durations(plan,REQUEST,self.check)
        self.assertTrue(notes)
        self.assertEqual(fixed.days[0].schedule.status,'feasible')
        self.assertEqual(fixed.days[0].schedule.items[-1].end_time,'13:45')
        self.assertEqual(fixed.days[0].attractions[-1],last)
        self.assertTrue(all(p.visit_duration<90 for p in fixed.days[0].attractions[:2]))

    async def test_fresh_route_failure_or_exception_does_not_adopt_local_replay(self):
        for mode in ('slow','exception'):
            plan=await self.fixture();before=plan.model_dump()
            async def checker(p,r,progress=None):
                if mode=='exception': raise RuntimeError('offline')
                await validate_schedule(p,r,AsyncMock(return_value={'duration_minutes':500,'distance_meters':500}))
            fixed,notes=await fit_suggested_durations(plan,REQUEST,checker)
            self.assertEqual(fixed.model_dump(),before)
            self.assertEqual(notes,[])

    async def test_exhausted_local_search_budget_preserves_original(self):
        plan=await self.fixture();validator=AsyncMock()
        with patch('app.services.duration_fit.MAX_REPLAYS',0):
            fixed,notes=await fit_suggested_durations(plan,REQUEST,validator)
        validator.assert_not_called()
        self.assertEqual(fixed.model_dump(),plan.model_dump())
        self.assertEqual(notes,[])

    async def test_user_duration_and_missing_routes_never_shorten(self):
        for mode in ('explicit','missing'):
            plan=await self.fixture();request=REQUEST
            if mode=='explicit': request=REQUEST.model_copy(update={'free_text_input':'每处停留两小时'})
            else: plan.days[0].schedule.segments[0].status='unverified'
            validator=AsyncMock()
            _,notes=await fit_suggested_durations(plan,request,validator)
            validator.assert_not_called()
            self.assertEqual(notes,[])

    async def test_cancellation_is_not_swallowed(self):
        plan=await self.fixture()
        with self.assertRaises(asyncio.CancelledError):
            await fit_suggested_durations(plan,REQUEST,AsyncMock(side_effect=asyncio.CancelledError()))
