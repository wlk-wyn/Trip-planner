"""Fenced job ownership: duplicates, takeover, cancellation, and queue recovery."""
import asyncio
import json
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.models.schemas import TripPlan
from app.services import planning_runs as jobs
from app.services.run_leases import claim, renew, release, owned_update, RunLeaseLost
from app.services.trip_repository import TripRepository
from test_travel_agent import PLAN, REQUEST

OWNER = 'synthetic-lease-owner'


class LeaseTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = TripRepository(Path(self.tmp.name)/'jobs.sqlite3')
        self.ticket_patch = patch.object(jobs, '_estimate_tickets', new=AsyncMock())
        self.ticket_patch.start()

    async def asyncTearDown(self):
        await jobs.shutdown_runs()
        self.ticket_patch.stop()
        self.tmp.cleanup()

    def queue(self):
        id = str(uuid4())
        with patch.object(jobs, 'launch'):
            jobs.start_create_run(self.repo, OWNER, REQUEST.model_dump(), id)
        return id

    def claim(self, id, **kwargs):
        return claim(self.repo, OWNER, id, **({'ttl':60, 'capacity':2, 'max_attempts':3} | kwargs))

    def expire(self, id):
        with self.repo.connect() as db:
            db.execute('UPDATE planning_runs SET lease_until=? WHERE id=?', (time.time()-1, id))

    def settings(self, **changes):
        return SimpleNamespace(**({'planning_lease_seconds':.6, 'planning_max_running':2,
                                  'planning_queue_timeout_seconds':180,
                                  'planning_max_attempts':3, 'planning_run_timeout_seconds':5} | changes))

    async def test_concurrent_claims_have_one_owner_and_do_not_expose_internal_token(self):
        id = self.queue()
        tokens = await asyncio.gather(*(asyncio.to_thread(self.claim, id) for _ in range(8)))
        self.assertEqual(sum(token is not None for token in tokens), 1)
        token = next(token for token in tokens if token)
        self.assertTrue(renew(self.repo, OWNER, id, token, 60))
        self.assertFalse(renew(self.repo, OWNER, id, 'other', 60))
        public = jobs.read_run(self.repo, OWNER, id)
        for key in ('lease_token', 'lease_until', 'attempt_count', 'owner', 'payload'):
            self.assertNotIn(key, public)

    async def test_takeover_fences_progress_prepared_checkpoint_and_trip_creation(self):
        id = self.queue(); old = self.claim(id)
        self.expire(id); current = self.claim(id)
        self.assertNotEqual(current, old)
        with self.assertRaises(RunLeaseLost):
            owned_update(self.repo, OWNER, id, old, {'payload':'{}'})
        with self.assertRaises(RunLeaseLost):
            owned_update(self.repo, OWNER, id, old, {'stage':'stale progress'})
        with self.assertRaises(RunLeaseLost):
            self.repo.create(OWNER, REQUEST.model_dump(), PLAN, run_id=id, lease_token=old)
        self.assertEqual(self.repo.list(OWNER), [])
        self.repo.create(OWNER, REQUEST.model_dump(), PLAN, run_id=id, lease_token=current)
        self.repo.create(OWNER, REQUEST.model_dump(), PLAN, run_id=id, lease_token=old)
        self.assertEqual(len(self.repo.list(OWNER)), 1)  # terminal replay is read-only

    async def test_takeover_cannot_modify_a_trip_with_the_stale_token(self):
        trip = self.repo.create(OWNER, REQUEST.model_dump(), PLAN)
        body = dict(kind='optimize', date=PLAN['start_date'], base_version=1, request_id=str(uuid4()))
        with patch.object(jobs, 'launch'):
            job = jobs.start_run(self.repo, OWNER, trip['id'], body)
        old = self.claim(job['id']); self.expire(job['id']); current = self.claim(job['id'])
        edited = TripPlan(**PLAN); edited.days[0].attractions[0].visit_duration = 45
        with self.assertRaises(RunLeaseLost):
            self.repo.commit(OWNER, trip['id'], 1, edited.model_dump(), 'optimize', run_id=job['id'], lease_token=old)
        self.assertEqual(self.repo.read(OWNER, trip['id'])['version'], 1)
        self.repo.commit(OWNER, trip['id'], 1, edited.model_dump(), 'optimize', run_id=job['id'], lease_token=current)
        self.assertEqual(self.repo.read(OWNER, trip['id'])['version'], 2)

    async def test_two_executors_generate_once_and_heartbeat_keeps_a_longer_job_owned(self):
        id = self.queue(); entered = asyncio.Event()
        async def generate(*args):
            entered.set(); await asyncio.sleep(1.2)
            return TripPlan(**PLAN)
        with patch.object(jobs, 'get_settings', return_value=self.settings()), patch.object(jobs, '_generate', new=AsyncMock(side_effect=generate)) as model:
            first = asyncio.create_task(jobs.execute(self.repo, OWNER, id))
            await asyncio.wait_for(entered.wait(), 1)
            await asyncio.sleep(.8)
            await jobs.execute(TripRepository(self.repo.path), OWNER, id)
            await first
        self.assertEqual(model.await_count, 1)
        self.assertEqual(jobs.read_run(self.repo, OWNER, id)['status'], 'succeeded')
        self.assertEqual(len(self.repo.list(OWNER)), 1)

    async def test_remote_cancellation_is_seen_by_heartbeat_and_does_not_save(self):
        id = self.queue(); entered = asyncio.Event()
        async def generate(*args):
            entered.set(); await asyncio.Event().wait()
        with patch.object(jobs, 'get_settings', return_value=self.settings()), patch.object(jobs, '_generate', new=generate):
            executor = asyncio.create_task(jobs.execute(self.repo, OWNER, id))
            await asyncio.wait_for(entered.wait(), 1)
            jobs.cancel_run(self.repo, OWNER, id)  # executor was not in this process's _tasks
            await asyncio.wait_for(executor, 1)
        self.assertEqual(jobs.read_run(self.repo, OWNER, id)['status'], 'cancelled')
        self.assertEqual(self.repo.list(OWNER), [])

    async def test_graceful_interruption_releases_job_for_immediate_restart(self):
        id = self.queue(); entered = asyncio.Event()
        async def generate(*args):
            entered.set(); await asyncio.Event().wait()
        with patch.object(jobs, '_generate', new=generate):
            executor = asyncio.create_task(jobs.execute(self.repo, OWNER, id))
            await asyncio.wait_for(entered.wait(), 1)
            executor.cancel(); await executor
        self.assertEqual(jobs.read_run(self.repo, OWNER, id)['status'], 'queued')
        with patch.object(jobs, '_generate', new=AsyncMock(return_value=TripPlan(**PLAN))):
            await jobs.execute(self.repo, OWNER, id)
        self.assertEqual(jobs.read_run(self.repo, OWNER, id)['status'], 'succeeded')

    async def test_cancelling_during_threaded_claim_does_not_orphan_a_new_lease(self):
        id = self.queue(); real_claim = jobs.claim
        def slow_claim(*args, **kwargs):
            time.sleep(.04)
            return real_claim(*args, **kwargs)
        with patch.object(jobs, 'claim', side_effect=slow_claim):
            task = asyncio.create_task(jobs.execute(self.repo, OWNER, id))
            await asyncio.sleep(.01); task.cancel()
            await asyncio.sleep(.005); task.cancel(); await task
        self.assertEqual(jobs.read_run(self.repo, OWNER, id)['status'], 'queued')
        self.assertIsNotNone(self.claim(id))

    async def test_repeated_cancellation_waits_for_sqlite_operation_to_close(self):
        closed = []
        def operation():
            with self.repo.connect() as db:
                time.sleep(.05)
                db.execute('SELECT 1').fetchone()
            closed.append(True)
        task = asyncio.create_task(jobs.database_call(operation))
        await asyncio.sleep(.01); task.cancel()
        await asyncio.sleep(.005); task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(closed, [True])

    async def test_timeout_is_terminal_and_not_requeued_or_saved(self):
        id = self.queue()
        async def slow(*args): await asyncio.sleep(10)
        with patch.object(jobs, 'get_settings', return_value=self.settings(planning_run_timeout_seconds=.05)), patch.object(jobs, '_generate', new=slow):
            await jobs.execute(self.repo, OWNER, id)
        job = jobs.read_run(self.repo, OWNER, id)
        self.assertEqual(job['status'], 'failed')
        self.assertIn('超时', job['error'])
        self.assertEqual(self.repo.list(OWNER), [])
        self.assertIsNone(self.claim(id))

    async def test_prepared_create_is_reused_but_its_quality_is_checked_again(self):
        id = self.queue()
        with self.repo.connect() as db:
            payload = json.loads(db.execute('SELECT payload FROM planning_runs WHERE id=?', (id,)).fetchone()[0])
            payload['prepared'] = {'plan':PLAN, 'extra':{'quality':{'official_hours_verified':True}}}
            db.execute('UPDATE planning_runs SET payload=? WHERE id=?', (json.dumps(payload), id))
        with patch.object(jobs, '_generate', new=AsyncMock(side_effect=AssertionError('must reuse prepared plan'))) as model:
            await jobs.execute(self.repo, OWNER, id)
        result = jobs.read_run(self.repo, OWNER, id)['result']
        self.assertFalse(result['quality']['official_hours_verified'])
        model.assert_not_awaited()
        self.assertEqual(len(self.repo.list(OWNER)), 1)

    async def test_expired_jobs_have_bounded_attempts_and_terminal_state_is_immutable(self):
        id = self.queue()
        self.claim(id, max_attempts=2); self.expire(id)
        self.claim(id, max_attempts=2); self.expire(id)
        self.assertIsNone(self.claim(id, max_attempts=2))
        self.assertEqual(jobs.read_run(self.repo, OWNER, id)['status'], 'failed')
        jobs.cancel_run(self.repo, OWNER, id)
        self.assertEqual(jobs.read_run(self.repo, OWNER, id)['status'], 'failed')

    async def test_capacity_is_shared_by_repository_connections(self):
        first, second = self.queue(), self.queue()
        token = self.claim(first, capacity=1)
        self.assertIsNone(claim(TripRepository(self.repo.path), OWNER, second, ttl=60, capacity=1, max_attempts=3))
        release(self.repo, OWNER, first, token)
        self.assertIsNotNone(self.claim(second, capacity=1))

    async def test_expiry_during_trip_transaction_rolls_back_the_entire_save(self):
        from app.services.run_leases import require_lease
        id = self.queue(); token = self.claim(id, ttl=.05)
        checked = []
        def slow_first_check(row, token):
            require_lease(row, token)
            if not checked:
                checked.append(True); time.sleep(.08)
        with patch('app.services.trip_repository.require_lease', side_effect=slow_first_check):
            with self.assertRaises(RunLeaseLost):
                self.repo.create(OWNER, REQUEST.model_dump(), PLAN, run_id=id, lease_token=token)
        self.assertEqual(self.repo.list(OWNER), [])
        with self.repo.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM versions').fetchone()[0], 0)

    async def test_background_recovery_drains_a_job_that_initially_had_no_capacity(self):
        first, second = self.queue(), self.queue()
        gate, entered = asyncio.Event(), asyncio.Event()
        async def generate(request, id, progress):
            if id == first:
                entered.set(); await gate.wait()
            return TripPlan(**PLAN)
        with patch.object(jobs, 'get_settings', return_value=self.settings(planning_lease_seconds=10, planning_max_running=1)), patch.object(jobs, '_generate', new=generate):
            jobs.launch(self.repo, OWNER, first)
            await asyncio.wait_for(entered.wait(), 1)
            await jobs.execute(self.repo, OWNER, second)
            self.assertEqual(jobs.read_run(self.repo, OWNER, second)['status'], 'queued')
            jobs.start_recovery(self.repo)
            gate.set()
            async def finished():
                while jobs.read_run(self.repo, OWNER, second)['status'] != 'succeeded':
                    await asyncio.sleep(.03)
            await asyncio.wait_for(finished(), 4)
        self.assertEqual(len(self.repo.list(OWNER)), 2)

    async def test_old_schema_migration_preserves_pending_payload(self):
        path = Path(self.tmp.name)/'legacy.sqlite3'
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE planning_runs(id TEXT PRIMARY KEY,owner TEXT,trip_id TEXT,base_version INTEGER,kind TEXT,status TEXT,stage TEXT,payload TEXT,result TEXT,error TEXT,queries INTEGER,created_at TEXT,updated_at TEXT)')
            db.execute("INSERT INTO planning_runs VALUES ('legacy',?,'',0,'create','queued','prepare',?,NULL,'',0,?,?)", (OWNER, json.dumps({'body':{'kind':'create'}}), jobs.now(), jobs.now()))
            db.commit()
        repo = TripRepository(path)
        with repo.connect() as db:
            row = db.execute("SELECT * FROM planning_runs WHERE id='legacy'").fetchone()
        self.assertEqual(json.loads(row['payload'])['body']['kind'], 'create')
        self.assertEqual(row['attempt_count'], 0)
        self.assertIsNone(row['lease_token'])

    async def test_readiness_requires_a_worker_and_accessible_migrated_storage(self):
        self.assertEqual((await jobs.readiness(self.repo))['status'], 'not_ready')
        jobs.start_recovery(self.repo)
        report = await jobs.readiness(self.repo)
        self.assertEqual(report['status'], 'ready')
        self.assertEqual(report['upstreams'], 'not_probed')
        with patch.object(jobs.sqlite3, 'connect', side_effect=sqlite3.OperationalError('unavailable')):
            self.assertEqual((await jobs.readiness(self.repo))['status'], 'not_ready')
