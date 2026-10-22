import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, Mock, AsyncMock

from app.services import amap_rest as amap


class ProviderPacingTests(unittest.IsolatedAsyncioTestCase):
    async def test_early_timer_wake_is_not_a_rate_limit_slot(self):
        clock = Mock(side_effect=[0, .015, .020])
        sleeper = AsyncMock()
        await amap._wait_until(.020, clock, sleeper)
        self.assertEqual(sleeper.await_count, 2)
        self.assertAlmostEqual(sleeper.await_args_list[1].args[0], .005)

    async def test_cancelled_waiters_do_not_reserve_future_provider_capacity(self):
        with patch.object(amap, 'get_settings', return_value=SimpleNamespace(amap_route_qps=50, amap_poi_qps=50)):
            for kind, pacer, field in [('route', amap._pace_route_request, '_next_route_slot'), ('poi', amap._pace_poi_request, '_next_poi_slot')]:
                amap._pace_lock(kind)
                original = time.monotonic() + 10
                setattr(amap, field, original)
                tasks = [asyncio.create_task(pacer()) for _ in range(20)]
                await asyncio.sleep(.005)
                for task in tasks: task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self.assertEqual(getattr(amap, field), original)
                setattr(amap, field, time.monotonic())
                await pacer()
                self.assertGreater(getattr(amap, field), time.monotonic())

    async def test_live_calls_still_obey_rate_limit_after_cancellation(self):
        with patch.object(amap, 'get_settings', return_value=SimpleNamespace(amap_route_qps=50, amap_poi_qps=50)):
            marks = []
            async def call():
                await amap._pace_route_request(); marks.append(time.monotonic())
            await asyncio.gather(*(call() for _ in range(3)))
            self.assertEqual(len(marks), 3)
            self.assertGreaterEqual(marks[-1] - marks[0], .035)

    async def test_poi_and_routes_have_independent_limits(self):
        with patch.object(amap, 'get_settings', return_value=SimpleNamespace(amap_route_qps=50, amap_poi_qps=50)):
            amap._pace_lock('route')
            amap._next_route_slot = time.monotonic() + 10
            task = asyncio.create_task(amap._pace_route_request())
            await asyncio.sleep(0)
            await asyncio.wait_for(amap._pace_poi_request(), .5)
            self.assertFalse(task.done())
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
