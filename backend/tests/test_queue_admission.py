"""Admission is atomic across connections; pending work remains retryable."""
import asyncio
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from app.api.routes import saved_trips
from app.services import planning_runs as jobs
from app.services.queue_admission import QueueCapacityError
from app.services.run_leases import claim
from app.services.trip_repository import TripRepository
from test_travel_agent import REQUEST, PLAN


class AdmissionTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = TripRepository(Path(self.temp.name)/'jobs.sqlite3')
        self.settings = SimpleNamespace(planning_max_pending=3, planning_max_pending_per_owner=2)
        self.settings_patch = patch.object(jobs, 'get_settings', return_value=self.settings)
        self.settings_patch.start()
        self.launch_patch = patch.object(jobs, 'launch')
        self.launch = self.launch_patch.start()
        self.owner = str(uuid4())

    async def asyncTearDown(self):
        self.launch_patch.stop()
        self.settings_patch.stop()
        await jobs.shutdown_runs()
        self.temp.cleanup()

    def create(self, owner=None, id=None):
        return jobs.start_create_run(self.repo, owner or self.owner, REQUEST.model_dump(mode='json'), id or str(uuid4()))

    def rows(self):
        with self.repo.connect() as db:
            return [dict(r) for r in db.execute('SELECT * FROM planning_runs')]

    async def test_owner_limit_counts_both_running_and_queued_and_releases_after_cancel(self):
        first=self.create()
        claim(self.repo,self.owner,first['id'],ttl=60,capacity=2,max_attempts=3)
        self.create()
        with self.assertRaises(QueueCapacityError): self.create()
        self.assertEqual(len(self.rows()),2)
        jobs.cancel_run(self.repo,self.owner,first['id'])
        self.assertEqual(self.create()['status'],'queued')

    async def test_global_limit_preserves_idempotent_retry_and_rejects_foreign_identity(self):
        run=self.create()
        self.create(str(uuid4()));self.create(str(uuid4()))
        with self.assertRaises(QueueCapacityError): self.create(str(uuid4()))
        self.assertEqual(self.create(id=run['id'])['id'],run['id'])
        with self.assertRaises(ValueError): self.create(str(uuid4()),run['id'])
        self.assertEqual(len(self.rows()),3)

    async def test_concurrent_connections_cannot_overbook_global_capacity(self):
        owners=[str(uuid4()) for _ in range(12)]
        def submit(owner):
            try: return self.create(owner)['id']
            except QueueCapacityError: return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            accepted=list(pool.map(submit,owners))
        self.assertEqual(sum(x is not None for x in accepted),3)
        self.assertEqual(len(self.rows()),3)

    async def test_concurrent_owner_submissions_cannot_bypass_owner_capacity(self):
        self.settings.planning_max_pending=20
        def submit(_):
            try: return self.create()['id']
            except QueueCapacityError: return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            accepted=list(pool.map(submit,range(8)))
        self.assertEqual(sum(x is not None for x in accepted),2)

    async def test_saved_trip_work_shares_admission_without_breaking_existing_run_retry(self):
        trip=self.repo.create(self.owner,REQUEST.model_dump(),PLAN)
        body={'kind':'chat','base_version':1,'message':'轻松一点','request_id':str(uuid4())}
        run=jobs.start_run(self.repo,self.owner,trip['id'],body)
        self.create()
        self.assertEqual(jobs.start_run(self.repo,self.owner,trip['id'],body)['id'],run['id'])
        second=self.repo.create(self.owner,REQUEST.model_dump(),PLAN)
        with self.assertRaises(QueueCapacityError):
            jobs.start_run(self.repo,self.owner,second['id'],{**body,'request_id':str(uuid4())})
        self.assertEqual(len(self.rows()),2)

    async def test_never_started_expired_queue_fails_without_lease_or_model_work(self):
        run=self.create();old=(datetime.now(timezone.utc)-timedelta(seconds=181)).isoformat()
        with self.repo.connect() as db: db.execute('UPDATE planning_runs SET created_at=? WHERE id=?',(old,run['id']))
        self.assertIsNone(claim(self.repo,self.owner,run['id'],ttl=60,capacity=2,max_attempts=3,queue_timeout=180))
        row=self.rows()[0]
        self.assertEqual((row['status'],row['stage'],row['attempt_count']),('failed','排队超时',0))
        self.assertEqual(self.repo.list(self.owner),[])
        self.assertIn('尚未开始',jobs.read_run(self.repo,self.owner,run['id'])['error'])

    async def test_previously_started_job_can_recover_even_when_created_long_ago(self):
        run=self.create();token=claim(self.repo,self.owner,run['id'],ttl=60,capacity=2,max_attempts=3)
        old=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        with self.repo.connect() as db:
            db.execute('UPDATE planning_runs SET created_at=?,lease_until=? WHERE id=?',(old,time.time()-1,run['id']))
        replacement=claim(self.repo,self.owner,run['id'],ttl=60,capacity=2,max_attempts=3)
        self.assertIsNotNone(replacement);self.assertNotEqual(replacement,token)

    async def test_locked_submit_yields_the_event_loop_and_acknowledges_after_commit(self):
        entered,release=threading.Event(),threading.Event()
        def writer():
            with self.repo.connect() as db:
                db.execute('BEGIN IMMEDIATE');entered.set();release.wait(2)
        thread=threading.Thread(target=writer);thread.start()
        self.assertTrue(await asyncio.to_thread(entered.wait,1))
        pending=asyncio.create_task(jobs.enqueue_create_run(self.repo,self.owner,REQUEST.model_dump(),str(uuid4())))
        try:
            await asyncio.sleep(.05)
            self.assertFalse(pending.done(),'SQLite lock must not stall the event loop until the holder times out')
        finally:
            release.set();await asyncio.to_thread(thread.join)
        run=await asyncio.wait_for(pending,2)
        self.launch.assert_called_once_with(self.repo,self.owner,run['id'])
        self.assertEqual(len(self.rows()),1)

    async def test_api_rejects_overload_with_retry_header_and_keeps_existing_request_readable(self):
        app=FastAPI();app.include_router(saved_trips.router,prefix='/api')
        app.dependency_overrides[saved_trips.repository]=lambda:self.repo
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':self.owner}) as client:
            body={'request':REQUEST.model_dump(mode='json'),'request_id':str(uuid4())}
            first=await client.post('/api/trips/plan-runs',json=body)
            self.assertEqual(first.status_code,202)
            second=await client.post('/api/trips/plan-runs',json={**body,'request_id':str(uuid4())})
            self.assertEqual(second.status_code,202)
            rejected=await client.post('/api/trips/plan-runs',json={**body,'request_id':str(uuid4())})
            self.assertEqual(rejected.status_code,429)
            self.assertEqual(rejected.headers['Retry-After'],'15')
            self.assertIsInstance(rejected.json()['detail'],str)
            again=await client.post('/api/trips/plan-runs',json=body)
            self.assertEqual(again.status_code,202)
            self.assertEqual(again.json()['id'],first.json()['id'])
            self.assertEqual(len(self.rows()),2)
