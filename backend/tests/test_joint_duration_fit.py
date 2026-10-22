import unittest
from unittest.mock import AsyncMock

from app.models.schemas import Meal
from app.services.initial_planning import fit_suggested_durations
from app.services.meal_timing import prepare_meal_times, meal_timing_issues
from app.services.schedule_integrity import check_integrity
from app.services.schedule_validator import validate_schedule
from test_schedule import sample
from test_travel_agent import REQUEST


class JointDurationFitTests(unittest.IsolatedAsyncioTestCase):
    async def draft(self):
        plan=sample(2);day=plan.days[0]
        day.date='2026-10-20';day.start_time='09:00';day.end_time='18:10';day.buffer_minutes=15
        day.attractions[0].visit_duration=240;day.attractions[1].visit_duration=90
        day.meals=[Meal(type=slot,restaurant=name,name=name,location=day.attractions[0].location,open_hours=hours)
            for slot,name,hours in [('breakfast','早餐店','07:00-11:00'),('lunch','午餐餐厅','11:30-15:00'),('dinner','晚餐餐厅','17:00-21:00')]]
        plan=prepare_meal_times(plan,REQUEST)
        await validate_schedule(plan,REQUEST,AsyncMock(return_value={'duration_minutes':10,'distance_meters':400}))
        return plan

    async def same_edges(self,plan,request,progress=None):
        return await validate_schedule(plan,request,AsyncMock(return_value={'duration_minutes':10,'distance_meters':400}))

    async def test_overtime_and_restaurant_hours_are_solved_together_without_changing_places_or_order(self):
        plan=await self.draft();before=plan.model_dump();day=plan.days[0]
        self.assertTrue(any('超出可用时间' in c for c in day.schedule.conflicts))
        self.assertTrue(any('午餐餐厅' in c for c in day.schedule.conflicts))
        check=AsyncMock(side_effect=self.same_edges)
        fitted,notes=await fit_suggested_durations(plan,REQUEST,check)
        self.assertTrue(notes);check.assert_awaited_once()
        result=fitted.days[0]
        self.assertEqual(result.schedule.status,'feasible');self.assertFalse(result.schedule.conflicts)
        self.assertFalse(meal_timing_issues(fitted,REQUEST));self.assertFalse(check_integrity(result,REQUEST))
        self.assertEqual([i.id for i in result.schedule.items],[i.id for i in day.schedule.items])
        self.assertEqual([m.model_dump() for m in result.meals],[m.model_dump() for m in day.meals])
        self.assertEqual((result.start_time,result.end_time,result.buffer_minutes),(day.start_time,day.end_time,day.buffer_minutes))
        self.assertTrue(all(b.visit_duration>=a.visit_duration*.75 for a,b in zip(day.attractions,result.attractions)))
        self.assertEqual(plan.model_dump(),before)

    async def test_unresolvable_closed_day_or_missing_traffic_is_not_papered_over(self):
        for mode in ('closed','unknown'):
            plan=await self.draft()
            if mode=='closed':
                plan.days[0].attractions[0].open_hours='周二闭馆'
                await self.same_edges(plan,REQUEST)
            else:plan.days[0].schedule.segments[0].status='unverified'
            check=AsyncMock(side_effect=self.same_edges)
            fitted,notes=await fit_suggested_durations(plan,REQUEST,check)
            self.assertFalse(notes);check.assert_not_awaited()
            self.assertEqual(fitted.model_dump(),plan.model_dump())

    async def test_explicit_or_protected_visits_keep_their_durations(self):
        for mode in ('explicit','locked'):
            plan=await self.draft();request=REQUEST.model_copy(deep=True)
            if mode=='explicit':request.free_text_input='博物馆停留4个小时'
            else:
                for p in plan.days[0].attractions:p.locked=True
            check=AsyncMock(side_effect=self.same_edges)
            fitted,notes=await fit_suggested_durations(plan,request,check)
            self.assertFalse(notes);check.assert_not_awaited()
            self.assertEqual(fitted.model_dump(),plan.model_dump())

    async def test_changed_route_results_must_still_pass_full_validation(self):
        plan=await self.draft()
        async def changed(partial,request,progress=None):
            await validate_schedule(partial,request,AsyncMock(return_value={'duration_minutes':30,'distance_meters':2000}))
        fitted,notes=await fit_suggested_durations(plan,REQUEST,changed)
        self.assertFalse(notes);self.assertEqual(fitted.model_dump(),plan.model_dump())
