import unittest
from datetime import datetime
from unittest.mock import AsyncMock,patch
from app.models.schemas import Location
from app.services.park_scopes import PARKS,matching_park,park_scope_issues
from app.services.visitor_access import CHINA,visit_reminders,scoped_restaurant
from app.services.schedule_validator import validate_schedule
from app.services.plan_quality import evaluate_plan
from test_schedule import sample
from test_travel_agent import REQUEST

NOW=datetime(2026,10,19,12,tzinfo=CHINA)


def fixture(index=0,visiting='2026-10-20',start='17:30'):
    policy=PARKS[index];plan=sample(1);plan.city='北京'
    day=plan.days[0];plan.start_date=plan.end_date=day.date=visiting;day.start_time=start;day.end_time='22:00'
    place=day.attractions[0];place.name=policy['names'][0];place.poi_id=policy['poi_id'];place.visit_duration=30
    left,bottom,right,top=policy['bounds'];place.location=Location(longitude=(left+right)/2,latitude=(bottom+top)/2)
    place.description='游览'+policy['inner_names'][0];day.identify_visits()
    request=REQUEST.model_copy(update={'city':'北京','start_date':visiting,'end_date':visiting,'daily_end_time':'22:00'})
    return plan,request


class ParkScopesTests(unittest.IsolatedAsyncioTestCase):
    def test_official_reminders_distinguish_park_inner_venues_and_ticket_scope(self):
        for index in (0,1):
            plan,_=fixture(index)
            item=visit_reminders(plan,now=NOW)[0]
            self.assertEqual(item['policy_status'],'reviewed')
            self.assertEqual(item['policy_reviewed_on'],'2026-10-19')
            self.assertEqual(item['policy_source_url'],PARKS[index]['source_url'])
            self.assertIn('17:30',item['message']);self.assertIn('周一',item['message'])
            self.assertIsNone(item['booking_opens_at'])
            self.assertEqual(item['booking_status'],'unverified')
        plan,_=fixture(1,'2026-11-01')
        self.assertIn('16:30',visit_reminders(plan,now=NOW)[0]['message'])

    async def test_arrival_at_last_admission_is_too_late_for_inner_venues(self):
        for index in (0,1):
            plan,request=fixture(index)
            await validate_schedule(plan,request,AsyncMock())
            issues=park_scope_issues(plan,request,now=NOW)
            self.assertIn('inner_venue_last_entry',[i['code'] for i in issues])
            self.assertIn('park_ticket_scope_unconfirmed',[i['code'] for i in issues])
            self.assertTrue(all(i['source_url']==PARKS[index]['source_url'] for i in issues))
            with patch('app.services.park_scopes.datetime') as clock:
                clock.now.return_value=NOW
                self.assertFalse(evaluate_plan(plan,request)['schedule_passed'])

    async def test_outer_park_walk_still_respects_its_own_entry_and_closing_times(self):
        plan,request=fixture(1,start='19:00');plan.days[0].attractions[0].description='公园散步'
        await validate_schedule(plan,request,AsyncMock())
        codes=[i['code'] for i in park_scope_issues(plan,request,now=NOW)]
        self.assertIn('park_last_entry',codes);self.assertNotIn('inner_venue_last_entry',codes)
        plan,request=fixture(1,start='18:30');plan.days[0].attractions[0].description='公园散步'
        plan.days[0].attractions[0].visit_duration=120
        await validate_schedule(plan,request,AsyncMock())
        self.assertIn('park_closing_time',[i['code'] for i in park_scope_issues(plan,request,now=NOW)])

    async def test_whole_park_duration_crossing_inner_close_is_not_proof_of_late_entry(self):
        plan,request=fixture(start='17:00');plan.days[0].attractions[0].visit_duration=120
        await validate_schedule(plan,request,AsyncMock())
        issues=park_scope_issues(plan,request,now=NOW)
        self.assertNotIn('inner_venue_last_entry',[i['code'] for i in issues])
        self.assertTrue(any(i['code']=='park_ticket_scope_unconfirmed' for i in issues))

    def test_monday_closure_is_distinct_from_unconfirmed_holiday_exceptions(self):
        for day,code in (('2026-10-26','inner_venue_closed'),('2026-10-05','inner_venue_holiday_unconfirmed'),
                         ('2027-01-11','inner_venue_holiday_unconfirmed')):
            plan,request=fixture(visiting=day)
            codes=[i['code'] for i in park_scope_issues(plan,request,now=NOW)]
            self.assertIn(code,codes)
            if 'unconfirmed' in code:self.assertNotIn('inner_venue_closed',codes)

    def test_park_walk_negated_inner_visit_and_exterior_view_are_not_forced_inside(self):
        for text in ('只在公园散步','不去祈年殿，只在公园散步','只看祈年殿外观'):
            plan,request=fixture();plan.days[0].attractions[0].description=text
            before=plan.model_dump()
            self.assertEqual(park_scope_issues(plan,request,now=NOW),[])
            self.assertEqual(plan.model_dump(),before)

    async def test_missing_or_stale_timeline_does_not_supply_an_arrival_time(self):
        plan,request=fixture();self.assertIn('inner_venue_time_unverified',[i['code'] for i in park_scope_issues(plan,request,now=NOW)])
        await validate_schedule(plan,request,AsyncMock());plan.days[0].attractions[0].visit_duration+=10
        self.assertIn('inner_venue_time_unverified',[i['code'] for i in park_scope_issues(plan,request,now=NOW)])

    def test_expired_policy_does_not_apply_precise_times_and_identity_is_scoped(self):
        plan,request=fixture()
        issues=park_scope_issues(plan,request,now=datetime(2026,11,20,tzinfo=CHINA))
        self.assertEqual([i['code'] for i in issues],['park_scope_expired'])
        place=plan.days[0].attractions[0]
        self.assertIsNone(matching_park('杭州',place))
        self.assertIsNone(matching_park('北京',place.model_copy(update={'poi_id':'another'})))
        self.assertIsNone(matching_park('北京',place.model_copy(update={'location':Location(longitude=120,latitude=30)})))

    def test_new_park_references_do_not_change_the_existing_museum_restaurant_filter(self):
        for policy in PARKS:
            left,bottom,right,top=policy['bounds']
            self.assertIsNone(scoped_restaurant({'parent_poi_id':policy['poi_id'],'coordinates':[(left+right)/2,(bottom+top)/2]}))
