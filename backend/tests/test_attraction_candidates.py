import copy
import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from app.services.attraction_candidates import select_candidates, overlap_issues, validate_initial_candidates
from app.services.amap_rest import _parse_pois
from app.services.place_facts import apply_provider_facts
from app.services.fact_evidence import reconcile_manual_facts
from app.models.schemas import Location
from app.agents.travel_agent import _evidence
from test_travel_agent import REQUEST, make_agent, FakeModel
from test_schedule import sample

NOW = datetime.now(timezone.utc).isoformat()
ROOT = dict(poi_id='B000A8UIN8', name='故宫博物院', coordinates=[116.397029,39.917839], address='景山前街4号', category='风景名胜', observed_at=NOW)
CHILD = dict(poi_id='B000A9PJ61', parent_poi_id=ROOT['poi_id'], name='故宫博物院-养心殿', coordinates=[116.395676,39.919765], address='景山前街4号故宫内', category='风景名胜', observed_at=NOW)


def nested():
    plan = sample(2)
    for place, poi in zip(plan.days[0].attractions, (ROOT, CHILD)):
        place.name=poi['name'];place.poi_id=poi['poi_id'];place.address=poi['address']
        place.location=Location(longitude=poi['coordinates'][0],latitude=poi['coordinates'][1])
        apply_provider_facts(place,poi)
    return plan


