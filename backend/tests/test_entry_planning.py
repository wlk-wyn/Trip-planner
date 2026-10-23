import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from langchain_core.messages import AIMessage

from app.api.routes import saved_trips
from app.models.schemas import EntryWindow, TripPlan, PlanPatch
from app.services.entry_planning import entry_preview
from app.services.schedule_validator import validate_schedule, build_schedule, stops_for_day
from app.services.schedule_integrity import check_integrity
from app.services.schedule_solver import solve_order
from app.services.trip_repository import TripRepository
from app.services.plan_patch import apply_patch
from app.services.conflict_fix import _movable
from test_schedule import sample
from test_travel_agent import REQUEST, PLAN, FakeModel, make_agent


class EntryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plan = sample()
        await validate_schedule(self.plan, REQUEST, AsyncMock(return_value={'duration_minutes': 20, 'distance_meters': 1000}))
        self.day = self.plan.days[0]
        self.target = self.day.attractions[-1]

    def preview(self, start, end):
        return entry_preview(self.plan, REQUEST, self.day.date, self.target.item_id, entry_window=EntryWindow(start=start, end=end))

    async def test_waits_for_entry_start_but_visit_can_finish_after_entry_deadline(self):
        candidate, earlier = self.preview('14:00', '14:30')
        item = next(i for i in candidate.schedule.items if i.id == self.target.item_id)
        self.assertEqual(item.start_time, '14:00')
        self.assertGreater(item.end_time, '14:30')
        self.assertEqual(candidate.schedule.status, 'feasible')
        self.assertEqual(check_integrity(candidate, REQUEST), [])
        self.assertIsNone(earlier)

    async def test_late_arrival_offers_bounded_earlier_departure_without_changing_visits(self):
        original = self.plan.model_dump()
        candidate, earlier = self.preview('09:00', '10:00')
        self.assertEqual(candidate.schedule.status, 'conflict')
        self.assertTrue(any('最晚10:00入场' in text for text in candidate.schedule.conflicts))
        self.assertIsNotNone(earlier)
        self.assertEqual(earlier.schedule.status, 'feasible')
        self.assertLess(earlier.start_time, candidate.start_time)
        self.assertEqual([a.item_id for a in candidate.attractions], [a.item_id for a in earlier.attractions])
        self.assertEqual([a.visit_duration for a in candidate.attractions], [a.visit_duration for a in earlier.attractions])
        self.assertEqual(original, self.plan.model_dump())

    async def test_opening_wait_cannot_hide_a_missed_entry_deadline(self):
        self.target.open_hours = '14:00-18:00'
        # Rebuild the original schedule so the saved graph is consistent.
        self.day.schedule = build_schedule(self.day, stops_for_day(self.day), self.day.schedule.segments, REQUEST)
        candidate, earlier = self.preview('09:00', '12:00')
        self.assertTrue(any('最晚12:00入场' in text for text in candidate.schedule.conflicts))
        self.assertIsNone(earlier)

    async def test_missing_or_stale_graph_never_claims_verified_arrival(self):
        for stale in ('missing', 'duration'):
            plan = self.plan.model_copy(deep=True)
            if stale == 'missing': plan.days[0].schedule = None
            else: plan.days[0].attractions[0].visit_duration += 1
            candidate, earlier = entry_preview(plan, REQUEST, self.day.date, self.target.item_id,
                                               entry_window=EntryWindow(start='10:00', end='13:00'))
            self.assertEqual(candidate.schedule.status, 'unverified')
            self.assertIsNone(candidate.schedule.items[-1].start_time)
            self.assertIsNone(earlier)

    async def test_solver_and_integrity_honor_window_without_claiming_booking(self):
        self.target.entry_window = EntryWindow(start='14:00', end='14:30')
        stops = stops_for_day(self.day)
        edge = self.day.schedule.segments[0]
        solved = solve_order(stops, {(0, 1): edge}, 540, 1200, 15, seconds=1, visit_date=self.day.date)
        self.assertIsNotNone(solved)
        self.assertTrue(840 <= solved[1][self.target.item_id] <= 870)
        self.day.schedule = build_schedule(self.day, stops, [edge], REQUEST)
        self.target.entry_window = EntryWindow(start='15:00', end='16:00')
        self.assertTrue(check_integrity(self.day, REQUEST))

    async def test_window_protects_visit_from_removal_and_survives_agent_revision(self):
        self.target.entry_window = EntryWindow(start='11:00', end='12:00')
        self.assertNotIn(self.target, _movable(self.day, REQUEST))
        patch_data = PlanPatch(operations=[{'action':'remove', 'date':self.day.date, 'kind':'attraction', 'item_id':self.target.item_id}])
        with self.assertRaisesRegex(ValueError, '固定预约'):
            apply_patch(self.plan, patch_data, {})
        current = sample(1)
        current.days[0].attractions[0].entry_window = EntryWindow(start='11:00', end='12:00')
        model = FakeModel([AIMessage(content=json.dumps({'changed': True, 'reply':'调整', 'plan': PLAN}))])
        result, *_ = await make_agent(model).run(REQUEST, current, '调整第一天')
        self.assertEqual(result.days[0].attractions[0].entry_window.start, '11:00')

    def test_invalid_or_ambiguous_time_constraints_rejected(self):
        for start, end in [('12:00','11:00'), ('12:00','12:00'), ('24:00','25:00')]:
            with self.assertRaises(ValidationError): EntryWindow(start=start,end=end)
        raw = self.target.model_dump()
        raw.update(fixed_start_time='10:00', entry_window={'start':'09:00','end':'11:00'})
        with self.assertRaises(ValidationError): TripPlan(**{**self.plan.model_dump(), 'days':[{**self.day.model_dump(),'attractions':[raw]}]})


class EntryApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.repo = TripRepository(Path(self.folder.name)/'trips.sqlite3')
        self.owner = '1fa9fe7d-3432-4cd7-ab80-7e7c290e160a'
        plan = sample()
        await validate_schedule(plan, REQUEST, AsyncMock(return_value={'duration_minutes':20,'distance_meters':1000}))
        self.trip = self.repo.create(self.owner, REQUEST.model_dump(), plan.model_dump())
        self.body = {'base_version':1,'day_date':plan.days[0].date,'item_id':plan.days[0].attractions[-1].item_id,
                     'entry_window':{'start':'09:00','end':'10:00'}}
        app = FastAPI(); app.include_router(saved_trips.router,prefix='/api')
        app.dependency_overrides[saved_trips.repository] = lambda:self.repo
        self.client = AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':self.owner})
        self.url = f"/api/trips/{self.trip['id']}"

    async def asyncTearDown(self):
        await self.client.aclose(); self.folder.cleanup()

    async def test_preview_is_readonly_no_provider_then_save_is_one_undoable_version(self):
        with patch('app.services.amap_rest.get_route',new=AsyncMock(side_effect=AssertionError('no provider'))) as provider:
            response = await self.client.post(self.url+'/entry-preview',json=self.body)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['day']['schedule']['status'],'conflict')
            self.assertIsNotNone(response.json()['earlier_day'])
            self.assertEqual(self.trip, self.repo.read(self.owner,self.trip['id']))
            saved = await self.client.post(self.url+'/entry-timing',json={**self.body,'choice':'earlier'})
            provider.assert_not_awaited()
        self.assertEqual(saved.status_code,200)
        self.assertEqual(saved.json()['version'],2)
        self.assertEqual(saved.json()['plan']['days'][0]['schedule']['status'],'feasible')
        restored = self.repo.restore(self.owner,self.trip['id'],2,1)
        self.assertEqual(restored['plan'],self.trip['plan'])

    async def test_owner_stale_version_and_invalid_choice_do_not_write(self):
        for change, status in [({'base_version':2},409),({'item_id':'missing'},404),({'day_date':'2026-99-99'},422),({'entry_window':{'start':'12:00','end':'11:00'}},422),({'fixed_start_time':'10:00'},422)]:
            r = await self.client.post(self.url+'/entry-timing',json={**self.body,**change})
            self.assertEqual(r.status_code,status,r.text)
        r = await self.client.post(self.url+'/entry-timing',json={**self.body,'entry_window':None,'choice':'earlier'})
        self.assertEqual(r.status_code,422)
        r = await self.client.post(self.url+'/entry-preview',json=self.body,headers={'X-Trip-Owner':'2fa9fe7d-3432-4cd7-ab80-7e7c290e160a'})
        self.assertEqual(r.status_code,404)
        self.assertEqual(self.trip,self.repo.read(self.owner,self.trip['id']))

    async def test_save_conflict_clear_constraint_and_duplicate_submit(self):
        saved = await self.client.post(self.url+'/entry-timing',json=self.body)
        self.assertEqual(saved.status_code,200)
        self.assertEqual(saved.json()['plan']['days'][0]['schedule']['status'],'conflict')
        repeated = await self.client.post(self.url+'/entry-timing',json=self.body)
        self.assertEqual(repeated.status_code,409)
        cleared = await self.client.post(self.url+'/entry-timing',json={**self.body,'base_version':2,'entry_window':None})
        self.assertEqual(cleared.status_code,200)
        self.assertIsNone(cleared.json()['plan']['days'][0]['attractions'][-1]['entry_window'])
