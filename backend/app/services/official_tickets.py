"""Small, explicit official ticket adapters. No arbitrary URL fetching or LLM prices.

Only identified main venues are covered. Published adult admission is a planning
reference, never ticket inventory, a booking, or proof of future opening.
"""
import asyncio
import hashlib
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser

import httpx

from .fact_evidence import fact_state, needs_refresh, record_fact


@dataclass(frozen=True)
class Source:
    key: str
    city: str
    poi_id: str
    names: tuple[str, ...]
    bounds: tuple[float, float, float, float]
    url: str
    scope: str


SOURCES = (
    Source('palace', '北京', 'B000A8UIN8', ('故宫博物院', '故宫'), (116.387, 39.909, 116.409, 39.927),
           'https://www.dpm.org.cn/singles_detail/257830.html', '成人大门票；珍宝馆、钟表馆等另售项目未计；优惠资格另核实'),
    Source('terracotta', '西安', 'B0FFGXMLTU', ('秦始皇兵马俑博物馆', '秦始皇帝陵博物院', '兵马俑博物馆'), (109.27, 34.37, 109.30, 34.40),
           'https://www.bmy.com.cn/jingtai/bmyweb/ticketing.html', '普通成人门票；不含讲解、电瓶车等额外项目；优惠资格另核实'),
)
_cache = {}
_flights = {}
CACHE_SECONDS = 6 * 3600
MAX_PAGE_BYTES = 2 * 1024 * 1024
LOOKUP_TIMEOUT = 5


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'noscript'}:
            self.skip += 1

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'noscript'}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


@dataclass(frozen=True)
class Policy:
    source_key: str
    peak: int
    low: int
    observed_at: str
    page_sha256: str

    def price_on(self, day):
        return self.peak if 4 <= day.month <= 10 else self.low


def parse_policy(source, html, observed_at):
    parser = VisibleText()
    parser.feed(html)
    text = ''.join(parser.parts)
    compact = re.sub(r'\s+', '', text)
    if source.key == 'palace':
        peak = re.findall(r'4月1日至10月31日为旺季[，,；;]?大?门票(\d{1,4})元/人', compact)
        low = re.findall(r'11月1日至(?:次年|来年)?3月31日为淡季[，,；;]?大?门票(\d{1,4})元/人', compact)
        if '故宫博物院' not in compact:
            return None
    elif source.key == 'terracotta':
        peak = re.findall(r'Standard\s*Adult\s*Ticket\s*[:：]\s*(\d{1,4})\s*RMB', text, re.I)
        peak += re.findall(r'普通成人票[，,:：]?(\d{1,4})元/人', compact)
        low = peak
        if '兵马俑' not in compact and 'Qin' not in text:
            return None
    else:
        return None
    if len(set(peak)) != 1 or len(set(low)) != 1:
        return None
    amounts = [int(peak[0]), int(low[0])]
    if not all(0 <= value <= 3000 for value in amounts):
        return None
    return Policy(source.key, *amounts, observed_at, hashlib.sha256(html.encode()).hexdigest())


@dataclass
class Flight:
    task: asyncio.Task
    waiters: int = 0


async def load_policy(source):
    cached = _cache.get(source.key)
    if cached and time.monotonic() < cached[0]:
        return cached[1]
    flight = _flights.get(source.key)
    if flight is None:
        flight = Flight(asyncio.create_task(_fetch_policy(source)))
        _flights[source.key] = flight
    flight.waiters += 1
    try:
        return await asyncio.shield(flight.task)
    finally:
        flight.waiters -= 1
        if flight.waiters == 0:
            if _flights.get(source.key) is flight:
                _flights.pop(source.key)
            if not flight.task.done():
                flight.task.cancel()
                await asyncio.gather(flight.task, return_exceptions=True)


async def _fetch_policy(source):
    # Exact, code-owned HTTPS endpoints; redirects are rejected, not followed.
    try:
        async with httpx.AsyncClient(timeout=4, follow_redirects=False) as client:
            async with client.stream('GET', source.url, headers={'Accept': 'text/html', 'User-Agent': 'TripPlanner/1.0'}) as response:
                response.raise_for_status()
                if response.status_code != 200 or 'text/html' not in response.headers.get('content-type', '').lower():
                    raise ValueError('Unsupported official page response')
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_PAGE_BYTES:
                        raise ValueError('Official page exceeds limit')
                html = payload.decode('utf-8', errors='strict')
                result = parse_policy(source, html, datetime.now(timezone.utc).isoformat())
    except (httpx.HTTPError, UnicodeError, ValueError):
        result = None
    _cache[source.key] = (time.monotonic() + (CACHE_SECONDS if result else 30), result)
    return result


def matching_source(city, place):
    location = place.location
    if not location:
        return None
    for source in SOURCES:
        left, bottom, right, top = source.bounds
        if (city.removesuffix('市') == source.city and place.poi_id == source.poi_id and place.name in source.names
                and left <= location.longitude <= right and bottom <= location.latitude <= top):
            return source
    return None


async def refresh_official_tickets(plan, lookup=None):
    lookup = lookup or load_policy
    targets = []
    for day in plan.days:
        try:
            visit = date.fromisoformat(day.date)
        except ValueError:
            continue
        for place in day.attractions:
            source = matching_source(plan.city, place)
            state = fact_state(place, 'price', visit_date=day.date)
            if source and place.cost_status != 'confirmed' and state['source'] != 'user' and (
                    state['source'] != 'official' or needs_refresh(place, 'price', visit_date=day.date)):
                targets.append((place, visit, source))
    sources = {source.key: source for _, _, source in targets}

    async def bounded(source):
        try:
            return await asyncio.wait_for(lookup(source), timeout=LOOKUP_TIMEOUT)
        except TimeoutError:
            _cache[source.key] = (time.monotonic()+30, None)
            return None
        except Exception:
            return None

    policies = dict(zip(sources, await asyncio.gather(*(bounded(source) for source in sources.values()))))
    policies = {key: policy if isinstance(policy, Policy) and policy.source_key == key else None for key, policy in policies.items()}
    updated = 0
    for place, visit, source in targets:
        policy = policies.get(source.key)
        if not isinstance(policy, Policy) or policy.source_key != source.key:
            continue
        place.ticket_price = policy.price_on(visit)
        place.cost_status = 'estimated'
        place.cost_source = '官网公示成人门票参考，非预订报价'
        record_fact(place, 'price', 'official', policy.observed_at, source_url=source.url,
                    scope=source.scope, applies_on=visit.isoformat(), document_hash=policy.page_sha256)
        updated += 1
    return {'official_sources': len(sources), 'official_updated': updated,
            'official_failed': sum(not isinstance(policy, Policy) for policy in policies.values())}
