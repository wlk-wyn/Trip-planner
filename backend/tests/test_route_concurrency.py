"""No network: reproduce shared-query cancellation and cache failure modes."""
import asyncio
import sqlite3
import unittest
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.services import schedule_validator as routes
from test_schedule import sample

RESULT = {"duration_minutes": 12, "distance_meters": 800}


class RouteConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        routes._route_cache.clear()
        routes._disk_disabled_until = 0
        self.disk = patch.object(routes, '_disk_cache', return_value=None)
        self.disk.start()
        stops = routes.stops_for_day(sample().days[0])
        self.first, self.second = stops[:2]

    async def asyncTearDown(self):
        await routes.close_route_queries()
        routes._route_cache.clear()
        routes._disk_disabled_until = 0
        self.disk.stop()

    async def segment(self, provider, mode='walking'):
        return await routes.route_segment(self.first, self.second, '测试城', mode, asyncio.Semaphore(4), provider)

    async def joined(self, count):
        async with asyncio.timeout(2):
            while not routes._inflight or next(iter(routes._inflight.values())).waiters < count:
                await asyncio.sleep(0)

    async def test_cancel_creator_does_not_cancel_other_waiter(self):
        ready, release = asyncio.Event(), asyncio.Event()
        async def request(*_):
            ready.set(); await release.wait(); return RESULT
        provider = AsyncMock(side_effect=request)
        with patch.object(routes, 'get_route', provider):
            first = asyncio.create_task(self.segment(provider)); await ready.wait()
            second = asyncio.create_task(self.segment(provider)); await self.joined(2)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError): await first
            release.set()
            result = await second
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.duration_minutes, 12)
        self.assertEqual(result.mode, 'walking')
        self.assertEqual(provider.await_count, 1)
        self.assertFalse(routes._inflight)

    async def test_cancel_joiner_does_not_cancel_creator(self):
        ready, release = asyncio.Event(), asyncio.Event()
        async def request(*_):
            ready.set(); await release.wait(); return RESULT
        provider = AsyncMock(side_effect=request)
        with patch.object(routes, 'get_route', provider):
            first = asyncio.create_task(self.segment(provider)); await ready.wait()
            second = asyncio.create_task(self.segment(provider)); await self.joined(2)
            second.cancel()
            with self.assertRaises(asyncio.CancelledError): await second
            release.set()
            self.assertEqual((await first).status, 'verified')
        self.assertEqual(provider.await_count, 1)

    async def test_last_waiter_cancellation_stops_provider_and_releases_entry(self):
        ready, cancelled = asyncio.Event(), asyncio.Event()
        async def request(*_):
            ready.set()
            try: await asyncio.Event().wait()
            finally: cancelled.set()
        provider = AsyncMock(side_effect=request)
        with patch.object(routes, 'get_route', provider):
            job = asyncio.create_task(self.segment(provider)); await ready.wait()
            job.cancel()
            with self.assertRaises(asyncio.CancelledError): await job
            await asyncio.wait_for(cancelled.wait(), 1)
        self.assertFalse(routes._inflight)

    async def test_cache_read_failure_still_queries_provider(self):
        provider = AsyncMock(return_value=RESULT)
        with patch.object(routes, 'get_route', provider), patch.object(routes, '_disk_cache', side_effect=sqlite3.OperationalError('locked')):
            result = await self.segment(provider)
            self.assertEqual(result.status, 'verified')
            self.assertEqual((await self.segment(provider)).duration_minutes, 12)
        self.assertEqual(provider.await_count, 1)

    async def test_disk_connections_are_closed_and_cache_can_be_recreated(self):
        from pathlib import Path
        self.disk.stop()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'nested' / 'cache.sqlite3'
            with patch.object(routes, 'ROUTE_CACHE_PATH', path):
                stamp = datetime.now(timezone.utc).isoformat()
                routes._disk_cache(('test',), (RESULT, stamp))
                self.assertEqual(routes._disk_cache(('test',))[1], stamp)
                # On Windows this also proves no live SQLite connection holds it.
                path.unlink()
                self.assertIsNone(routes._disk_cache(('test',)))

    async def test_slow_cache_write_does_not_hold_up_real_result(self):
        release, started = asyncio.Event(), asyncio.Event()
        async def disk(_key, value=None):
            if value is not None:
                started.set(); await release.wait()
        provider = AsyncMock(return_value=RESULT)
        with patch.object(routes, 'get_route', provider), patch.object(routes, '_best_effort_disk', side_effect=disk):
            result = await asyncio.wait_for(self.segment(provider), 1)
            await started.wait()
            self.assertEqual(result.status, 'verified')
            self.assertTrue(routes._cache_writes)
            release.set()
            await asyncio.gather(*routes._cache_writes)

    async def test_corrupt_expired_or_future_cache_is_not_used(self):
        now = datetime.now(timezone.utc)
        for cached in [(RESULT, 'bad date'), (RESULT, now.replace(tzinfo=None).isoformat()),
                       (RESULT, (now + timedelta(hours=1)).isoformat()),
                       (RESULT, (now - timedelta(seconds=routes.CACHE_TTL + 1)).isoformat()),
                       ({'duration_minutes': None}, now.isoformat())]:
            with self.subTest(cached=cached):
                routes._route_cache.clear()
                provider = AsyncMock(return_value=RESULT)
                with patch.object(routes, 'get_route', provider), patch.object(routes, '_disk_cache', return_value=cached):
                    self.assertEqual((await self.segment(provider)).duration_minutes, 12)
                    self.assertEqual(provider.await_count, 1)
                    await asyncio.gather(*routes._cache_writes)

    async def test_disk_hit_promotes_to_memory_preserving_original_time(self):
        stamp = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        provider = AsyncMock(return_value=RESULT)
        with patch.object(routes, 'get_route', provider), patch.object(routes, '_disk_cache', return_value=(RESULT, stamp)) as disk:
            first = await self.segment(provider); second = await self.segment(provider)
        provider.assert_not_awaited()
        self.assertEqual(disk.call_count, 1)
        self.assertEqual(first.checked_at, stamp)
        self.assertEqual(second.checked_at, stamp)

    async def test_invalid_provider_duration_is_never_verified_or_cached(self):
        for value in [None, -1, True, float('nan'), float('inf'), '12']:
            routes._route_cache.clear()
            provider = AsyncMock(return_value={'duration_minutes': value})
            with patch.object(routes, 'get_route', provider):
                result = await self.segment(provider)
            self.assertEqual(result.status, 'unverified')
            self.assertIsNone(result.duration_minutes)
            self.assertFalse(routes._route_cache)
        # A measured zero is valid, unlike missing duration.
        self.assertEqual((await self.segment(AsyncMock(return_value={'duration_minutes': 0}))).duration_minutes, 0)

    async def test_failure_is_shared_then_next_request_can_retry(self):
        ready, release = asyncio.Event(), asyncio.Event()
        async def request(*_):
            ready.set(); await release.wait(); raise RuntimeError('offline')
        provider = AsyncMock(side_effect=request)
        with patch.object(routes, 'get_route', provider):
            first = asyncio.create_task(self.segment(provider)); await ready.wait()
            second = asyncio.create_task(self.segment(provider)); await self.joined(2)
            release.set()
            self.assertEqual([r.status for r in await asyncio.gather(first, second)], ['unverified', 'unverified'])
            provider.side_effect = None; provider.return_value = RESULT
            self.assertEqual((await self.segment(provider)).status, 'verified')
        self.assertEqual(provider.await_count, 2)

    async def test_request_identity_cannot_be_overwritten_by_provider_payload(self):
        provider = AsyncMock(return_value={**RESULT, 'from_id': 'wrong', 'to_name': 'wrong'})
        result = await self.segment(provider)
        self.assertEqual(result.from_id, self.first['id'])
        self.assertNotEqual(result.to_name, 'wrong')

    async def test_shutdown_releases_background_work(self):
        started = asyncio.Event()
        async def request(*_):
            started.set(); await asyncio.Event().wait()
        provider = AsyncMock(side_effect=request)
        with patch.object(routes, 'get_route', provider):
            waiter = asyncio.create_task(self.segment(provider)); await started.wait()
            await routes.close_route_queries()
            with self.assertRaises(asyncio.CancelledError): await waiter
        self.assertFalse(routes._inflight)
        self.assertFalse(routes._cache_writes)
