import copy
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.routes import saved_trips
from app.services.admission_guides import admission_guide, GUIDES, REVIEWED
from app.services.visitor_access import CHINA
from app.services.trip_repository import TripRepository
from test_visitor_access import palace
from test_travel_agent import REQUEST

NOW = datetime(2026, 10, 22, 12, tzinfo=CHINA)


class GuideTests(unittest.TestCase):
    def test_palace_summary_is_sourced_without_booking_or_inventory_claim(self):
        plan, place = palace()
        guide = admission_guide(plan.city, place, '2026-10-27', now=NOW)
        self.assertEqual(guide['status'], 'reviewed')
        self.assertEqual(guide['booking_status'], 'unverified')
        self.assertEqual(guide['booking_opens_at'], '2026-10-20T20:00:00+08:00')
        self.assertEqual(guide['booking_url'], 'https://ticket.dpm.org.cn/')
        self.assertIn('未授权', guide['notice'])
        self.assertTrue(any('不出售当日票' in row['text'] for row in guide['summary']))
        self.assertTrue(guide['sources'])

    def test_city_id_name_and_coordinates_must_all_match(self):
        plan, place = palace()
        for field, value in [('poi_id','other'),('name','故宫餐厅'),('location',{'longitude':120,'latitude':30})]:
            altered=place.model_dump(); altered[field]=value
            self.assertIsNone(admission_guide(plan.city, altered, '2026-10-27', now=NOW))
        self.assertIsNone(admission_guide('杭州',place,'2026-10-27',now=NOW))

    def test_expired_and_future_review_do_not_expose_actionable_old_rules(self):
        plan, place = palace()
        for instant in (NOW+timedelta(days=31), NOW-timedelta(days=1)):
            guide=admission_guide(plan.city,place,'2026-10-27',now=instant)
            self.assertEqual(guide['status'],'expired')
            self.assertEqual(guide['summary'],[])
            self.assertEqual(guide['details'],[])
            self.assertIsNone(guide['booking_opens_at'])

    def test_visit_date_recomputes_window_and_monday_does_not_invent_it(self):
        plan, place=palace()
        first=admission_guide(plan.city,place,'2026-10-28',now=NOW)
        self.assertEqual(first['booking_opens_at'],'2026-10-21T20:00:00+08:00')
        self.assertIsNone(admission_guide(plan.city,place,'2026-10-26',now=NOW)['booking_opens_at'])
        first['summary'][0]['text']='mutated'
        self.assertNotEqual(GUIDES['palace']['summary'][0]['text'],'mutated')
        with self.assertRaises(ValueError): admission_guide(plan.city,place,'2026-10-28',now=NOW.replace(tzinfo=None))

    def test_park_has_season_and_no_fabricated_direct_booking_url(self):
        place={'poi_id':'B000A7O1CU','name':'颐和园','location':{'longitude':116.275,'latitude':39.995}}
        peak=admission_guide('北京',place,'2026-10-27',now=NOW)
        low=admission_guide('北京',place,'2026-11-03',now=NOW)
        self.assertIsNone(peak['booking_url'])
        self.assertIn('19:00停止入园',peak['details'][0]['text'])
        self.assertIn('18:00停止入园',low['details'][0]['text'])


class GuideApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.repo=TripRepository(Path(self.folder.name)/'trips.sqlite3')
        self.owner='1fa9fe7d-3432-4cd7-ab80-7e7c290e160a'
        plan,_=palace()
        self.trip=self.repo.create(self.owner,REQUEST.model_dump(),plan.model_dump())
        self.original=copy.deepcopy(self.trip)
        self.item=plan.days[0].attractions[0].item_id
        app=FastAPI(); app.include_router(saved_trips.router,prefix='/api')
        app.dependency_overrides[saved_trips.repository]=lambda:self.repo
        self.client=AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':self.owner})
        self.url=f"/api/trips/{self.trip['id']}/admission-guide"
        self.params={'version':1,'day_date':'2026-10-27','visit_date':'2026-10-28','item_id':self.item}

    async def asyncTearDown(self):
        await self.client.aclose(); self.folder.cleanup()

    async def test_read_is_versioned_scoped_and_does_not_query_provider_or_write_trip(self):
        with patch('app.services.meituan_travel.query_travel',new=AsyncMock()) as supplier:
            response=await self.client.get(self.url,params=self.params)
            supplier.assert_not_awaited()
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['visit_date'],'2026-10-28')
        self.assertEqual(response.json()['guide']['booking_status'],'unverified')
        self.assertEqual(self.original,self.repo.read(self.owner,self.trip['id']))

    async def test_owner_version_missing_place_and_invalid_dates_are_rejected(self):
        for changes,status in [({'version':2},409),({'item_id':'missing'},404),({'day_date':'2026-10-29'},404),({'visit_date':'not-a-date'},422)]:
            response=await self.client.get(self.url,params={**self.params,**changes})
            self.assertEqual(response.status_code,status)
        other=await self.client.get(self.url,params=self.params,headers={'X-Trip-Owner':'2fa9fe7d-3432-4cd7-ab80-7e7c290e160a'})
        self.assertEqual(other.status_code,404)
