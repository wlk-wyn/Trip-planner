import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4
from fastapi import FastAPI
from httpx import ASGITransport,AsyncClient
from pydantic import ValidationError
from app.api.routes import saved_trips
from app.models.schemas import Hotel,Meal
from app.services.cost_breakdown import build_cost_report
from app.services.hotel_quotes import HotelQuoteInput,apply_hotel_quotes,retain_hotel_quotes
from app.services.fact_evidence import fact_state,reconcile_manual_facts
from app.services.schedule_validator import validate_schedule
from app.services.trip_repository import TripRepository
from test_schedule import sample
from test_travel_agent import REQUEST


class HotelQuoteTests(unittest.IsolatedAsyncioTestCase):
    async def fixture(self):
        plan=sample(1);template=plan.days[0]
        template.attractions[0].ticket_price=10;template.attractions[0].cost_status='confirmed'
        template.meals=[Meal(name='',restaurant='午餐',type='lunch',location=template.attractions[0].location,estimated_cost=20,cost_status='estimated')]
        template.hotel=Hotel(name='示例酒店',poi_id='hotel-a',location=template.attractions[0].location)
        plan.days=[template.model_copy(deep=True) for _ in range(3)]
        for i,day in enumerate(plan.days):day.date=f'2026-10-{3+i:02d}';day.day_index=i;day.identify_visits()
        plan.end_date='2026-10-05'
        request=REQUEST.model_copy(update={'end_date':plan.end_date,'travel_days':3,'party_size':2,'room_count':2,'destination_budget':1500})
        await validate_schedule(plan,request,AsyncMock(return_value={'duration_minutes':10,'distance_meters':500,'fare':2}))
        return plan,request

    def quotes(self,plan,taxes=True):
        return [HotelQuoteInput(night_date=day.date,hotel_item_id=day.hotel.item_id,price_per_room=price,room_type='双人标准间',source_label='用户查到的测试报价',taxes_included=taxes)
                for day,price in zip(plan.days,[300,320])]

    async def test_unpriced_room_nights_show_a_conditional_allowance_not_a_price(self):
        plan,request=await self.fixture();before=plan.model_dump()
        report=build_cost_report(plan,request)
        self.assertEqual(report['known_total'],216)
        self.assertEqual(report['lodging_allowance']['remaining_reference_budget'],1284)
        self.assertEqual(report['lodging_allowance']['unpriced_room_nights'],4)
        self.assertEqual(report['lodging_allowance']['average_ceiling_per_room_night'],321)
        self.assertEqual(report['budget']['status'],'incomplete')
        self.assertTrue(all(line['unit_price'] is None for line in report['lines'] if line['kind']=='hotels'))
        self.assertEqual(plan.model_dump(),before)

    async def test_allowance_is_suppressed_for_other_unknown_costs_or_assumed_counts(self):
        plan,request=await self.fixture()
        plan.days[0].attractions[0].ticket_price=None;plan.days[0].attractions[0].cost_status='unknown'
        self.assertIsNone(build_cost_report(plan,request)['lodging_allowance'])
        plan,request=await self.fixture()
        self.assertIsNone(build_cost_report(plan,request.model_copy(update={'room_count':None}))['lodging_allowance'])
        self.assertIsNone(build_cost_report(plan,request.model_copy(update={'budget_mode':'deferred'}))['lodging_allowance'])

    async def test_dated_room_quotes_count_each_night_and_room_without_route_changes(self):
        plan,request=await self.fixture();before=plan.model_dump()
        fixed=apply_hotel_quotes(plan,self.quotes(plan));report=build_cost_report(fixed,request)
        self.assertEqual(report['totals']['hotels'],1240)
        self.assertEqual(report['known_total'],1456)
        self.assertEqual(report['unknown_count'],0)
        self.assertEqual(report['budget']['status'],'within_listed_reference')
        self.assertEqual([d.schedule for d in fixed.days],[d.schedule for d in plan.days])
        self.assertEqual(plan.model_dump(),before)
        self.assertIsNone(fixed.days[-1].hotel.nightly_quote)
        self.assertEqual(fact_state(fixed.days[0].hotel,'price',visit_date=plan.start_date)['source'],'user')

    async def test_quote_cannot_follow_a_different_hotel_or_night(self):
        plan,request=await self.fixture();fixed=apply_hotel_quotes(plan,self.quotes(plan))
        fixed.days[0].hotel.nightly_quote.hotel_place_id='amap:another-hotel'
        fixed.days[1].hotel.nightly_quote.night_date='2026-10-03'
        report=build_cost_report(fixed,request)
        self.assertEqual(report['totals']['hotels'],0)
        self.assertEqual(report['unknown_hotel_nights'],2)

    async def test_tax_unknown_keeps_budget_incomplete_even_with_known_room_numbers(self):
        plan,request=await self.fixture();fixed=apply_hotel_quotes(plan,self.quotes(plan,taxes=None))
        report=build_cost_report(fixed,request)
        self.assertEqual(report['unknown_count'],0)
        self.assertEqual(report['hotel_quote_taxes_pending'],2)
        self.assertEqual(report['budget']['status'],'incomplete')
        self.assertIsNone(report['lodging_allowance'])

    async def test_checkout_duplicate_or_wrong_visit_is_rejected_without_mutating_plan(self):
        plan,_=await self.fixture();before=plan.model_dump();quote=self.quotes(plan)[0]
        for batch in [[quote,quote],[quote.model_copy(update={'night_date':plan.end_date})],[quote.model_copy(update={'hotel_item_id':'other'})]]:
            with self.assertRaises(ValueError):apply_hotel_quotes(plan,batch)
            self.assertEqual(plan.model_dump(),before)

    async def test_invalid_price_room_or_calendar_date_is_rejected(self):
        plan,_=await self.fixture();raw=self.quotes(plan)[0].model_dump()
        for changes in [{'price_per_room':True},{'price_per_room':-1},{'price_per_room':float('nan')},{'price_per_room':float('inf')},{'room_type':''},{'night_date':'2026-02-30'},{'taxes_included':1}]:
            with self.subTest(changes=changes),self.assertRaises(ValidationError):HotelQuoteInput(**(raw|changes))

    async def test_manual_legacy_price_edit_clears_priority_quote_and_zero_is_explicit(self):
        plan,request=await self.fixture();before=apply_hotel_quotes(plan,self.quotes(plan))
        edited=before.model_copy(deep=True);edited.days[0].hotel.estimated_cost=0;edited.days[0].hotel.cost_status='confirmed'
        reconcile_manual_facts(edited,before)
        self.assertIsNone(edited.days[0].hotel.nightly_quote)
        report=build_cost_report(edited,request)
        self.assertEqual(next(line for line in report['lines'] if line['kind']=='hotels')['unit_price'],0)

    async def test_api_bulk_save_is_one_version_owner_scoped_atomic_and_reversible(self):
        plan,request=await self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            repo=TripRepository(Path(folder)/'test.sqlite3');owner=str(uuid4())
            trip=repo.create(owner,request.model_dump(),plan.model_dump())
            app=FastAPI();app.include_router(saved_trips.router,prefix='/api');app.dependency_overrides[saved_trips.repository]=lambda:repo
            body={'base_version':1,'quotes':[q.model_dump() for q in self.quotes(plan)]}
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':owner}) as client:
                path=f"/api/trips/{trip['id']}/hotel-quotes"
                denied=await client.post(path,json=body,headers={'X-Trip-Owner':str(uuid4())});self.assertEqual(denied.status_code,404)
                invalid=await client.post(path,json={**body,'quotes':body['quotes']+[body['quotes'][0]]});self.assertEqual(invalid.status_code,422)
                self.assertEqual(repo.read(owner,trip['id'])['version'],1)
                saved=await client.post(path,json=body);self.assertEqual(saved.status_code,200);self.assertEqual(saved.json()['version'],2)
                self.assertEqual(len(repo.versions(owner,trip['id'])),2)
                self.assertTrue(any('报价' in line for line in saved.json()['change_summary']))
                stale=await client.post(path,json=body);self.assertEqual(stale.status_code,409)
                restored=repo.restore(owner,trip['id'],2,1)
                self.assertIsNone(restored['plan']['days'][0]['hotel']['nightly_quote'])

    async def test_model_output_cannot_invent_or_overwrite_a_user_quote(self):
        plan,_=await self.fixture();previous=apply_hotel_quotes(plan,self.quotes(plan))
        generated=previous.model_copy(deep=True);generated.days[0].hotel.nightly_quote.price_per_room=9000
        retain_hotel_quotes(generated,previous)
        self.assertEqual(generated.days[0].hotel.nightly_quote.price_per_room,300)
        retain_hotel_quotes(generated)
        self.assertIsNone(generated.days[0].hotel.nightly_quote)

    async def test_general_edit_cannot_invent_or_rewrite_dated_quotes(self):
        plan,_=await self.fixture();quoted=apply_hotel_quotes(plan,self.quotes(plan))
        invented=quoted.model_copy(deep=True)
        reconcile_manual_facts(invented,plan)
        self.assertIsNone(invented.days[0].hotel.nightly_quote)
        edited=quoted.model_copy(deep=True)
        edited.days[0].hotel.nightly_quote.price_per_room=1
        edited.days[0].hotel.nightly_quote.taxes_included=True
        edited.days[0].description='仅调整当天说明'
        reconcile_manual_facts(edited,quoted)
        self.assertEqual(edited.days[0].hotel.nightly_quote,quoted.days[0].hotel.nightly_quote)
        self.assertIsNot(edited.days[0].hotel.nightly_quote,quoted.days[0].hotel.nightly_quote)

    async def test_general_edit_cannot_move_quote_to_another_night_or_hotel(self):
        plan,_=await self.fixture();quoted=apply_hotel_quotes(plan,self.quotes(plan))
        for changes in ({'poi_id':'hotel-b'},{'name':'另一家酒店'},{'address':'另一个地址'},
                        {'item_id':'new-visit'},{'estimated_cost':99}):
            edited=quoted.model_copy(deep=True)
            for key,value in changes.items():setattr(edited.days[0].hotel,key,value)
            reconcile_manual_facts(edited,quoted)
            self.assertIsNone(edited.days[0].hotel.nightly_quote,changes)
        moved=quoted.model_copy(deep=True)
        moved.days[-1].hotel=quoted.days[0].hotel.model_copy(deep=True)
        reconcile_manual_facts(moved,quoted)
        self.assertIsNone(moved.days[-1].hotel.nightly_quote)

    async def test_manual_save_preserves_server_quote_in_the_persisted_version(self):
        plan,request=await self.fixture();quoted=apply_hotel_quotes(plan,self.quotes(plan))
        with tempfile.TemporaryDirectory() as folder:
            repo=TripRepository(Path(folder)/'test.sqlite3');owner=str(uuid4())
            trip=repo.create(owner,request.model_dump(),quoted.model_dump())
            edited=quoted.model_copy(deep=True)
            edited.days[0].hotel.nightly_quote.price_per_room=1
            edited.days[0].description='修改说明，不改房价'
            app=FastAPI();app.include_router(saved_trips.router,prefix='/api')
            app.dependency_overrides[saved_trips.repository]=lambda:repo
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':owner}) as client:
                saved=await client.post(f"/api/trips/{trip['id']}/versions",json={'base_version':1,'plan':edited.model_dump()})
                self.assertEqual(saved.status_code,200,saved.text)
            current=repo.read(owner,trip['id'])
            self.assertEqual(current['version'],2)
            self.assertEqual(current['plan']['days'][0]['hotel']['nightly_quote']['price_per_room'],300)
            self.assertEqual(current['plan']['days'][0]['description'],'修改说明，不改房价')

    async def test_explicit_zero_room_quote_does_not_turn_the_other_night_into_free(self):
        plan,request=await self.fixture();quote=self.quotes(plan)[0].model_copy(update={'price_per_room':0})
        fixed=apply_hotel_quotes(plan,[quote]);report=build_cost_report(fixed,request)
        lines=[line for line in report['lines'] if line['kind']=='hotels']
        self.assertEqual(lines[0]['unit_price'],0)
        self.assertIsNone(lines[1]['unit_price'])
        self.assertEqual(report['unknown_hotel_nights'],1)
