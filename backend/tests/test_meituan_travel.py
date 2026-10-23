import asyncio
import copy
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.routes import saved_trips
from app.models.schemas import TripPlan
from app.services import meituan_travel as service
from app.services.request_gate import request_group
from app.services.trip_repository import TripRepository
from test_travel_agent import PLAN, REQUEST


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        service._cache.clear()
        self.settings = patch.object(service, 'get_settings', return_value=SimpleNamespace(meituan_travel_token=SecretStr('synthetic-secret')))
        self.settings.start()

    async def asyncTearDown(self):
        await service.close_queries()
        service._cache.clear()
        self.settings.stop()

    async def fetch(self, response):
        original_client = httpx.AsyncClient
        async def handler(request):
            self.assertEqual(str(request.url), service.ENDPOINT)
            self.assertEqual(request.headers['Authorization'], 'synthetic-secret')
            self.assertEqual(json.loads(request.content), {'city': '北京', 'query': '公开门票查询'})
            return response
        with patch.object(service.httpx, 'AsyncClient', side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs)):
            return await service._fetch('北京', '公开门票查询', 'synthetic-secret')

    async def test_wire_protocol_preserves_price_text_and_redacts_credentials(self):
        response = await self.fetch(httpx.Response(200, json={'code':0, 'data':'￥4XX起 [购买](http://dpurl.cn/demo) synthetic-secret'}))
        self.assertEqual(response['content'], '￥4XX起 [购买](http://dpurl.cn/demo) [已隐藏凭据]')
        self.assertEqual(response['source'], 'meituan')
        self.assertFalse(response['cached'])
        self.assertTrue(response['queried_at'])

    async def test_auth_rate_limit_bad_json_empty_and_redirect_are_bounded_errors(self):
        cases = [(httpx.Response(401, text='synthetic-secret'),502), (httpx.Response(429),429),
                 (httpx.Response(302, headers={'Location':'https://example.invalid'}),502),
                 (httpx.Response(200,text='bad'),502), (httpx.Response(200,json=[]),502),
                 (httpx.Response(200,json={'code':401,'msg':'synthetic-secret'}),502),
                 (httpx.Response(200,json={'code':0,'data':''}),502),
                 (httpx.Response(200,content=b'x'*(service.MAX_BYTES+1)),502)]
        for response, status in cases:
            with self.subTest(status=response.status_code):
                with self.assertRaises(service.MeituanError) as error:
                    await self.fetch(response)
                self.assertEqual(error.exception.status, status)
                self.assertNotIn('synthetic-secret',str(error.exception))

    async def test_cache_and_shared_query_do_not_retry_or_reprice(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def query(*_args):
            entered.set(); await release.wait()
            return {'content':'￥4XX起','source':'meituan','queried_at':'2026-10-22T00:00:00Z','cached':False}
        with patch.object(service, '_fetch', side_effect=query) as upstream:
            first = asyncio.create_task(service.query_travel('北京','门票'))
            await entered.wait()
            second = asyncio.create_task(service.query_travel('北京','门票'))
            await asyncio.sleep(0)
            release.set()
            results = await asyncio.gather(first, second)
            self.assertEqual(upstream.call_count,1)
            self.assertEqual(results[0]['content'],results[1]['content'])
            self.assertTrue((await service.query_travel('北京','门票'))['cached'])
            self.assertEqual(upstream.call_count,1)

    async def test_missing_token_and_provider_failure_are_not_cached(self):
        with patch.object(service, '_fetch', new=AsyncMock(side_effect=service.MeituanError(504,'超时'))) as upstream:
            for _ in range(2):
                with self.assertRaises(service.MeituanError): await service.query_travel('北京','门票')
            self.assertEqual(upstream.await_count,2)
        with patch.object(service,'get_settings',return_value=SimpleNamespace(meituan_travel_token=SecretStr(''))):
            with self.assertRaises(service.MeituanError) as error: await service.query_travel('北京','门票')
            self.assertEqual(error.exception.status,503)

    async def test_only_transport_footer_is_removed_and_code_zero_auth_error_is_rejected(self):
        text='**门票￥4XX起**\n[商品](http://dpurl.cn/demo)\n</answer>\n\n提示：当前请求未传 channel，建议升级 Skill 到最新版本，以获取最新的完整功能和最佳体验。'
        result=await self.fetch(httpx.Response(200,json={'code':0,'data':text}))
        self.assertEqual(result['content'],'**门票￥4XX起**\n[商品](http://dpurl.cn/demo)')
        with self.assertRaises(service.MeituanError):
            await self.fetch(httpx.Response(200,json={'code':0,'data':'鉴权失败：访问令牌已过期'}))

    async def test_network_timeout_is_not_exposed_or_retried(self):
        with patch.object(service.httpx,'AsyncClient',side_effect=httpx.ReadTimeout('synthetic-secret')) as client:
            with self.assertRaises(service.MeituanError) as error:
                await service._fetch('北京','公开门票查询','synthetic-secret')
            self.assertEqual(error.exception.status,504)
            self.assertNotIn('synthetic-secret',str(error.exception))
            self.assertEqual(client.call_count,1)


class TicketApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.repo = TripRepository(Path(self.folder.name)/'trips.sqlite3')
        self.owner='1fa9fe7d-3432-4cd7-ab80-7e7c290e160a'
        plan=TripPlan(**copy.deepcopy(PLAN)).model_dump()
        plan['days'][0]['attractions'][0]['item_id']='public-attraction'
        self.trip=self.repo.create(self.owner,REQUEST.model_dump(),plan)
        self.original=self.repo.read(self.owner,self.trip['id'])
        app=FastAPI(); app.include_router(saved_trips.router,prefix='/api')
        app.dependency_overrides[saved_trips.repository]=lambda:self.repo
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test',headers={'X-Trip-Owner':self.owner})
        self.url=f"/api/trips/{self.trip['id']}/ticket-query"
        self.body={'base_version':1,'day_date':'2026-10-03','item_id':'public-attraction','visit_date':(date.today()+timedelta(days=2)).isoformat()}

    async def asyncTearDown(self):
        await self.client.aclose(); self.folder.cleanup()

    async def test_query_uses_saved_identity_and_does_not_write_price_booking_or_version(self):
        result={'content':'[购买](http://dpurl.cn/demo)','source':'meituan','queried_at':'2026-10-22T00:00:00Z','cached':False}
        with patch.object(service,'query_travel',new=AsyncMock(return_value=result)) as upstream:
            response=await self.client.post(self.url,json=self.body)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['item_id'],'public-attraction')
        city,query=upstream.await_args.args
        self.assertEqual(city,'杭州'); self.assertIn('西湖博物馆',query)
        self.assertNotIn('南山路89号',query); self.assertNotIn('120.15',query)
        self.assertEqual(self.original,self.repo.read(self.owner,self.trip['id']))
        self.assertEqual(len(self.repo.versions(self.owner,self.trip['id'])),1)
        self.assertEqual(request_group('POST','/api/trips/{trip_id}/ticket-query'),'interactive')

    async def test_owner_stale_deleted_place_and_bad_date_never_call_provider(self):
        cases=[({'base_version':2},409),({'item_id':'other'},404),({'day_date':'2026-10-04'},404),
               ({'visit_date':'2000-01-01'},422),({'visit_date':'not-a-date'},422)]
        with patch.object(service,'query_travel',new=AsyncMock()) as upstream:
            for changes,status in cases:
                self.assertEqual((await self.client.post(self.url,json={**self.body,**changes})).status_code,status)
            other=await self.client.post(self.url,json=self.body,headers={'X-Trip-Owner':'2fa9fe7d-3432-4cd7-ab80-7e7c290e160a'})
            self.assertEqual(other.status_code,404)
            upstream.assert_not_awaited()

    async def test_provider_failure_is_readable_and_keeps_trip(self):
        with patch.object(service,'query_travel',new=AsyncMock(side_effect=service.MeituanError(429,'美团查询限额'))):
            response=await self.client.post(self.url,json=self.body)
        self.assertEqual(response.status_code,429); self.assertEqual(response.headers['Retry-After'],'60')
        self.assertEqual(self.original,self.repo.read(self.owner,self.trip['id']))

    async def test_legacy_plan_identity_matches_the_read_endpoint_without_rewriting_it(self):
        legacy=self.repo.create(self.owner,REQUEST.model_dump(),copy.deepcopy(PLAN))
        url=f"/api/trips/{legacy['id']}"
        loaded=(await self.client.get(url)).json()
        item_id=loaded['plan']['days'][0]['attractions'][0]['item_id']
        result={'content':'暂无对应商品','source':'meituan','queried_at':'2026-10-22T00:00:00Z','cached':False}
        with patch.object(service,'query_travel',new=AsyncMock(return_value=result)):
            response=await self.client.post(url+'/ticket-query',json={**self.body,'item_id':item_id})
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.repo.read(self.owner,legacy['id'])['plan'],PLAN)
