import unittest
import tempfile
from datetime import date,timedelta
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI
from httpx import ASGITransport,AsyncClient

from app.api.routes import saved_trips
from app.models.schemas import Hotel,Location
from app.services.trip_prep import build_prep
from app.services.cost_breakdown import build_cost_report
from app.services.hotel_quotes import HotelQuoteInput,apply_hotel_quotes
from app.services.trip_repository import TripRepository
from test_schedule import sample
from test_travel_agent import REQUEST


def trip(nights):
    plan=sample(1);first=date.fromisoformat(plan.start_date)
    template=plan.days[0];plan.days=[]
    for i in range(nights+1):
        day=template.model_copy(deep=True);day.date=(first+timedelta(days=i)).isoformat();day.day_index=i
        day.hotel=Hotel(name='测试酒店',poi_id='hotel-a',address='示例路1号',location=Location(longitude=120.1,latitude=30.2))
        day.identify_visits();plan.days.append(day)
    plan.end_date=plan.days[-1].date
    return plan


class LodgingPreparationTests(unittest.TestCase):
    def test_day_trip_has_no_overnight_even_with_a_hotel_route_anchor(self):
        plan=trip(0);before=plan.model_dump()
        prep=build_prep(plan)
        self.assertEqual(prep['stays'],[])
        self.assertIn('0 晚',prep['lodging_note'])
        self.assertEqual(build_cost_report(plan,REQUEST)['context']['lodging_nights'],0)
        self.assertEqual(plan.model_dump(),before)

    def test_consecutive_same_hotel_nights_merge_and_checkout_hotel_does_not_add_a_night(self):
        plan=trip(3);before=plan.model_dump()
        prep=build_prep(plan)
        self.assertEqual([(s['check_in'],s['check_out'],s['nights']) for s in prep['stays']],
                         [(plan.start_date,plan.end_date,3)])
        self.assertEqual(build_cost_report(plan,REQUEST)['context']['lodging_nights'],3)
        self.assertEqual(plan.model_dump(),before)

    def test_calendar_order_and_missing_day_do_not_invent_a_hotel(self):
        plan=trip(3);missing=plan.days.pop(1).date;plan.days.reverse()
        stays=build_prep(plan)['stays']
        self.assertEqual([s['check_in'] for s in stays],sorted(s['check_in'] for s in stays))
        self.assertEqual(sum(s['nights'] for s in stays),3)
        self.assertEqual(next(s for s in stays if s['check_in']==missing)['name'],None)
        report=build_cost_report(plan,REQUEST)
        self.assertEqual(next(s for s in report['lines'] if s['kind']=='hotels' and s['date']==missing)['name'],'当晚住宿待选择')

    def test_same_named_branches_coordinates_and_duplicate_dates_cannot_be_merged(self):
        plan=trip(2);plan.days[1].hotel.address='另一家分店地址'
        self.assertEqual(len(build_prep(plan)['stays']),2)
        plan=trip(2);plan.days[1].hotel.location.longitude+=.01
        self.assertEqual(len(build_prep(plan)['stays']),2)
        plan=trip(2);plan.days.append(plan.days[0].model_copy(deep=True))
        self.assertIsNone(build_prep(plan)['stays'][0]['name'])

    def test_check_identity_changes_with_hotel_dates_or_room_terms_but_not_unrelated_text(self):
        plan=trip(2);original=build_prep(plan)['stays'][0]['id']
        unchanged=plan.model_copy(deep=True);unchanged.days[0].description='只更新游览说明'
        self.assertEqual(build_prep(unchanged)['stays'][0]['id'],original)
        for field,value in [('name','另一家酒店'),('poi_id','hotel-b'),('address','另一个地址')]:
            changed=plan.model_copy(deep=True);setattr(changed.days[0].hotel,field,value)
            self.assertNotEqual(build_prep(changed)['stays'][0]['id'],original)
        shorter=trip(1)
        self.assertNotEqual(build_prep(shorter)['stays'][0]['id'],original)
        quoted=apply_hotel_quotes(plan,[HotelQuoteInput(night_date=plan.start_date,hotel_item_id=plan.days[0].hotel.item_id,
                                    price_per_room=300,room_type='双床房',taxes_included=True)])
        self.assertEqual(len(build_prep(quoted)['stays']),1)
        self.assertNotEqual(build_prep(quoted)['stays'][0]['id'],original)
        refreshed=quoted.model_copy(deep=True);refreshed.days[0].hotel.nightly_quote.recorded_at='2026-10-19T00:00:00+00:00'
        self.assertEqual(build_prep(refreshed)['stays'][0]['id'],build_prep(quoted)['stays'][0]['id'])
        refreshed.days[0].hotel.nightly_quote.room_type='大床房'
        self.assertNotEqual(build_prep(refreshed)['stays'][0]['id'],build_prep(quoted)['stays'][0]['id'])

    def test_invalid_range_stays_unknown_instead_of_becoming_zero_nights(self):
        plan=trip(0);plan.end_date='2026-10-01'
        self.assertEqual(build_prep(plan)['stays'],[])
        self.assertIn('无效',build_prep(plan)['lodging_note'])
        self.assertIsNone(build_cost_report(plan,REQUEST)['context']['lodging_nights'])


class LodgingPreparationApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_preparation_api_is_owner_scoped_read_only_and_matches_calendar_nights(self):
        with tempfile.TemporaryDirectory() as folder:
            repo=TripRepository(Path(folder)/'trips.sqlite3');owner=str(uuid4())
            app=FastAPI();app.include_router(saved_trips.router,prefix='/api')
            app.dependency_overrides[saved_trips.repository]=lambda:repo
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':owner}) as client:
                for nights in (0,2):
                    plan=trip(nights)
                    request=REQUEST.model_copy(update={'end_date':plan.end_date,'travel_days':nights+1})
                    saved=repo.create(owner,request.model_dump(),plan.model_dump())
                    before=repo.read(owner,saved['id'])
                    response=await client.get(f"/api/trips/{saved['id']}/prep")
                    self.assertEqual(response.status_code,200,response.text)
                    prep=response.json()
                    self.assertEqual(prep['version'],1)
                    self.assertEqual(sum(s['nights'] for s in prep['stays']),nights)
                    self.assertIn(f'{nights} 晚',prep['lodging_note'])
                    self.assertEqual(repo.read(owner,saved['id']),before)
                    self.assertEqual(len(repo.versions(owner,saved['id'])),1)
                    denied=await client.get(f"/api/trips/{saved['id']}/prep",headers={'X-Trip-Owner':str(uuid4())})
                    self.assertEqual(denied.status_code,404)
