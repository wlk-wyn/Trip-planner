import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.routes import saved_trips
from app.models.schemas import EntryWindow, Budget
from app.services.entry_planning import entry_preview
from app.services.entry_options import checked_options, candidates
from app.services.schedule_validator import validate_schedule
from app.services.ticket_bookings import record_booking
from app.services.trip_repository import TripRepository
from test_schedule import sample
from test_travel_agent import REQUEST
from test_ticket_bookings import input_for


async def validator(plan, request, **kwargs):
    return await validate_schedule(plan, request, AsyncMock(return_value={'duration_minutes':20,'distance_meters':1000}))


async def fixture():
    plan=sample(3)
    second=plan.days[0].model_copy(deep=True); second.date=plan.days[0].date[:8]+f'{int(plan.days[0].date[-2:])+1:02d}'
    second.day_index=1; second.attractions=second.attractions[:1]
    second.attractions[0].name='另一天的景点';second.attractions[0].item_id='second-day-stop'
    second.attractions[0].location.longitude+=.1
    plan.days.append(second);plan.end_date=second.date
    await validator(plan,REQUEST)
    target=plan.days[0].attractions[-1]
    seed,_=entry_preview(plan,REQUEST,plan.days[0].date,target.item_id,entry_window=EntryWindow(start='09:00',end='12:00'))
    return plan,target,seed


class OptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_reorders_or_moves_flexible_visit_but_keeps_target_date_and_all_visits(self):
        plan,target,seed=await fixture(); plan.budget=Budget(total=999); original=plan.model_dump()
        rows=await checked_options(plan,REQUEST,seed,target.item_id,True,validator=validator)
        self.assertEqual(len(rows),3)
        self.assertTrue(any(row['status']=='feasible' for row in rows))
        cross=next(row for row in rows if row['id']=='move-flex')
        self.assertEqual(cross['status'],'feasible')
        self.assertIsNone(cross['plan'].budget)
        self.assertEqual(sum(len(d.attractions) for d in cross['plan'].days),4)
        self.assertTrue(any(p.item_id==target.item_id for p in cross['plan'].days[0].attractions))
        self.assertEqual(cross['plan'].days[0].attractions[-1].entry_window.end,'12:00')
        self.assertEqual(original,plan.model_dump())

    async def test_cross_day_requires_optin_and_preserves_booked_and_locked_visits(self):
        plan,target,seed=await fixture()
        self.assertTrue(all(row['id']!='move-flex' for row in candidates(plan,REQUEST,seed,target.item_id)))
        plan=record_booking(plan,input_for(plan))
        seed.attractions[1].locked=True
        rows=candidates(plan,REQUEST,seed,target.item_id,True)
        self.assertTrue(all(row['id']!='move-flex' for row in rows))
        self.assertFalse(rows) # no unprotected preceding visit can be swapped

    async def test_unknown_routes_do_not_become_adoptable_solutions(self):
        plan,target,seed=await fixture()
        async def unknown(value, request, **kwargs):
            return await validate_schedule(value,request,AsyncMock(side_effect=RuntimeError('offline')))
        rows=await checked_options(plan,REQUEST,seed,target.item_id,validator=unknown)
        self.assertTrue(rows)
        self.assertTrue(all(row['status']!='feasible' for row in rows))

    async def test_bounded_candidates_and_route_budget(self):
        plan,target,seed=await fixture(); calls=[]
        async def counted(value,request,**kwargs):
            calls.append(kwargs['route_timeout_seconds']);return await validator(value,request)
        rows=await checked_options(plan,REQUEST,seed,target.item_id,True,validator=counted)
        self.assertLessEqual(len(rows),3)
        self.assertTrue(all(0 < seconds <= 8 for seconds in calls))
        strict=REQUEST.model_copy(update={'reference_mode':'strict'})
        self.assertTrue(all(row['id']!='move-flex' for row in candidates(plan,strict,seed,target.item_id,True)))


class OptionApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory();self.repo=TripRepository(Path(self.folder.name)/'trips.sqlite3')
        self.owner='1fa9fe7d-3432-4cd7-ab80-7e7c290e160a'
        plan,target,_=await fixture()
        self.trip=self.repo.create(self.owner,REQUEST.model_dump(),plan.model_dump())
        self.body={'base_version':1,'day_date':plan.days[0].date,'item_id':target.item_id,'entry_window':{'start':'09:00','end':'12:00'},'allow_cross_day':True}
        app=FastAPI();app.include_router(saved_trips.router,prefix='/api');app.dependency_overrides[saved_trips.repository]=lambda:self.repo
        self.client=AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':self.owner})
        self.url=f"/api/trips/{self.trip['id']}"

    async def asyncTearDown(self):
        await self.client.aclose();self.folder.cleanup()

    async def test_preview_then_adopt_recomputes_server_candidate_as_one_recoverable_version(self):
        async def checked(*args,**kwargs): return await checked_options(*args,**kwargs,validator=validator)
        with patch('app.services.entry_options.checked_options',side_effect=checked):
            response=await self.client.post(self.url+'/entry-options',json=self.body)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(self.repo.read(self.owner,self.trip['id']),self.trip)
            self.assertTrue(all('plan' not in row for row in response.json()['options']))
            chosen=next(row['id'] for row in response.json()['options'] if row['status']=='feasible')
            saved=await self.client.post(self.url+'/entry-option',json={**self.body,'selection_id':chosen})
            self.assertEqual(saved.status_code,200,saved.text);self.assertEqual(saved.json()['version'],2)
        restored=self.repo.restore(self.owner,self.trip['id'],2,1)
        self.assertEqual(restored['plan'],self.trip['plan'])

    async def test_unverified_unknown_choice_and_stale_version_cannot_be_saved(self):
        for changes,expected in [({'selection_id':'forged'},422),({'base_version':2,'selection_id':'reorder-1'},409)]:
            response=await self.client.post(self.url+'/entry-option',json={**self.body,**changes})
            self.assertEqual(response.status_code,expected)
        with patch('app.services.entry_options.checked_options',new=AsyncMock(return_value=[{'status':'unverified'}])):
            response=await self.client.post(self.url+'/entry-option',json={**self.body,'selection_id':'reorder-1'})
            self.assertEqual(response.status_code,422)
        self.assertEqual(self.repo.read(self.owner,self.trip['id']),self.trip)
