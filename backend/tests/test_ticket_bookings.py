import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from app.api.routes import saved_trips
from app.models.schemas import TripCostContext, EntryWindow
from app.services.ticket_bookings import BookingInput, record_booking, cancel_booking, BookingConflict
from app.services.cost_breakdown import build_cost_report
from app.services.trip_repository import TripRepository
from app.services.fact_evidence import reconcile_manual_facts
from test_schedule import sample
from test_travel_agent import REQUEST


def input_for(plan, **values):
    day = plan.days[0]
    return BookingInput(day_date=day.date,item_id=day.attractions[0].item_id,
        **{'ticket_name':'成人票＋展览','participants':2,'paid_total':150.0,**values})


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.plan = sample(1)
        place = self.plan.days[0].attractions[0]
        place.ticket_price, place.cost_status = 60, 'estimated'
        self.context = TripCostContext(party_size=3,room_count=1,destination_budget=1000)

    def ticket_lines(self, plan):
        return [line for line in build_cost_report(plan, REQUEST, self.context)['lines'] if line['kind']=='attractions']

    def test_paid_total_replaces_only_covered_people_and_is_never_multiplied(self):
        plan = record_booking(self.plan, input_for(self.plan))
        lines = self.ticket_lines(plan)
        self.assertEqual(sum(line['amount'] for line in lines),210)
        self.assertEqual([line['quantity'] for line in lines],[1,1])
        self.assertEqual(lines[-1]['booking_participants'],2)
        plan = record_booking(plan,input_for(plan,booking_id=plan.ticket_bookings[0].id,participants=3,paid_total=200.0))
        self.assertEqual(len(plan.ticket_bookings),1)
        self.assertEqual(sum(line['amount'] for line in self.ticket_lines(plan)),200)
        self.assertEqual(self.plan.ticket_bookings,[])

    def test_cancel_does_not_assume_refund_and_rebooking_keeps_unreturned_money(self):
        plan = record_booking(self.plan,input_for(self.plan))
        booking_id = plan.ticket_bookings[0].id
        cancelled = cancel_booking(plan,booking_id,0)
        self.assertEqual(sum(line['amount'] for line in self.ticket_lines(cancelled)),330)
        refunded = cancel_booking(cancelled,booking_id,100)
        self.assertEqual(sum(line['amount'] for line in self.ticket_lines(refunded)),230)
        rebooked = record_booking(refunded,input_for(refunded,paid_total=120.0,participants=3))
        self.assertEqual(sum(line['amount'] for line in self.ticket_lines(rebooked)),170)
        self.assertEqual(len(rebooked.ticket_bookings),2)
        with self.assertRaises(BookingConflict): cancel_booking(rebooked,booking_id,151)

    def test_zero_price_validates_and_duplicate_or_foreign_record_is_rejected(self):
        plan = record_booking(self.plan,input_for(self.plan,paid_total=0.0,participants=3))
        self.assertEqual(self.ticket_lines(plan)[0]['amount'],0)
        with self.assertRaises(BookingConflict): record_booking(plan,input_for(plan))
        with self.assertRaises(BookingConflict): record_booking(plan,input_for(plan,booking_id='other'))
        for value in [float('nan'), True, -1.0, 1.111]:
            with self.assertRaises(ValidationError): input_for(self.plan,paid_total=value)

    def test_general_edits_preserve_ledger_and_protect_booked_venue_date_and_identity(self):
        plan = record_booking(self.plan,input_for(self.plan))
        edited = plan.model_copy(deep=True); edited.ticket_bookings=[]
        reconcile_manual_facts(edited,plan)
        self.assertEqual(edited.ticket_bookings,plan.ticket_bookings)
        for key,value in [('name','另一个景点'),('item_id','replacement')]:
            edited=plan.model_copy(deep=True);setattr(edited.days[0].attractions[0],key,value)
            with self.assertRaises(BookingConflict): reconcile_manual_facts(edited,plan)

    def test_cancel_only_clears_exact_recorded_timing_and_leaves_manual_change_intact(self):
        self.plan.days[0].attractions[0].entry_window=EntryWindow(start='09:00',end='12:00')
        plan=record_booking(self.plan,input_for(self.plan))
        cleared=cancel_booking(plan,plan.ticket_bookings[0].id,150,True)
        self.assertIsNone(cleared.days[0].attractions[0].entry_window)
        plan.days[0].attractions[0].entry_window=EntryWindow(start='10:00',end='13:00')
        with self.assertRaises(BookingConflict): cancel_booking(plan,plan.ticket_bookings[0].id,150,True)


class BookingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory();self.repo=TripRepository(Path(self.folder.name)/'trips.sqlite3')
        self.owner='1fa9fe7d-3432-4cd7-ab80-7e7c290e160a';self.plan=sample(1)
        self.trip=self.repo.create(self.owner,REQUEST.model_dump(),self.plan.model_dump())
        app=FastAPI();app.include_router(saved_trips.router,prefix='/api')
        app.dependency_overrides[saved_trips.repository]=lambda:self.repo
        self.client=AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':self.owner})
        self.url=f"/api/trips/{self.trip['id']}"
        self.body={'base_version':1,**input_for(self.plan).model_dump(mode='json')}

    async def asyncTearDown(self):
        await self.client.aclose();self.folder.cleanup()

    async def test_preview_readonly_then_save_cancel_refund_restore_are_versioned_without_provider(self):
        with patch('app.services.meituan_travel.query_travel',new=AsyncMock()) as supplier:
            response=await self.client.post(self.url+'/ticket-booking-preview',json=self.body)
            self.assertEqual(response.status_code,200)
            self.assertEqual(self.repo.read(self.owner,self.trip['id']),self.trip)
            saved=await self.client.post(self.url+'/ticket-booking',json=self.body)
            self.assertEqual(saved.status_code,200,saved.text)
            self.assertEqual(saved.json()['version'],2)
            booking_id=saved.json()['plan']['ticket_bookings'][0]['id']
            cancelled=await self.client.post(self.url+'/ticket-booking-cancel',json={'base_version':2,'booking_id':booking_id,'refunded_total':0})
            self.assertEqual(cancelled.status_code,200,cancelled.text)
            self.assertEqual(cancelled.json()['plan']['ticket_bookings'][0]['refunded_total'],0)
            supplier.assert_not_awaited()
        restored=self.repo.restore(self.owner,self.trip['id'],3,1)
        self.assertEqual(restored['plan'],self.trip['plan'])

    async def test_stale_owner_dates_and_general_commit_cannot_inject_financial_records(self):
        for change,expected in [({'base_version':2},409),({'item_id':'missing'},422),({'day_date':'2020-01-01'},422),({'participants':True},422)]:
            response=await self.client.post(self.url+'/ticket-booking',json={**self.body,**change})
            self.assertEqual(response.status_code,expected,response.text)
        response=await self.client.post(self.url+'/ticket-booking',json=self.body,headers={'X-Trip-Owner':'2fa9fe7d-3432-4cd7-ab80-7e7c290e160a'})
        self.assertEqual(response.status_code,404)
        fake=record_booking(self.plan,input_for(self.plan))
        saved=self.repo.commit(self.owner,self.trip['id'],1,fake.model_dump(),'manual')
        self.assertEqual(saved['plan']['ticket_bookings'],[])
        self.assertEqual(saved['version'],1)