class AttractionCandidateTests(unittest.TestCase):
    def test_real_parent_relationship_is_preserved_and_child_is_not_a_second_default_visit(self):
        rows=_parse_pois({'pois':[
            dict(id=ROOT['poi_id'],name=ROOT['name'],location='116.397029,39.917839',parent=[]),
            dict(id=CHILD['poi_id'],name=CHILD['name'],location='116.395676,39.919765',parent=ROOT['poi_id'])]})
        self.assertEqual(rows[1]['parent_poi_id'],ROOT['poi_id'])
        kept,notes=select_candidates(rows)
        self.assertEqual([p['poi_id'] for p in kept],[ROOT['poi_id']])
        self.assertIn('登记属于',notes[0])

    def test_explicit_pavilion_request_and_strict_reference_are_preserved(self):
        for request in (REQUEST.model_copy(update={'free_text_input':'想去养心殿看看'}),REQUEST.model_copy(update={'reference_mode':'strict'})):
            self.assertEqual(len(select_candidates([ROOT,CHILD],request)[0]),2)
        self.assertEqual(len(select_candidates([ROOT,CHILD],keyword='养心殿')[0]),2)

    def test_missing_parent_far_coordinates_and_cycles_do_not_silently_erase_visits(self):
        self.assertEqual(select_candidates([CHILD])[0],[CHILD])
        far={**ROOT,'coordinates':[120,30]}
        self.assertEqual(len(select_candidates([far,CHILD])[0]),2)
        cycle=[{**ROOT,'parent_poi_id':'third'},CHILD,dict(poi_id='third',parent_poi_id=CHILD['poi_id'],name='第三展馆',coordinates=ROOT['coordinates'])]
        self.assertEqual(len(select_candidates(cycle)[0]),3)

    def test_facility_filter_does_not_treat_historic_gate_as_a_parking_facility(self):
        rows=[dict(poi_id='p',name='故宫博物院-停车场'),dict(poi_id='t',name='故宫博物院-售票处'),
              dict(poi_id='gate',name='中华门'),dict(poi_id='west',name='古城西门')]
        self.assertEqual([p['poi_id'] for p in select_candidates(rows)[0]],['gate','west'])
        self.assertEqual(len(select_candidates(rows,keyword='故宫博物院-停车场')[0]),3)

    def test_malformed_parent_metadata_and_self_links_are_not_relationships(self):
        for parent in ([],{},True,'','0','B000A8UIN8','bad relation'):
            row=_parse_pois({'pois':[dict(id=ROOT['poi_id'],name=ROOT['name'],location='116,39',parent=parent)]})[0]
            self.assertIsNone(row['parent_poi_id'])

    def test_same_category_or_name_prefix_without_parent_id_never_proves_containment(self):
        child={k:v for k,v in CHILD.items() if k!='parent_poi_id'}
        self.assertEqual(len(select_candidates([ROOT,child])[0]),2)

    def test_overlap_warns_without_forbidding_a_legitimate_separate_visit(self):
        plan=nested()
        self.assertEqual(overlap_issues(plan,REQUEST)[0]['severity'],'warning')
        validate_initial_candidates(plan,REQUEST)
        request=REQUEST.model_copy(update={'free_text_input':'想去养心殿'})
        validate_initial_candidates(plan,request)
        self.assertEqual(overlap_issues(plan,request)[0]['severity'],'info')

    def test_new_unrequested_facility_is_rejected_but_explicit_choice_is_kept(self):
        plan=sample(1)
        plan.days[0].attractions[0].name='景区-停车场'
        with self.assertRaisesRegex(ValueError,'配套设施'):
            validate_initial_candidates(plan,REQUEST)
        validate_initial_candidates(plan,REQUEST.model_copy(update={'free_text_input':'先去景区-停车场'}))

    def test_unobserved_or_stale_relationship_is_not_an_assertion(self):
        plan=nested();child=plan.days[0].attractions[1]
        for stamp in (None,(datetime.now(timezone.utc)-timedelta(days=31)).isoformat()):
            apply_provider_facts(child,{**CHILD,'observed_at':stamp})
            self.assertEqual(overlap_issues(plan,REQUEST),[])

    def test_manual_post_cannot_forge_relationship_but_unchanged_visit_keeps_server_evidence(self):
        previous=nested();plan=previous.model_copy(deep=True)
        child=plan.days[0].attractions[1]
        child.parent_poi_id='invented'
        reconcile_manual_facts(plan,previous)
        self.assertEqual(child.parent_poi_id,ROOT['poi_id'])
        child.poi_id='replacement'
        reconcile_manual_facts(plan,previous)
        self.assertIsNone(child.parent_poi_id)
        self.assertIsNone(child.parent_evidence)

    def test_current_plan_evidence_keeps_observed_relationship_for_a_revision(self):
        plan=nested()
        row=_evidence([],plan)['by_id']['attractions'][CHILD['poi_id']]
        self.assertEqual(row['parent_poi_id'],ROOT['poi_id'])
        self.assertEqual(row['observed_at'],NOW)

    def test_filter_is_read_only(self):
        rows=[ROOT,CHILD];before=copy.deepcopy(rows)
        select_candidates(rows)
        self.assertEqual(rows,before)

    def test_snack_title_does_not_automatically_fill_a_full_meal_but_breakfast_and_dining_venue_remain(self):
        from app.services.restaurant_candidates import suitable_restaurant
        self.assertFalse(suitable_restaurant('东坡烧饼','lunch','餐饮服务;中餐厅;中餐厅'))
        self.assertFalse(suitable_restaurant('东坡烧饼','dinner','餐饮服务;中餐厅;中餐厅'))
        self.assertTrue(suitable_restaurant('东坡烧饼','breakfast','餐饮服务;中餐厅;中餐厅'))
        self.assertTrue(suitable_restaurant('烧饼家常菜馆','lunch','餐饮服务;中餐厅;中餐厅'))

    def test_known_reservation_venue_restaurant_is_not_automatically_treated_as_street_dining(self):
        from app.services.restaurant_candidates import automatic_restaurant
        poi=dict(name='坤宁宫东院餐厅',parent_poi_id=ROOT['poi_id'],coordinates=[116.397327,39.921089],category='餐饮服务;中餐厅;中餐厅')
        self.assertFalse(automatic_restaurant(poi,'lunch'))
        self.assertTrue(automatic_restaurant({**poi,'parent_poi_id':None},'lunch'))
        self.assertTrue(automatic_restaurant({**poi,'coordinates':[120,30]},'lunch'))
        self.assertTrue(automatic_restaurant({**poi,'parent_poi_id':'unknown-park'},'lunch'))
        self.assertFalse(automatic_restaurant({**poi,'parent_poi_id':'selected-park'},'lunch',visit_parent_ids={'selected-park'}))
        for word in ('冰激淋','冰激凌','冰淇淋','冰淇凌'):
            self.assertFalse(automatic_restaurant({'name':f'尹三豆汁·尹三{word}'},'dinner'))

    def test_existing_automatic_inside_meal_has_an_access_warning_but_an_explicit_choice_is_kept(self):
        from app.models.schemas import Meal
        from app.services.restaurant_candidates import meal_identity_issues
        plan=sample(1)
        meal=Meal(type='lunch',restaurant='坤宁宫东院餐厅',name='',poi_id='inside-restaurant',route_matched=True,
                  location=Location(longitude=116.397327,latitude=39.921089),category='餐饮服务;中餐厅;中餐厅')
        apply_provider_facts(meal,dict(parent_poi_id=ROOT['poi_id'],observed_at=NOW,category=meal.category))
        plan.days[0].meals=[meal];plan.days[0].identify_visits()
        issues=meal_identity_issues(plan,REQUEST)
        self.assertEqual(issues[0]['code'],'automatic_meal_access')
        self.assertIn('不能直接视为场外餐厅',issues[0]['message'])
        request=REQUEST.model_copy(update={'free_text_input':'午餐想在坤宁宫东院餐厅吃'})
        self.assertEqual(meal_identity_issues(plan,request),[])

    def test_manual_restaurant_identity_change_does_not_keep_parent_evidence(self):
        from app.models.schemas import Meal
        plan=sample(1)
        meal=Meal(type='lunch',restaurant='坤宁宫东院餐厅',name='',poi_id='inside-restaurant',
                  location=Location(longitude=116.397327,latitude=39.921089))
        apply_provider_facts(meal,dict(parent_poi_id=ROOT['poi_id'],observed_at=NOW))
        plan.days[0].meals=[meal];plan.days[0].identify_visits()
        previous=plan.model_copy(deep=True)
        meal.restaurant='其他餐厅'
        reconcile_manual_facts(plan,previous)
        self.assertIsNone(meal.parent_poi_id)
        self.assertIsNone(meal.parent_evidence)

    def test_explicit_three_meals_missing_breakfast_is_not_only_an_informational_hint(self):
        from app.services.plan_checks import run_checks
        plan=sample(1)
        for text, severity in (('正常吃早午晚三餐','warning'),('不要安排三餐','info'),('不要早午晚三餐','info'),('不要太赶，正常吃早午晚三餐','warning')):
            issues=run_checks(plan,REQUEST.model_copy(update={'free_text_input':text}))
            missing=[i for i in issues if i['code']=='missing_meal']
            self.assertEqual(missing[0]['severity'],severity)
        issues=run_checks(plan,REQUEST.model_copy(update={'free_text_input':'正常三餐，不吃早餐'}))
        self.assertFalse(any('早餐' in i['message'] for i in issues if i['code']=='missing_meal'))


class CandidateToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_imported_client_relationship_is_not_promoted_to_server_provider_evidence(self):
        import tempfile
        from pathlib import Path
        from app.api.routes.saved_trips import create_trip, CreateTrip
        from app.services.trip_repository import TripRepository
        plan=nested()
        request=REQUEST.model_copy(update={'city':plan.city,'start_date':plan.start_date,'end_date':plan.end_date,'travel_days':len(plan.days)})
        with tempfile.TemporaryDirectory() as folder:
            repo=TripRepository(Path(folder)/'trips.sqlite3')
            saved=create_trip(CreateTrip(request=request,plan=plan),owner='public-test',repo=repo)
        for place in saved['plan']['days'][0]['attractions']:
            self.assertIsNone(place['parent_poi_id'])
            self.assertIsNone(place['parent_evidence'])

    async def test_agent_search_filters_default_child_but_keeps_a_named_child(self):
        agent=make_agent(FakeModel([]))
        with patch('app.agents.travel_agent.search_pois',new=AsyncMock(return_value=[ROOT,CHILD])):
            tool=agent._tools(REQUEST)[0]
            generic=json.loads(await tool.ainvoke({'keyword':'历史文化'}))
            named=json.loads(await tool.ainvoke({'keyword':'养心殿'}))
        self.assertEqual([p['poi_id'] for p in generic['pois']],[ROOT['poi_id']])
        self.assertTrue(generic['candidate_notes'])
        self.assertEqual(len(named['pois']),2)
