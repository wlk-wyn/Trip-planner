import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from app.models.schemas import Location
from app.services.visitor_access import visit_reminders, candidate_reference, matching_policy
from app.services.trip_prep import build_prep
from test_schedule import sample
from app.models.schemas import WeatherInfo
from app.services.trip_prep import saved_forecasts

NOW = datetime(2026, 10, 18, 12, tzinfo=timezone(timedelta(hours=8)))


def palace():
    plan = sample(1)
    plan.city = '北京'
    plan.days[0].date = '2026-10-27'
    place = plan.days[0].attractions[0]
    place.name = '故宫博物院'
    place.poi_id = 'B000A8UIN8'
    place.location = Location(longitude=116.397029, latitude=39.917839)
    return plan, place


class VisitorAccessTests(unittest.TestCase):
    def test_official_reference_has_exact_opening_time_but_no_booking_claim(self):
        plan, _ = palace()
        item = visit_reminders(plan, now=NOW)[0]
        self.assertEqual(item['booking_opens_at'], '2026-10-20T20:00:00+08:00')
        self.assertIsNone(item['confirm_by'])
        self.assertEqual(item['booking_status'], 'unverified')
        self.assertEqual(item['policy_status'], 'reviewed')
        self.assertIn('不售当日票', item['message'])
        self.assertIsNotNone(item['policy_source_url'])

    def test_name_id_city_and_coordinates_must_all_match(self):
        plan, place = palace()
        for city, changed in (('西安', {}), ('北京', {'poi_id': 'fake'}),
                              ('北京', {'name': '故宫餐厅'}),
                              ('北京', {'location': Location(longitude=110, latitude=30)})):
            self.assertIsNone(matching_policy(city, place.model_copy(update=changed)))
        raw = {'poi_id': place.poi_id, 'name': place.name, 'coordinates': [116.397029, 39.917839]}
        self.assertEqual(matching_policy('北京市', raw)['key'], 'palace')
        self.assertIsNone(candidate_reference('北京', {**raw, 'coordinates': ['bad', 39]}))

    def test_expired_and_future_review_dates_never_generate_booking_times(self):
        plan, _ = palace()
        for now in (NOW + timedelta(days=31), NOW - timedelta(days=1)):
            item = visit_reminders(plan, now=now)[0]
            self.assertEqual(item['policy_status'], 'expired')
            self.assertIsNone(item['booking_opens_at'])
            self.assertIn('复核期限', item['message'])

    def test_fixed_slot_keeps_official_policy_and_is_not_a_receipt(self):
        plan, place = palace()
        place.fixed_start_time = '10:00'
        item = visit_reminders(plan, now=NOW)[0]
        self.assertIn('不售当日票', item['message'])
        self.assertIn('预约成功情况仍未核实', item['message'])
        self.assertEqual(item['booking_status'], 'unverified')

    def test_same_day_palace_visit_prompts_existing_ticket_check_without_assuming_none(self):
        plan, _ = palace()
        plan.days[0].date = NOW.date().isoformat()
        item = visit_reminders(plan, now=NOW)[0]
        self.assertIn('是否已持有效预约', item['message'])
        self.assertEqual(item['booking_status'], 'unverified')

    def test_unknown_venue_and_fixed_slot_do_not_invent_lead_days(self):
        plan, place = palace()
        place.poi_id = 'other'
        item = visit_reminders(plan, now=NOW)[0]
        self.assertEqual(item['kind'], 'likely_booking')
        self.assertIsNone(item['booking_opens_at'])
        self.assertIsNone(item['policy_source_url'])
        place.fixed_start_time = '10:00'
        item = visit_reminders(plan, now=NOW)[0]
        self.assertEqual(item['kind'], 'appointment')
        self.assertIn('设置时间不等于订票', item['message'])

    def test_national_museum_free_entry_still_requires_reservation(self):
        plan, place = palace()
        place.poi_id = 'B000A83U0P'; place.name = '中国国家博物馆'
        place.location = Location(longitude=116.401304, latitude=39.905374)
        item = visit_reminders(plan, now=NOW)[0]
        self.assertIn('免费不代表免预约', item['message'])
        self.assertIsNone(item['booking_opens_at'])  # No inferred 7-day exact release date.

    def test_check_record_identity_changes_for_replacement_date_or_fixed_slot(self):
        plan, place = palace()
        original = visit_reminders(plan, now=NOW)[0]['id']
        self.assertEqual(visit_reminders(plan.model_copy(deep=True), now=NOW)[0]['id'], original)
        place.poi_id = 'replacement'
        self.assertNotEqual(visit_reminders(plan, now=NOW)[0]['id'], original)
        place.poi_id = 'B000A8UIN8'; place.fixed_start_time = '11:00'
        self.assertNotEqual(visit_reminders(plan, now=NOW)[0]['id'], original)
        place.fixed_start_time = None; plan.days[0].date = '2026-10-28'
        self.assertNotEqual(visit_reminders(plan, now=NOW)[0]['id'], original)

    def test_prep_uses_same_derived_references_without_mutating_plan(self):
        plan, _ = palace()
        before = plan.model_dump()
        with patch('app.services.visitor_access.datetime') as clock:
            clock.now.return_value = NOW
            prep = build_prep(plan)
        self.assertEqual(prep['reminders'], visit_reminders(plan, now=NOW))
        self.assertEqual(plan.model_dump(), before)

    def test_saved_weather_requires_fresh_query_and_report_evidence(self):
        plan, _ = palace()
        weather = WeatherInfo(date=plan.days[0].date, day_weather='晴', day_temp=0,
                              source='amap', observed_at='2026-10-18T03:00:00+00:00',
                              report_time='2026-10-18 10:00:00')
        plan.weather_info = [weather]
        self.assertEqual(saved_forecasts(plan, now=NOW)[weather.date]['daytemp'], 0)
        for change in ({'source': 'user'}, {'observed_at': None},
                       {'observed_at': '2026-10-18 10:00:00'},
                       {'report_time': '2026-10-16 10:00:00'},
                       {'report_time': '2026-10-19 10:00:00'}):
            plan.weather_info = [weather.model_copy(update=change)]
            self.assertEqual(saved_forecasts(plan, now=NOW), {})


class PreparationApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_preparation_and_checks_share_versioned_venue_requirements_without_provider_calls(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import AsyncMock
        from app.api.routes.saved_trips import trip_prep, plan_checks
        from app.services.trip_repository import TripRepository
        from test_travel_agent import REQUEST
        plan, _ = palace()
        with tempfile.TemporaryDirectory() as folder:
            repo = TripRepository(Path(folder) / 'trips.sqlite3')
            saved = repo.create('test-owner', REQUEST.model_dump(), plan.model_dump())
            with patch('app.services.amap_rest.get_forecast', new_callable=AsyncMock) as provider:
                prep = await trip_prep(saved['id'], owner='test-owner', repo=repo)
                checks = await plan_checks(saved['id'], owner='test-owner', repo=repo)
            provider.assert_not_awaited()
            self.assertEqual(prep['version'], checks['version'])
            self.assertEqual(prep['reminders'], checks['visit_reminders'])
            self.assertEqual(len(repo.versions('test-owner', saved['id'])), 1)
