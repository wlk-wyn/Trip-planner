"""Read-only travel query protocol from @meituan-travel/travel-cli 1.0.16.

No ordering API, automatic retries, redirects or writes to itinerary facts.
"""
import asyncio
import hashlib
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone

import httpx

from ..config import get_settings

ENDPOINT = 'https://mcp-open-cater.meituan.com/v1/api/voyage/openapi/query'
MAX_BYTES = 512_000
CACHE_SECONDS = 600
_cache = OrderedDict()
_inflight = {}


class MeituanError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


async def _fetch(city, query, token):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(125, connect=10), follow_redirects=False) as client:
            async with client.stream('POST', ENDPOINT, headers={'Authorization': token},
                                     json={'city': city, 'query': query}) as response:
                if response.status_code in (401, 403):
                    raise MeituanError(502, '美团授权无效、已过期或权限不足，请在后端更新 Token 后重试')
                if response.status_code == 429:
                    raise MeituanError(429, '美团查询达到频率或额度限制，请稍后再试')
                if response.status_code != 200:
                    raise MeituanError(502, '美团服务暂时无法完成查询，请稍后重试')
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise MeituanError(502, '美团返回内容过大，请稍后重试')
        import json
        payload = json.loads(body)
    except MeituanError:
        raise
    except (httpx.TimeoutException, TimeoutError):
        raise MeituanError(504, '美团查询超时，请稍后手动重试') from None
    except (httpx.HTTPError, ValueError, TypeError):
        # Never expose exception repr, raw response or authentication headers.
        raise MeituanError(502, '暂时无法读取美团查询结果，请稍后重试') from None
    if not isinstance(payload, dict):
        raise MeituanError(502, '美团返回格式无法识别')
    if payload.get('code') != 0:
        code = str(payload.get('code', ''))
        message = str(payload.get('msg', '')).lower()
        if code in {'401', '403'} or any(word in message for word in ('token', '鉴权', '授权', 'unauthorized')):
            raise MeituanError(502, '美团授权无效、已过期或权限不足，请在后端更新 Token 后重试')
        if code == '429' or any(word in message for word in ('额度', '限流', '频率')):
            raise MeituanError(429, '美团查询达到频率或额度限制，请稍后再试')
        raise MeituanError(502, '美团暂时无法完成查询，请稍后重试')
    content = payload.get('data')
    if not isinstance(content, str) or not content.strip():
        raise MeituanError(502, '美团暂无相关结果，可前往景区官方渠道核对')
    if any(word in content.lower() for word in ('鉴权失败', '无效的访问令牌', 'authorization failed', 'unauthorized', 'token无效', '访问令牌已过期')):
        raise MeituanError(502, '美团授权无效、已过期或权限不足，请在后端更新 Token 后重试')
    # Strip only known transport wrappers, never product descriptions/prices.
    content = re.sub(r'(?m)^</?answer>\s*$', '', content)
    content = re.sub(r'(?m)^提示：当前请求未传 channel，建议升级 Skill 到最新版本，以获取最新的完整功能和最佳体验。\s*$', '', content).strip()
    if not content:
        raise MeituanError(502, '美团暂无相关结果，可前往景区官方渠道核对')
    # Defensive redaction in case a provider ever echoes a credential.
    content = content.replace(token, '[已隐藏凭据]')
    return {'content': content, 'source': 'meituan',
            'queried_at': datetime.now(timezone.utc).isoformat(), 'cached': False}


async def query_travel(city, query):
    token = get_settings().meituan_travel_token.get_secret_value().strip()
    if not token:
        raise MeituanError(503, '尚未配置美团查询，请在后端设置 MEITUAN_TRAVEL_TOKEN')
    key = (hashlib.sha256(token.encode()).hexdigest(), city, query)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        _cache.move_to_end(key)
        return {**cached[1], 'cached': True}
    task = _inflight.get(key)
    if task is None:
        async def run():
            async with asyncio.timeout(130):
                result = await _fetch(city, query, token)
            _cache[key] = (time.monotonic(), result)
            _cache.move_to_end(key)
            while len(_cache) > 64:
                _cache.popitem(last=False)
            return result
        task = asyncio.create_task(run())
        _inflight[key] = task
        def finished(done):
            _inflight.pop(key, None)
            if not done.cancelled():
                done.exception()  # retrieve when all HTTP callers disconnected
        task.add_done_callback(finished)
    try:
        return dict(await asyncio.shield(task))
    except TimeoutError:
        raise MeituanError(504, '美团查询超时，请稍后手动重试') from None


async def close_queries():
    tasks = list(_inflight.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _inflight.clear()
