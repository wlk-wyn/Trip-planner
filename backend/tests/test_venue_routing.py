import asyncio
import copy
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

from app.models.schemas import Location, RouteEndpoint, TripPlan
from app.services import schedule_validator as routes
from app.services.venue_routing import endpoint_for, gate_issues, POLICY_URL, REVIEWED_ON, GATES, GateUnavailable
from app.services.schedule_integrity import check_integrity
from app.services.schedule_solver import optimize_day
from test_schedule import sample
from test_travel_agent import REQUEST

POINTS = {'B000A84GDN': [116.397228,39.913582], 'B000A9PISW': [116.396786,39.922305]}


def gate(poi_id):
    spec=next(s for s in GATES.values() if s['poi_id']==poi_id)
    return dict(poi_id=poi_id,name=spec['name'],parent_poi_id='B000A8UIN8',coordinates=POINTS[poi_id],
                observed_at=datetime.now(timezone.utc).isoformat())


def plan():
    result=sample(2);result.city='北京'
    p=result.days[0].attractions[0]
    p.name='故宫博物院';p.poi_id='B000A8UIN8';p.location=Location(longitude=116.397029,latitude=39.917839)
    result.days[0].attractions[1].location=Location(longitude=116.4,latitude=39.93)
    result.days[0].date='2026-10-20'
    return result


class VenueRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        routes._route_cache.clear();routes._disk_disabled_until=0
        self.disk=patch.object(routes,'_disk_cache',return_value=None);self.disk.start()
        self.lookup=AsyncMock(side_effect=gate)

    async def asyncTearDown(self):
        await routes.close_route_queries();routes._route_cache.clear();self.disk.stop()

    async def test_exact_venue_uses_wumen_for_arrival_and_shenwumen_for_departure(self):
        stops=routes.stops_for_day(plan().days[0]);p=stops[0]
        before=p['place'].model_dump()
        entrance=await endpoint_for(p,'北京','entrance',self.lookup)
        exit=await endpoint_for(p,'北京','exit',self.lookup)
        self.assertEqual(entrance.poi_id,'B000A84GDN');self.assertEqual(exit.poi_id,'B000A9PISW')
        self.assertNotEqual(entrance.location,p['place'].location)
        self.assertEqual(entrance.policy_source_url,POLICY_URL)
        self.assertEqual(p['place'].model_dump(),before)

    async def test_same_name_wrong_id_city_or_coordinates_stays_a_labelled_coordinate_reference(self):
        stop=routes.stops_for_day(plan().days[0])[0]
        for city,changes in (('台北',{}),('北京',{'poi_id':'wrong'}),('北京',{'location':Location(longitude=120,latitude=30)})):
            p={**stop,'place':stop['place'].model_copy(update=changes)}
            endpoint=await endpoint_for(p,city,'entrance',self.lookup)
            self.assertEqual(endpoint.basis,'place_coordinate')
        self.lookup.assert_not_awaited()

    async def test_invalid_parent_identity_area_and_undated_gate_cannot_be_used(self):
        stop=routes.stops_for_day(plan().days[0])[0]
        original=gate('B000A84GDN')
        for change in ({'parent_poi_id':None},{'poi_id':'wrong'},{'name':'午门公交站'},
                       {'coordinates':[120,30]},{'observed_at':None},
                       {'observed_at':(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()}):
            with self.subTest(change=change):
                with self.assertRaises(GateUnavailable):
                    await endpoint_for(stop,'北京','entrance',AsyncMock(return_value={**original,**change}))

    async def test_failed_gate_lookup_does_not_fall_back_to_centre_or_invent_a_duration(self):
        stops=routes.stops_for_day(plan().days[0]);provider=AsyncMock(return_value={'duration_minutes':5})
        edge=await routes.route_segment(stops[1],stops[0],'北京','walking',asyncio.Semaphore(2),provider,gate_lookup=AsyncMock(return_value=None))
        self.assertEqual(edge.status,'unverified');self.assertIsNone(edge.duration_minutes)
        self.assertIn('未用景点中心',edge.note);provider.assert_not_awaited()

    async def test_route_call_receives_exit_and_entrance_coordinates_not_centre(self):
        stops=routes.stops_for_day(plan().days[0]);provider=AsyncMock(return_value={'duration_minutes':12,'distance_meters':800})
        outward=await routes.route_segment(stops[0],stops[1],'北京','transit',asyncio.Semaphore(2),provider,gate_lookup=self.lookup)
        self.assertEqual(provider.await_args.args[0].model_dump(),dict(longitude=116.396786,latitude=39.922305))
        inward=await routes.route_segment(stops[1],stops[0],'北京','transit',asyncio.Semaphore(2),provider,gate_lookup=self.lookup)
        self.assertEqual(provider.await_args.args[1].model_dump(),dict(longitude=116.397228,latitude=39.913582))
        self.assertEqual(outward.from_endpoint.role,'exit');self.assertEqual(inward.to_endpoint.role,'entrance')
        self.assertEqual(inward.status,'verified')

    async def test_car_route_does_not_claim_pedestrian_gate_has_zero_last_mile(self):
        stops=routes.stops_for_day(plan().days[0]);provider=AsyncMock(return_value={'duration_minutes':1})
        edge=await routes.route_segment(stops[1],stops[0],'北京','driving',asyncio.Semaphore(2),provider,gate_lookup=self.lookup)
        self.assertEqual(edge.status,'unverified');provider.assert_not_awaited()
        self.assertIn('停车或落客点',edge.note)

    async def test_centre_cache_is_not_reused_for_a_gate_route(self):
        stops=routes.stops_for_day(plan().days[0]);a,b=stops[1]['place'].location,stops[0]['place'].location
        old=('v2','北京','walking',a.longitude,a.latitude,b.longitude,b.latitude)
        routes._remember(old,{'duration_minutes':999},datetime.now(timezone.utc).isoformat())
        provider=AsyncMock(return_value={'duration_minutes':12,'distance_meters':800})
        with patch.object(routes,'get_route',provider):
            edge=await routes.route_segment(stops[1],stops[0],'北京','walking',asyncio.Semaphore(2),provider,gate_lookup=self.lookup)
        self.assertEqual(edge.duration_minutes,12);provider.assert_awaited_once()

    async def test_endpoint_geometry_and_direction_survive_serialization_but_edits_are_detected(self):
        p=plan();provider=AsyncMock(return_value={'duration_minutes':12,'distance_meters':800})
        await routes.validate_schedule(p,REQUEST.model_copy(update={'city':'北京'}),provider,gate_lookup=self.lookup)
        restored=TripPlan.model_validate_json(p.model_dump_json())
        self.assertEqual(check_integrity(restored.days[0]),[])
        edge=restored.days[0].schedule.segments[0]
        edge.from_endpoint=await endpoint_for(routes.stops_for_day(restored.days[0])[0],'北京','entrance',self.lookup)
        self.assertTrue(any('端点' in i['message'] for i in check_integrity(restored.days[0])))

    async def test_old_centre_routes_are_flagged_for_recheck_without_mutating_the_plan(self):
        p=plan();stops=routes.stops_for_day(p.days[0])
        edge=routes.RouteSegment(from_id=stops[0]['id'],to_id=stops[1]['id'],from_name='故宫博物院',to_name='第二景点',mode='walking',status='verified',duration_minutes=12)
        p.days[0].schedule=routes.build_schedule(p.days[0],stops,[edge],REQUEST)
        before=p.model_dump();issues=gate_issues(p)
        self.assertEqual(issues[0]['code'],'venue_gate_unconfirmed')
        self.assertEqual(p.model_dump(),before)

    async def test_optimizer_uses_the_same_directional_endpoints(self):
        p=plan();provider=AsyncMock(return_value={'duration_minutes':12,'distance_meters':800})
        day,result=await optimize_day(p.days[0],REQUEST,'北京',provider,budget=2,gate_lookup=self.lookup)
        self.assertEqual(result['queries'],2)
        self.assertTrue(any(call.args[0].longitude==116.396786 for call in provider.await_args_list))
        self.assertTrue(any(call.args[1].longitude==116.397228 for call in provider.await_args_list))

    async def test_cancellation_during_gate_lookup_propagates(self):
        stop=routes.stops_for_day(plan().days[0])[0]
        with self.assertRaises(asyncio.CancelledError):
            await endpoint_for(stop,'北京','entrance',AsyncMock(side_effect=asyncio.CancelledError()))
