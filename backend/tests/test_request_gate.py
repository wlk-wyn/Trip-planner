import asyncio
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from collections import OrderedDict
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.routes import trip, saved_trips, poi, map as maps
from app.models.schemas import TripIntakeData
from app.services.request_gate import RequestGate, Policy, GateFull, GateUnavailable, request_group, protect_request
from app.services.trip_repository import TripRepository
from app.services.planning_runs import start_create_run
from app.services import amap_service
from app.services import amap_rest
from test_travel_agent import REQUEST


class RequestGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'gate.sqlite3'
        self.clock = 120.0
        self.gate = RequestGate(self.path, {'interactive': Policy(2,1,3,8), 'lookup': Policy(4,2,10,30)}, clock=lambda:self.clock)
        self.gate.probe()

    async def asyncTearDown(self):
        self.temp.cleanup()

    def app(self):
        app=FastAPI();app.state.request_gate=self.gate
        for router in (trip.router,saved_trips.router,poi.router,maps.router):app.include_router(router,prefix='/api')
        self.repo=TripRepository(Path(self.temp.name)/'trips.sqlite3')
        app.dependency_overrides[saved_trips.repository]=lambda:self.repo
        return app

    def leases(self):
        with closing(sqlite3.connect(self.path)) as db:return db.execute('SELECT COUNT(*) FROM http_leases').fetchone()[0]

    async def test_peer_and_global_limits_are_atomic_across_connections(self):
        def acquire(i):
            try:self.gate.acquire(str(i),'interactive','peer'+str(i));return True
            except GateFull:return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            results=list(pool.map(acquire,range(8)))
        self.assertEqual(sum(results),2);self.assertEqual(self.leases(),2)

    async def test_minute_counters_survive_release_and_rejects_do_not_spend_other_budgets(self):
        for i in range(3):self.gate.acquire(str(i),'interactive','one');self.gate.release(str(i))
        with self.assertRaises(GateFull) as caught:self.gate.acquire('full','interactive','one')
        self.assertEqual(caught.exception.retry_after,60)
        self.gate.acquire('other','interactive','two')
        self.gate.acquire('lookup','lookup','one')
        self.assertEqual(self.leases(),2)
        self.clock=180
        self.gate.acquire('reset','interactive','one')
        self.assertEqual(self.leases(),1)

    async def test_renewal_and_expiry_cannot_resurrect_an_old_permit(self):
        self.gate.acquire('old','interactive','one');self.clock=150;self.gate.renew('old')
        self.clock=190
        with self.assertRaises(GateFull):self.gate.acquire('early','interactive','one')
        self.clock=211;self.gate.acquire('new','interactive','one')
        with self.assertRaises(GateUnavailable):self.gate.renew('old')
        self.gate.release('old');self.assertEqual(self.leases(),1)

    async def test_changing_peers_does_not_bypass_the_global_minute_budget(self):
        for i in range(8):
            self.gate.acquire(str(i),'interactive',str(i));self.gate.release(str(i))
        with self.assertRaises(GateFull):self.gate.acquire('rotated','interactive','new-peer')
        self.assertEqual(self.leases(),0)

    async def test_live_heartbeat_keeps_long_requests_counted_until_completion(self):
        self.gate.clock=time.time;self.gate.ttl=.09
        self.gate.policies['interactive']=Policy(1,1,100,100)
        with patch.object(self.gate,'renew',wraps=self.gate.renew) as renewed:
            async with self.gate.permit('interactive','peer'):
                await asyncio.sleep(.2)
                with self.assertRaises(GateFull):await self.gate.database_call(self.gate.acquire,'other','interactive','other')
                self.assertGreater(renewed.call_count,0)
        self.assertEqual(self.leases(),0)

    async def test_threaded_legacy_service_initialization_is_singleton(self):
        def initialize():time.sleep(.02);return object()
        with patch.object(amap_service,'_amap_service',None),patch.object(amap_service,'AmapService',side_effect=initialize) as created:
            with ThreadPoolExecutor(max_workers=4) as pool:instances=list(pool.map(lambda _:amap_service.get_amap_service(),range(4)))
            self.assertEqual(created.call_count,1)
            self.assertTrue(all(instance is instances[0] for instance in instances))

    async def test_cancellation_during_sqlite_admission_cleans_up_committed_permit(self):
        entered, release=threading.Event(),threading.Event()
        original=self.gate.acquire
        def delayed(*args):
            original(*args);entered.set();release.wait(2)
        async def work():
            async with self.gate.permit('interactive','peer'):self.fail('cancelled admission entered handler')
        with patch.object(self.gate,'acquire',side_effect=delayed):
            task=asyncio.create_task(work())
            self.assertTrue(await asyncio.to_thread(entered.wait,1))
            task.cancel();release.set()
            with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.leases(),0)

    async def test_cancelled_handler_releases_its_permit(self):
        entered=asyncio.Event()
        async def work():
            async with self.gate.permit('interactive','peer'):
                entered.set();await asyncio.Event().wait()
        task=asyncio.create_task(work());await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        self.assertEqual(self.leases(),0)

    async def test_lost_renewal_stops_the_request_and_releases_the_slot(self):
        self.gate.ttl=.03
        async def work():
            async with self.gate.permit('interactive','peer'):await asyncio.Event().wait()
        with patch.object(self.gate,'renew',side_effect=sqlite3.OperationalError('unavailable')):
            task=asyncio.create_task(work())
            with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,1)
        self.assertEqual(self.leases(),0)

    async def test_actual_api_rejects_before_model_but_progress_cancel_and_lookup_remain_available(self):
        app=self.app();owner=str(uuid4());entered,release=asyncio.Event(),asyncio.Event()
        run=start_create_run(self.repo,owner,REQUEST.model_dump(mode='json'),str(uuid4()),schedule=False)
        async def model(_history):
            entered.set();await release.wait();return TripIntakeData(ready=False,reply='请提供出发日期')
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':owner}) as client:
            with patch.object(trip,'interpret_trip_intake',side_effect=model) as mock_model, patch.object(poi,'find_poi_photo',AsyncMock(return_value='https://example.com/public.jpg')):
                running=asyncio.create_task(client.post('/api/trip/intake',json={'history':[{'role':'user','content':'西安'}]}))
                await entered.wait()
                try:
                    denied=await client.post('/api/trip/intake',json={'history':[{'role':'user','content':'北京'}]},headers={'X-Forwarded-For':'192.0.2.50','X-Trip-Owner':str(uuid4())})
                    self.assertEqual(denied.status_code,429);self.assertEqual(denied.headers['Retry-After'],'3')
                    self.assertEqual(mock_model.call_count,1)
                    self.assertEqual((await client.get('/api/trips/plan-runs/'+run['id'])).status_code,200)
                    self.assertEqual((await client.post('/api/trips/plan-runs/'+run['id']+'/cancel')).status_code,200)
                    self.assertEqual((await client.get('/api/poi/photo',params={'name':'公开地点'})).status_code,200)
                finally:release.set();await running
        self.assertEqual(self.leases(),0)

    async def test_unavailable_coordination_rejects_before_business_work(self):
        app=self.app()
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            with patch.object(self.gate,'acquire',side_effect=sqlite3.OperationalError('store failure')),patch.object(trip,'interpret_trip_intake') as model:
                response=await client.post('/api/trip/intake',json={'history':[{'role':'user','content':'西安'}]})
                self.assertEqual(response.status_code,503);model.assert_not_called()

    async def test_map_rest_wait_does_not_block_the_event_loop(self):
        app=self.app();entered,release=asyncio.Event(),asyncio.Event()
        async def search(*_args):entered.set();await release.wait();return {'pois':[]}
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            with patch.object(amap_rest,'_poi_cache',OrderedDict()),patch.object(amap_rest,'_get_json',side_effect=search):
                task=asyncio.create_task(client.get('/api/map/poi',params={'keywords':'公开地点','city':'北京'}))
                await asyncio.wait_for(entered.wait(),1)
                self.assertFalse(task.done())
                self.assertEqual((await client.get('/api/map/health')).status_code,200)
                release.set();self.assertEqual((await task).status_code,200)
        self.assertEqual(self.leases(),0)

    async def test_health_poll_does_not_initialize_a_model_or_mcp(self):
        with patch('app.config.get_settings',return_value=SimpleNamespace(amap_api_key='synthetic',openai_api_key='synthetic')),patch.object(trip,'get_travel_agent',side_effect=AssertionError('model initialization')),patch.object(amap_service,'get_amap_service',side_effect=AssertionError('MCP initialization')):
            self.assertEqual((await trip.health_check())['upstreams'],'not_probed')
            self.assertEqual((await maps.health_check())['upstreams'],'not_probed')

    async def test_real_application_configures_the_gate_and_reports_coordination_failure(self):
        from app.api import main
        self.assertIsInstance(main.app.state.request_gate,RequestGate)
        with patch('app.services.planning_runs.readiness',AsyncMock(return_value={'status':'ready'})),patch('app.services.provider_limits.readiness',AsyncMock(return_value=True)),patch.object(main.app.state.request_gate,'probe',side_effect=OSError('not available')):
            response=await main.readiness()
            self.assertEqual(response.status_code,503)
            self.assertIn(b'http_resource_coordinator',response.body)

    async def test_every_provider_route_is_guarded_and_status_routes_are_exempt(self):
        app=self.app()
        expected={
            'intake_trip','plan_trip','chat_with_trip_agent','extract_url_content','extract_image_content',
            'recommend_trip_places','get_alternatives','search_poi_for_diy','reoptimize_route',
            'get_poi_detail','search_poi','get_attraction_photo','get_weather','plan_route','references',
            'commit_trip','chat','revalidate','weather_check','trip_prep','enrich_trip','day_fixes',
        }
        found=set()
        for route in app.routes:
            if getattr(route,'name',None) not in expected:continue
            if route.path.endswith('/health'):continue
            found.add(route.name)
            self.assertTrue(any(dep.call is protect_request for dep in route.dependant.dependencies),route.path)
            self.assertTrue(any(request_group(method,route.path) for method in route.methods),route.path)
        self.assertEqual(found,expected)
        for method,path in [('POST','/api/trips/plan-runs'),('GET','/api/trips/plan-runs/{run_id}'),('POST','/api/trips/{trip_id}/runs'),('POST','/api/trips/plan-runs/{run_id}/cancel'),('GET','/api/trips/runs/{run_id}/events')]:
            self.assertIsNone(request_group(method,path))
