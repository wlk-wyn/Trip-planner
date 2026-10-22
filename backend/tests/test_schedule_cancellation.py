"""A cancelled planning owner must not leave traffic requests running."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, MockTransport, Response

from app.api.routes import saved_trips
from app.services import planning_runs as jobs
from app.services import amap_rest as amap, provider_limits
from app.services import schedule_validator as routes
from app.services.trip_repository import TripRepository
from test_schedule import sample
from test_travel_agent import REQUEST


class ScheduleCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_preview_deadline_preserves_finished_routes_and_drains_pending(self):
        calls = 0
        cancelled = asyncio.Event()
        async def provider(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {'duration_minutes': 5, 'distance_meters': 100}
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        plan = sample(3)
        await asyncio.wait_for(routes.validate_schedule(plan, REQUEST, provider, route_timeout_seconds=.03), 2)
        segments = plan.days[0].schedule.segments
        self.assertTrue(any(s.status == 'verified' for s in segments))
        self.assertTrue(any(s.status == 'unverified' and s.duration_minutes is None for s in segments))
        self.assertEqual(plan.days[0].schedule.status, 'unverified')
        self.assertTrue(cancelled.is_set())

    async def test_invalid_route_deadline_starts_no_provider_work(self):
        for timeout in (0, -1, float('nan'), float('inf')):
            provider = AsyncMock()
            with self.assertRaises(ValueError):
                await routes.validate_schedule(sample(2), REQUEST, provider, route_timeout_seconds=timeout)
            provider.assert_not_awaited()

    async def test_real_pacing_queue_stops_sending_after_cancel(self):
        entered=asyncio.Event();sent=[];blocked=True
        async def transport(request):
            sent.append(request.url.path);entered.set()
            if blocked:await asyncio.Event().wait()
            return Response(200,json={'status':'1','route':{
                'paths':[{'duration':'300','distance':'900'}],
                'transits':[{'duration':'300','distance':'900'}]}})
        settings=SimpleNamespace(amap_api_key='synthetic-cancellation-test',amap_route_qps=10)
        routes._route_cache.clear()
        with tempfile.TemporaryDirectory() as folder:
            async with AsyncClient(transport=MockTransport(transport)) as http:
                with patch.object(amap,'get_settings',return_value=settings),patch.object(amap,'_shared_client',return_value=http),\
                     patch.object(provider_limits,'default_store',return_value=Path(folder)/'permits.sqlite3'),\
                     patch.object(routes,'_best_effort_disk',AsyncMock(return_value=None)):
                    work=asyncio.create_task(routes.validate_schedule(sample(8),REQUEST))
                    try:
                        await asyncio.wait_for(entered.wait(),2)
                        work.cancel()
                        with self.assertRaises(asyncio.CancelledError):await work
                        self.assertFalse(routes._inflight)
                        await asyncio.sleep(.13)  # More than one configured permit interval.
                        self.assertEqual(len(sent),1,'abandoned requests spent another provider permit')
                        blocked=False
                        fresh=await asyncio.wait_for(routes.validate_schedule(sample(2),REQUEST),2)
                        self.assertEqual(fresh.days[0].schedule.segments[0].status,'verified')
                        self.assertEqual(len(sent),2)
                    finally:
                        work.cancel();await asyncio.gather(work,return_exceptions=True)
                        await routes.close_route_queries();routes._route_cache.clear()

    async def test_worker_thread_notifications_wake_the_subscribing_event_loop(self):
        run_id=str(uuid4());event=jobs.subscribe(run_id)
        waiter=asyncio.create_task(event.wait())
        try:
            await asyncio.sleep(0)
            await asyncio.to_thread(jobs.notify,run_id)
            self.assertTrue(await asyncio.wait_for(waiter,1))
        finally:
            jobs.unsubscribe(run_id,event)
            waiter.cancel();await asyncio.gather(waiter,return_exceptions=True)
        self.assertNotIn(run_id,jobs._listeners)

    async def test_repeated_cancel_waits_for_provider_cleanup(self):
        entered,cleaning,release=asyncio.Event(),asyncio.Event(),asyncio.Event()
        async def provider(*args):
            entered.set()
            try:await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
        work=asyncio.create_task(routes.validate_schedule(sample(),REQUEST,provider))
        try:
            await asyncio.wait_for(entered.wait(),2)
            work.cancel();await asyncio.wait_for(cleaning.wait(),2)
            work.cancel();await asyncio.sleep(0)
            self.assertFalse(work.done(),'owner acknowledged cancellation before provider cleanup')
            release.set()
            with self.assertRaises(asyncio.CancelledError):await work
        finally:
            release.set();work.cancel()
            await asyncio.gather(work,return_exceptions=True)

    async def test_cancelled_validation_does_not_cancel_another_shared_consumer(self):
        entered,release,cancelled=asyncio.Event(),asyncio.Event(),asyncio.Event()
        async def provider(*args):
            entered.set()
            try:
                await release.wait()
                return {'duration_minutes':5,'distance_meters':100}
            except asyncio.CancelledError:
                cancelled.set();raise
        routes._route_cache.clear()
        with patch.object(routes,'get_route',provider),patch.object(routes,'_best_effort_disk',AsyncMock(return_value=None)):
            first=asyncio.create_task(routes.validate_schedule(sample(),REQUEST))
            second=asyncio.create_task(routes.validate_schedule(sample(),REQUEST))
            try:
                await asyncio.wait_for(entered.wait(),2)
                async with asyncio.timeout(2):
                    while not routes._inflight or next(iter(routes._inflight.values())).waiters!=2:
                        await asyncio.sleep(0)
                first.cancel()
                with self.assertRaises(asyncio.CancelledError):await first
                self.assertFalse(cancelled.is_set())
                release.set();result=await second
                self.assertEqual(result.days[0].schedule.segments[0].status,'verified')
                self.assertFalse(routes._inflight)
            finally:
                release.set();first.cancel();second.cancel()
                await asyncio.gather(first,second,return_exceptions=True)
                await routes.close_route_queries();routes._route_cache.clear()

    async def test_cancel_drains_running_and_queued_requests_before_returning(self):
        plan=sample(8)
        started=asyncio.Event()
        active=set();entered=[];finished=[]
        async def provider(*args):
            task=asyncio.current_task();active.add(task);entered.append(task)
            if len(active)==4:started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active.discard(task);finished.append(task)
        work=asyncio.create_task(routes.validate_schedule(plan,REQUEST,provider))
        try:
            await asyncio.wait_for(started.wait(),2)
            work.cancel()
            with self.assertRaises(asyncio.CancelledError):await work
            self.assertFalse(active,'cancelled traffic requests survived their planning owner')
            self.assertEqual(len(entered),4,'queued requests should never reach the provider')
            self.assertEqual(len(finished),4)
            self.assertIsNone(plan.days[0].schedule)
            # A fresh validation can use all permits immediately.
            fresh=AsyncMock(return_value={'duration_minutes':5,'distance_meters':100})
            await routes.validate_schedule(sample(2),REQUEST,fresh)
            fresh.assert_awaited_once()
        finally:
            work.cancel()
            await asyncio.gather(work,return_exceptions=True)
            # Clean up even on the pre-fix failure, so this regression is isolated.
            for task in active.copy():task.cancel()
            await asyncio.gather(*active.copy(),return_exceptions=True)

    async def test_deadline_drains_children_and_keeps_unknown_transport(self):
        plan=sample(3)
        began=asyncio.Event();finished=[]
        async def provider(*args):
            began.set()
            try:await asyncio.Event().wait()
            finally:finished.append(True)
        original_wait=asyncio.wait
        async def short_deadline(tasks,timeout):
            await began.wait()
            return await original_wait(tasks,timeout=0)
        with patch.object(routes.asyncio,'wait',short_deadline):
            await routes.validate_schedule(plan,REQUEST,provider)
        self.assertEqual(len(finished),2)
        self.assertEqual(plan.days[0].schedule.status,'unverified')
        self.assertTrue(all(s.duration_minutes is None for s in plan.days[0].schedule.segments))
        self.assertTrue(all('超时' in s.note for s in plan.days[0].schedule.segments))

    async def test_api_cancel_drains_requests_and_does_not_save_a_partial_trip(self):
        entered=asyncio.Event();active=set();requests=[]
        async def provider(*args):
            task=asyncio.current_task();active.add(task);requests.append(task)
            if len(active)==4:entered.set()
            try:await asyncio.Event().wait()
            finally:active.discard(task)
        async def generate(request,run_id,progress):
            return await routes.validate_schedule(sample(8),request,provider,progress)
        with tempfile.TemporaryDirectory() as folder:
            repo=TripRepository(Path(folder)/'jobs.sqlite3');owner=str(uuid4())
            app=FastAPI();app.include_router(saved_trips.router,prefix='/api')
            app.dependency_overrides[saved_trips.repository]=lambda:repo
            with patch.object(jobs,'_generate',generate),patch.object(jobs,'_estimate_tickets',AsyncMock()):
                try:
                    async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':owner}) as client:
                        started=await client.post('/api/trips/plan-runs',json={'request':REQUEST.model_dump(),'request_id':str(uuid4())})
                        self.assertEqual(started.status_code,202,started.text)
                        run_id=started.json()['id']
                        await asyncio.wait_for(entered.wait(),3)
                        worker=jobs._tasks[(str(repo.path.resolve()),run_id)]
                        cancelled=await client.post(f'/api/trips/plan-runs/{run_id}/cancel')
                        self.assertEqual(cancelled.status_code,200,cancelled.text)
                        await asyncio.wait_for(worker,3)
                        self.assertFalse(active)
                        self.assertEqual(len(requests),4)
                        state=await client.get(f'/api/trips/plan-runs/{run_id}')
                        self.assertEqual(state.json()['status'],'cancelled')
                        self.assertEqual((await client.get('/api/trips')).json(),[])
                        with repo.connect() as db:
                            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0],0)
                finally:
                    await jobs.shutdown_runs()
                    for task in active.copy():task.cancel()
                    await asyncio.gather(*active.copy(),return_exceptions=True)
