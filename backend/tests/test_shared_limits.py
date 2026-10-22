import asyncio
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import provider_limits as limits
from app.services import amap_rest as amap


class SharedLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'limits.sqlite3'

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_atomic_attempts_grant_one_slot_and_store_no_plaintext_key(self):
        scope = limits.scope_id('synthetic-secret', 'route')
        results = await asyncio.gather(*(asyncio.to_thread(limits.attempt, self.path, scope, 10, lambda:1000) for _ in range(8)))
        self.assertEqual(results.count(0), 1)
        self.assertTrue(all(value == 0 or abs(value-.1)<.00001 for value in results))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT scope FROM permits').fetchall(), [(scope,)])
        self.assertNotIn(b'synthetic-secret', self.path.read_bytes())

    async def test_independent_keys_and_categories_do_not_block_each_other(self):
        await limits.acquire('key-a', 'route', 1, path=self.path)
        await asyncio.wait_for(limits.acquire('key-a', 'poi', 1, path=self.path), .5)
        await asyncio.wait_for(limits.acquire('key-a', 'metadata', 1, path=self.path), .5)
        await asyncio.wait_for(limits.acquire('key-b', 'route', 1, path=self.path), .5)

    async def test_cancelled_future_waiters_do_not_spend_future_slots(self):
        await limits.acquire('key-a', 'route', 1, path=self.path)
        with closing(sqlite3.connect(self.path)) as db:
            before = db.execute('SELECT last_grant FROM permits').fetchone()[0]
        tasks = [asyncio.create_task(limits.acquire('key-a', 'route', 1, path=self.path)) for _ in range(15)]
        await asyncio.sleep(.04)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT last_grant FROM permits').fetchone()[0], before)

    async def test_busy_store_times_out_without_bypassing_shared_limit(self):
        limits.attempt(self.path, 'route', 10)
        with closing(sqlite3.connect(self.path)) as locked:
            locked.execute('BEGIN IMMEDIATE')
            with self.assertRaises(limits.LimiterUnavailable):
                await limits.acquire('key-a', 'route', 10, path=self.path, timeout=.12)

    async def test_unavailable_store_and_clock_rollback_fail_closed(self):
        invalid = Path(self.tmp.name)/'not-a-directory'
        invalid.write_text('file', encoding='utf-8')
        with self.assertRaises(limits.LimiterUnavailable):
            await limits.acquire('key', 'route', 1, path=invalid/'limits.sqlite3')
        scope = limits.scope_id('key', 'route')
        limits.attempt(self.path, scope, 1, lambda:1000)
        with self.assertRaises(limits.LimiterUnavailable):
            limits.attempt(self.path, scope, 1, lambda:900)

    async def test_early_wakeup_rechecks_actual_shared_deadline(self):
        real_sleep = asyncio.sleep
        async def early(delay): await real_sleep(delay*.5)
        marks=[]
        with patch.object(limits.asyncio, 'sleep', new=early):
            for _ in range(3):
                await limits.acquire('key', 'route', 20, path=self.path)
                marks.append(time.time())
        self.assertGreaterEqual(marks[-1]-marks[0], .095)

    async def test_coordinator_failure_never_reaches_provider_http(self):
        settings = SimpleNamespace(amap_api_key='fake-key',amap_route_qps=10,amap_poi_qps=10)
        client = SimpleNamespace(get=AsyncMock())
        with patch.object(amap, 'get_settings', return_value=settings), patch.object(amap,'_shared_client',return_value=client), \
             patch.object(amap,'acquire_shared_permit',new=AsyncMock(side_effect=limits.LimiterUnavailable('共享限流不可用'))):
            with self.assertRaises(amap.AMapServiceError) as caught:
                await amap._get_json('direction/walking', {})
        self.assertEqual(caught.exception.code, 'local_throttle')
        client.get.assert_not_awaited()

    async def test_metadata_queries_fail_closed_before_http_when_coordination_fails(self):
        settings=SimpleNamespace(amap_api_key='synthetic',amap_metadata_qps=1)
        client=SimpleNamespace(get=AsyncMock())
        with patch.object(amap,'get_settings',return_value=settings),patch.object(amap,'_shared_client',return_value=client),patch.object(amap,'acquire_shared_permit',new=AsyncMock(side_effect=limits.LimiterUnavailable('不可用'))) as permit:
            for path in ('config/district','geocode/geo','weather/weatherInfo'):
                with self.assertRaises(amap.AMapServiceError):await amap._get_json(path,{})
                self.assertEqual(permit.call_args.args[1],'metadata')
        client.get.assert_not_awaited()

    async def test_rate_change_cannot_make_live_traffic_faster_than_recent_stricter_policy(self):
        limits.attempt(self.path, 'scope', 1, lambda:1000)
        self.assertAlmostEqual(limits.attempt(self.path, 'scope', 50, lambda:1000.1), .9)
        self.assertEqual(limits.attempt(self.path, 'scope', 50, lambda:1061), 0)

    async def test_readiness_checks_local_coordinator_without_real_provider_quota(self):
        with patch.object(limits, 'default_store', return_value=self.path):
            self.assertTrue(await limits.readiness())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT scope FROM permits').fetchall(), [(limits.scope_id('__local_health__','health'),)])
        with patch.object(limits, 'attempt', side_effect=sqlite3.OperationalError('unavailable')):
            self.assertFalse(await limits.readiness())


if __name__ == '__main__': unittest.main()
