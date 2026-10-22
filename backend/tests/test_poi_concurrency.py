"""Public-query fan-out, cancellation ownership and result provenance; no network."""
import asyncio
import subprocess
import sys
import time
import unittest
from collections import OrderedDict
from unittest.mock import AsyncMock, patch

from app.services import amap_rest as rest

RAW={'pois':[{'id':'p1','name':'合成展馆','location':'116.4,39.9','photos':[{'url':'https://example.com/poi.jpg'}]}]}


class PlaceConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.real_query=rest._get_json
        self.cache=patch.object(rest,'_poi_cache',OrderedDict());self.cache.start()
        self.ready,self.release=asyncio.Event(),asyncio.Event()
        async def query(*_):
            self.ready.set();await self.release.wait();return RAW
        self.patch=patch.object(rest,'_get_json',new=AsyncMock(side_effect=query));self.query=self.patch.start()

    async def asyncTearDown(self):
        await rest.close_client();self.patch.stop();self.cache.stop()
        self.assertFalse(rest._poi_queries)

    async def joined(self,count):
        async with asyncio.timeout(2):
            while not rest._poi_queries or next(iter(rest._poi_queries.values())).waiters<count:
                await asyncio.sleep(0)

    async def test_search_detail_nearby_and_photos_coalesce_identical_bursts(self):
        for load in (lambda:rest.place_detail('p1'),lambda:rest.search_pois('北京','合成展馆'),
                     lambda:rest.search_nearby_restaurants(116.4,39.9),lambda:rest.find_poi_photo('合成展馆','北京')):
            self.release.clear();self.ready.clear();before=self.query.await_count
            tasks=[asyncio.create_task(load()) for _ in range(32)]
            await self.ready.wait();await self.joined(32)
            self.release.set();values=await asyncio.gather(*tasks)
            self.assertEqual(self.query.await_count-before,1)
            self.assertEqual(values[0],values[-1]);self.assertIsNotNone(values[0])

    async def test_shared_results_and_cache_have_independent_nested_copies_and_original_time(self):
        first=asyncio.create_task(rest.place_detail('p1'));await self.ready.wait()
        second=asyncio.create_task(rest.place_detail('p1'));await self.joined(2)
        self.release.set();a,b=await asyncio.gather(first,second)
        a['photos'].append('https://example.com/changed.jpg');a['coordinates'][0]=120
        again=await rest.place_detail('p1')
        self.assertEqual(again,b);self.assertNotEqual(a,b)
        self.assertEqual(again['observed_at'],b['observed_at']);self.assertEqual(self.query.await_count,1)

    async def test_cancel_creator_or_joiner_preserves_the_other_waiter(self):
        for cancel_index in (0,1):
            rest._poi_cache.clear();self.ready.clear();self.release.clear()
            first=asyncio.create_task(rest.place_detail('p1'));await self.ready.wait()
            second=asyncio.create_task(rest.place_detail('p1'));await self.joined(2)
            jobs=[first,second];jobs[cancel_index].cancel()
            with self.assertRaises(asyncio.CancelledError):await jobs[cancel_index]
            self.release.set();self.assertEqual((await jobs[1-cancel_index])['poi_id'],'p1')
        self.assertEqual(self.query.await_count,2)

    async def test_last_departing_waiter_cancels_work_and_cannot_cache_a_partial_result(self):
        cancelled=asyncio.Event()
        async def query(*_):
            self.ready.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        self.query.side_effect=query
        task=asyncio.create_task(rest.place_detail('p1'));await self.ready.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        await asyncio.wait_for(cancelled.wait(),1)
        self.assertFalse(rest._poi_queries);self.assertFalse(rest._poi_cache)

    async def test_abandoned_query_waiting_for_quota_never_sends_http(self):
        waiting,cancelled=asyncio.Event(),asyncio.Event()
        async def pace():
            waiting.set()
            try:await asyncio.Event().wait()
            finally:cancelled.set()
        client=AsyncMock()
        with patch.object(rest,'_get_json',self.real_query),patch.object(rest,'_shared_client',return_value=client),\
             patch.object(rest,'_pace_poi_request',side_effect=pace):
            job=asyncio.create_task(rest.place_detail('p1'));await waiting.wait();job.cancel()
            with self.assertRaises(asyncio.CancelledError):await job
            await asyncio.wait_for(cancelled.wait(),1)
            client.get.assert_not_awaited()

    async def test_failure_is_shared_not_cached_and_next_request_can_retry(self):
        async def query(*_):
            self.ready.set();await self.release.wait();raise rest.AMapServiceError('network','合成失败',True)
        self.query.side_effect=query
        a=asyncio.create_task(rest.place_detail('p1'));await self.ready.wait()
        b=asyncio.create_task(rest.place_detail('p1'));await self.joined(2)
        self.release.set();values=await asyncio.gather(a,b,return_exceptions=True)
        self.assertTrue(all(isinstance(v,rest.AMapServiceError) for v in values));self.assertFalse(rest._poi_cache)
        self.query.side_effect=None;self.query.return_value=RAW
        self.assertEqual((await rest.place_detail('p1'))['poi_id'],'p1');self.assertEqual(self.query.await_count,2)

    async def test_distinct_query_types_cities_limits_filters_and_locations_are_not_shared(self):
        self.release.set()
        await asyncio.gather(rest.place_detail('p1'),rest.place_detail('p2'),
            rest.search_pois('北京','馆',limit=1),rest.search_pois('杭州','馆',limit=1),
            rest.search_pois('北京','馆',limit=2),rest.search_pois('北京','馆',limit=1,category='140000'),
            rest.search_pois('北京','馆',limit=1,citylimit=False),
            rest.search_nearby_restaurants(116.4,39.9),rest.search_nearby_restaurants(116.5,39.9),
            rest.search_nearby_restaurants(116.4,39.9,radius=700))
        self.assertEqual(self.query.await_count,10)

    async def test_empty_result_is_cached_but_expired_result_is_refreshed(self):
        self.release.set();self.query.side_effect=None;self.query.return_value={'pois':[]}
        self.assertIsNone(await rest.place_detail('absent'));self.assertIsNone(await rest.place_detail('absent'))
        self.assertEqual(self.query.await_count,1)
        rest._poi_cache[('detail','absent')]=(time.monotonic()-rest.POI_CACHE_TTL-1,None)
        self.query.return_value=RAW
        self.assertIsNotNone(await rest.place_detail('absent'));self.assertEqual(self.query.await_count,2)

    async def test_shutdown_cancels_shared_queries_before_closing_transport(self):
        first=asyncio.create_task(rest.place_detail('p1'));await self.ready.wait()
        await rest.close_client()
        with self.assertRaises(asyncio.CancelledError):await first
        self.assertFalse(rest._poi_queries)

    async def test_last_waiter_cleanup_finishes_before_transport_closes(self):
        order=[]
        async def query(*_):
            self.ready.set()
            try:await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                order.append('lookup_cleanup')
        self.query.side_effect=query
        client=AsyncMock();client.is_closed=False
        client.aclose.side_effect=lambda:order.append('transport_closed')
        with patch.object(rest,'_client',client):
            first=asyncio.create_task(rest.place_detail('p1'));await self.ready.wait();first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            await rest.close_client()
        self.assertEqual(order,['lookup_cleanup','transport_closed'])

    def test_public_map_metrics_do_not_import_the_model_runtime(self):
        result=subprocess.run([sys.executable,'-c',
            "import app.services.observability,sys; assert 'langchain_core' not in sys.modules"],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
