"""高德 Web Service 的轻量只读查询，用于为行程生成提供真实地点。"""

from typing import Any
import asyncio
import time
import math
import re
import unicodedata
from copy import deepcopy
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, date

import httpx

from ..config import get_settings
from .provider_limits import acquire as acquire_shared_permit, LimiterUnavailable


AMAP_BASE_URL = "https://restapi.amap.com/v3"


class AMapServiceError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False):
        self.code, self.retryable = code, retryable
        super().__init__(message)


_next_route_slot = 0.0
_next_poi_slot = 0.0
_next_metadata_slot = 0.0
_pace_loop = None
_route_lock = None
_poi_lock = None
_metadata_lock = None
_client: httpx.AsyncClient | None = None
_client_loop = None
_poi_cache: OrderedDict = OrderedDict()
_region_cache: OrderedDict = OrderedDict()
_geocode_cache: OrderedDict = OrderedDict()
_poi_queries = {}
_poi_tasks = set()
POI_CACHE_TTL = 6 * 3600


@dataclass
class PlaceQuery:
    task: asyncio.Task
    waiters: int = 0


def _observe_place_query(task):
    if not task.cancelled():
        task.exception()


async def _poi_result(key, load):
    """Coalesce only identical public queries; return isolated result copies.

    One cancelled HTTP/job waiter cannot cancel another's lookup. The final
    departing waiter owns cancellation, so abandoned lookups spend no new slot.
    """
    from .observability import record_event
    hit = _poi_cache.get(key)
    if hit and time.monotonic() - hit[0] < POI_CACHE_TTL:
        _poi_cache.move_to_end(key)
        record_event('amap_poi_cache_hits')
        return deepcopy(hit[1])

    async def query():
        result = await load()
        _poi_cache[key] = (time.monotonic(), result)
        _poi_cache.move_to_end(key)
        while len(_poi_cache) > 1024:
            _poi_cache.popitem(last=False)
        return result

    identity = (asyncio.get_running_loop(), key)
    entry = _poi_queries.get(identity)
    if entry is None:
        entry = PlaceQuery(asyncio.create_task(query()))
        _poi_tasks.add(entry.task)
        entry.task.add_done_callback(_poi_tasks.discard)
        entry.task.add_done_callback(_observe_place_query)
        _poi_queries[identity] = entry
    else:
        record_event('amap_poi_shared_joins')
    entry.waiters += 1
    try:
        return deepcopy(await asyncio.shield(entry.task))
    finally:
        entry.waiters -= 1
        if entry.waiters == 0:
            if _poi_queries.get(identity) is entry:
                _poi_queries.pop(identity)
            if not entry.task.done():
                entry.task.cancel()


async def _pace_route_request():
    """Only spend a rate-limit slot when a live waiter is ready to send."""
    global _next_route_slot
    qps = max(0.1, min(get_settings().amap_route_qps, 50.0))
    async with _pace_lock('route'):
        await _wait_until(_next_route_slot)
        key = getattr(get_settings(), 'amap_api_key', '')
        if key:
            await acquire_shared_permit(key, 'route', qps)
        _next_route_slot = time.monotonic() + 1 / qps


async def _pace_poi_request():
    """Place search and detail calls are paced too; bursts of parallel searches otherwise hit 10021."""
    global _next_poi_slot
    qps = max(0.1, min(get_settings().amap_poi_qps, 50.0))
    async with _pace_lock('poi'):
        await _wait_until(_next_poi_slot)
        key = getattr(get_settings(), 'amap_api_key', '')
        if key:
            await acquire_shared_permit(key, 'poi', qps)
        _next_poi_slot = time.monotonic() + 1 / qps


async def _pace_metadata_request():
    global _next_metadata_slot
    qps=max(.1,min(getattr(get_settings(),'amap_metadata_qps',1.0),50.0))
    async with _pace_lock('metadata'):
        await _wait_until(_next_metadata_slot)
        key=getattr(get_settings(),'amap_api_key','')
        if key:await acquire_shared_permit(key,'metadata',qps)
        _next_metadata_slot=time.monotonic()+1/qps


async def _wait_until(deadline, clock=time.monotonic, sleeper=asyncio.sleep):
    # Windows timers may wake before the requested deadline. Recheck the clock
    # rather than accidentally exceeding the provider's sliding-window quota.
    while True:
        delay = deadline - clock()
        if delay <= 0:
            return
        await sleeper(delay)


def _pace_lock(kind):
    global _pace_loop, _route_lock, _poi_lock, _metadata_lock, _next_route_slot, _next_poi_slot, _next_metadata_slot
    loop = asyncio.get_running_loop()
    if _pace_loop is not loop:
        _pace_loop = loop
        _route_lock, _poi_lock, _metadata_lock = asyncio.Lock(), asyncio.Lock(), asyncio.Lock()
        _next_route_slot = _next_poi_slot = _next_metadata_slot = 0.0
    return {'route':_route_lock,'poi':_poi_lock,'metadata':_metadata_lock}[kind]


def _shared_client() -> httpx.AsyncClient:
    # One keep-alive client per event loop avoids a TLS handshake per query.
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client = httpx.AsyncClient(timeout=12, limits=httpx.Limits(max_keepalive_connections=10, max_connections=20))
        _client_loop = loop
    return _client


async def close_client():
    global _client
    tasks = list(_poi_tasks)
    for task in tasks:
        if not task.cancelling():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _poi_queries.clear()
    _poi_tasks.clear()
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _get_json(path: str, params: dict[str, Any]) -> dict[str, Any]:
    from .observability import record_event
    key = get_settings().amap_api_key
    if not key:
        raise AMapServiceError("configuration", "高德地图 Key 未配置")
    is_route = path.startswith("direction/")
    attempts = 3
    client = _shared_client()
    for attempt in range(attempts):
        try:
            waiting = time.monotonic()
            if is_route:
                await _pace_route_request()
            elif path.startswith("place/"):
                await _pace_poi_request()
            else:
                await _pace_metadata_request()
            kind = 'route' if is_route else 'poi' if path.startswith('place/') else 'metadata'
            record_event(f'amap_{kind}_permit_wait_seconds',time.monotonic()-waiting)
            record_event(f'amap_{kind}_http_attempts')
            sent = time.monotonic()
            try:
                response = await client.get(f"{AMAP_BASE_URL}/{path}", params={**params, "key": key})
            finally:
                record_event(f'amap_{kind}_http_seconds',time.monotonic()-sent)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data,dict):
                raise AMapServiceError('invalid_response','高德服务返回格式异常')
            if data.get("status") == "1":
                return data
            code = str(data.get("infocode", "unknown"))
            record_event('amap_provider_rejections')
            retryable = code in {"10014", "10015", "10016", "10019", "10020", "10021", "10029"}
            if retryable:
                message = f"高德交通服务限流或繁忙（代码 {code}），请稍后重试"
            elif code in {"10003", "10044", "40000"}:
                message = f"高德服务配额或余额不足（代码 {code}），请检查服务账户"
            elif code in {"10001", "10002", "10005", "10009", "10012", "10041"}:
                message = f"高德服务密钥、权限或白名单配置异常（代码 {code}）"
            else:
                message = f"高德地图查询失败（代码 {code}）"
            raise AMapServiceError(code, message, retryable)
        except httpx.TimeoutException:
            error = AMapServiceError("timeout", "高德交通查询超时", True)
        except LimiterUnavailable as exc:
            error = AMapServiceError('local_throttle', str(exc), False)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            error = AMapServiceError(f"http_{code}", f"高德服务 HTTP {code}", code == 429 or code >= 500)
        except httpx.HTTPError:
            error = AMapServiceError("network", "高德服务网络连接失败", True)
        except ValueError:
            error = AMapServiceError("invalid_response", "高德服务返回格式异常")
        except AMapServiceError as exc:
            error = exc
        if not error.retryable or attempt + 1 == attempts:
            raise error from None
        await asyncio.sleep(1.0 * (2 ** attempt))


async def _cached_pois(key: tuple, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    async def load():
        return _parse_pois(await _get_json(path, params))
    return await _poi_result(key, load)


async def search_pois(city: str, keyword: str, limit: int = 10, category: str = "", *, citylimit: bool = True) -> list[dict[str, Any]]:
    return await _cached_pois(("text", city, keyword, limit, category, citylimit), "place/text", {
        "city": city,
        "keywords": keyword,
        "types": category,
        "citylimit": "true" if citylimit else "false",
        "offset": min(max(limit, 1), 20),
        "page": 1,
        "extensions": "all",
    })


async def search_nearby_restaurants(
    longitude: float, latitude: float, keyword: str = "", radius: int = 1800,
    category: str = "050100",
) -> list[dict[str, Any]]:
    """按当天行程锚点找附近餐厅，避免全城搜索造成跨区折返。"""
    return await _cached_pois(("around", round(longitude, 5), round(latitude, 5), keyword, radius, category), "place/around", {
        "location": f"{longitude},{latitude}",
        "keywords": keyword,
        "types": category,
        "radius": radius,
        "sortrule": "distance",
        "offset": 20,
        "page": 1,
        "extensions": "all",
    })


async def place_detail(poi_id: str) -> dict[str, Any] | None:
    """One POI by Amap ID with business fields (cost, rating, opening hours)."""
    key = ("detail", poi_id)
    async def load():
        pois = _parse_pois(await _get_json("place/detail", {"id": poi_id, "extensions": "all"}))
        return pois[0] if pois else None
    return await _poi_result(key, load)


def _amount(value) -> float | None:
    """Amap uses [] or "" for missing numbers."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if not isinstance(value, bool) and math.isfinite(number) and number > 0 else None


def _business(poi: dict[str, Any]) -> dict[str, Any]:
    biz = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
    hours = biz.get("opentime2") if isinstance(biz.get("opentime2"), str) and biz.get("opentime2") else biz.get("open_time")
    fields = {"rating": _amount(biz.get("rating")), "avg_cost": _amount(biz.get("cost")),
              "hotel_price": _amount(biz.get("lowest_price")),
              "open_hours": hours[:160] if isinstance(hours, str) and hours else None}
    return {key: value for key, value in fields.items() if value is not None}


def _parse_pois(data: dict[str, Any]) -> list[dict[str, Any]]:
    from .place_identity import parent_id
    results = []
    observed_at = datetime.now(timezone.utc).isoformat()
    raw = data.get('pois', [])
    if not isinstance(raw, list):
        raise AMapServiceError('invalid_response', '地点服务返回格式异常')
    for poi in raw:
        if not isinstance(poi, dict):continue
        location = poi.get("location", "")
        try:
            longitude, latitude = (float(value) for value in location.split(",", 1))
        except (ValueError, AttributeError):
            continue
        if not math.isfinite(longitude) or not math.isfinite(latitude) or not -180<=longitude<=180 or not -90<=latitude<=90:
            continue
        address = poi.get("address")
        results.append({
            "poi_id": poi.get("id", ""),
            "parent_poi_id": parent_id(poi.get('parent'), poi.get('id')),
            "observed_at": observed_at,
            "name": poi.get("name", ""),
            "address": address if isinstance(address, str) else "",
            "coordinates": [longitude, latitude],
            "category": poi.get("type", ""),
            "tel": poi.get('tel') if isinstance(poi.get('tel'),str) else None,
            "photos": [p['url'] for p in poi.get('photos',[]) if isinstance(p,dict) and isinstance(p.get('url'),str) and p['url'].startswith(('https://','http://'))] if isinstance(poi.get('photos'),list) else [],
            **_business(poi),
        })
    if raw and not results:
        raise AMapServiceError('invalid_response', '地点服务没有返回有效坐标')
    return results


async def find_poi_photo(name: str, city: str = "") -> str | None:
    """Photo attached to an exactly named Amap POI; no nearby or stock substitute."""
    key = ("photo", city, name)
    async def load():
        data = await _get_json("place/text", {"keywords": name, "city": city, "citylimit": "true" if city else "false",
                                              "offset": 3, "page": 1, "extensions": "all"})
        for poi in data.get("pois", []):
            normalize=lambda value:re.sub(r'\s+','',unicodedata.normalize('NFKC',value)).casefold() if isinstance(value,str) else ''
            if normalize(poi.get('name'))!=normalize(name):continue
            photos = poi.get("photos") or []
            if isinstance(photos, list) and photos and isinstance(photos[0], dict) and isinstance(photos[0].get('url'),str) and photos[0]['url'].startswith(('http://','https://')):
                return str(photos[0]["url"]).replace("http://", "https://", 1)
        return None
    return await _poi_result(key, load)


async def get_forecast(city: str) -> list[dict[str, Any]]:
    region = await resolve_region(city)
    data = await _get_json("weather/weatherInfo", {
        "city": region['adcode'],
        "extensions": "all",
    })
    forecasts = data.get("forecasts", [])
    if not forecasts:
        return []
    if not isinstance(forecasts,list) or not isinstance(forecasts[0],dict):
        raise AMapServiceError('invalid_response','天气服务返回格式异常')
    forecast = forecasts[0]
    try:
        report=datetime.fromisoformat(forecast.get('reporttime',''))
        if report.tzinfo is None:report=report.replace(tzinfo=timezone(timedelta(hours=8)))
        age=(datetime.now(timezone.utc)-report).total_seconds()
        if not -300<=age<=86400:raise ValueError()
    except (TypeError,ValueError):
        raise AMapServiceError('invalid_response','天气预报更新时间未知或已过期，暂不能作为可靠预报') from None
    if forecast.get('adcode') and str(forecast['adcode']) != region['adcode']:
        raise AMapServiceError('invalid_response','天气服务返回了其他地区的预报')
    casts = forecast.get('casts',[])
    if not isinstance(casts,list) or any(not isinstance(cast,dict) for cast in casts):
        raise AMapServiceError('invalid_response','天气服务返回格式异常')
    try:
        dates=[date.fromisoformat(cast['date']).isoformat() for cast in casts]
        if len(set(dates))!=len(dates):raise ValueError()
    except (TypeError,ValueError,KeyError):
        raise AMapServiceError('invalid_response','天气服务的预报日期无效') from None
    stamp = datetime.now(timezone.utc).isoformat()
    return [{**cast,'source':'amap','adcode':region['adcode'],'city':forecast.get('city') or region['name'],
             'observed_at':stamp,'report_time':forecast.get('reporttime') if isinstance(forecast.get('reporttime'),str) else None} for cast in casts]


async def resolve_region(city: str) -> dict:
    """Weather requires adcode. Never choose the first of ambiguous district names."""
    city = city.strip()
    if not city:raise AMapServiceError('invalid_query','请填写具体城市或区县')
    if re.fullmatch(r'\d{6}',city):return {'name':city,'adcode':city}
    hit = _region_cache.get(city)
    if hit and time.monotonic()-hit[0]<POI_CACHE_TTL:return dict(hit[1])
    data = await _get_json('config/district',{'keywords':city,'subdistrict':0,'extensions':'base'})
    if not isinstance(data.get('districts',[]),list):raise AMapServiceError('invalid_response','地区服务返回格式异常')
    clean = lambda value: re.sub(r'(?:市|区|县)$','',value) if isinstance(value,str) else ''
    candidates = {d['adcode']:d for d in data.get('districts',[]) if isinstance(d,dict)
                  and re.fullmatch(r'\d{6}',str(d.get('adcode','')))
                  and (clean(d.get('name'))==clean(city) or city==d.get('citycode'))
                  and (d.get('level') in {'city','district'} or d.get('name') in {'北京市','上海市','天津市','重庆市'})}
    if not candidates:raise AMapServiceError('unknown_region','未找到明确的城市或区县，请补全名称或使用行政区编码')
    if len(candidates)!=1:raise AMapServiceError('ambiguous_region','城市或区县存在重名，请提供完整地区名称或行政区编码')
    item = next(iter(candidates.values()))
    result = {'name':item['name'],'adcode':str(item['adcode'])}
    _region_cache[city] = (time.monotonic(),result)
    while len(_region_cache)>128:_region_cache.popitem(last=False)
    return dict(result)


async def geocode_address(address: str, city: str | None = None) -> dict:
    key = (address,city)
    hit = _geocode_cache.get(key)
    if hit and time.monotonic()-hit[0]<POI_CACHE_TTL:return deepcopy(hit[1])
    data = await _get_json('geocode/geo',{'address':address,**({'city':city} if city else {})})
    raw = data.get('geocodes',[])
    if not isinstance(raw,list):raise AMapServiceError('invalid_response','地址服务返回格式异常')
    if not raw:raise AMapServiceError('unknown_address','未找到该地址，请提供完整城市、街道或地点名称')
    if len(raw)!=1:raise AMapServiceError('ambiguous_address','地址存在多个匹配，请补充区县、街道或门牌号')
    item = raw[0]
    # Coarse city/district centres cannot become precise route endpoints.
    if not isinstance(item,dict) or item.get('level') not in {'兴趣点','门牌号','单元号','建筑物','住宅','商厦','风景区'}:
        raise AMapServiceError('coarse_address','地址匹配精度不足，请补充门牌号或具体地点')
    try:
        longitude,latitude = map(float,item['location'].split(','))
        if not math.isfinite(longitude) or not math.isfinite(latitude) or not -180<=longitude<=180 or not -90<=latitude<=90:raise ValueError()
    except (KeyError,TypeError,AttributeError,ValueError):
        raise AMapServiceError('invalid_response','地址服务没有返回有效坐标') from None
    result = {'address':item.get('formatted_address') or address,'longitude':longitude,'latitude':latitude,
              'level':item['level'],'adcode':item.get('adcode'),
              'city':item.get('city') if isinstance(item.get('city'),str) else '',
              'province':item.get('province') if isinstance(item.get('province'),str) else '',
              'observed_at':datetime.now(timezone.utc).isoformat()}
    _geocode_cache[key] = (time.monotonic(),result)
    while len(_geocode_cache)>256:_geocode_cache.popitem(last=False)
    return deepcopy(result)


SHORT_WALK_METERS = 1000
POLYLINE_POINTS = 160


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _points(polyline) -> list[str]:
    return [point for point in polyline.split(";") if "," in point] if isinstance(polyline, str) else []


def _compact(points: list[str]) -> str:
    """Evenly thinned path (endpoints kept), 5 decimals: enough to draw, small to store."""
    if len(points) > POLYLINE_POINTS:
        step = (len(points) - 1) / (POLYLINE_POINTS - 1)
        points = [points[round(i * step)] for i in range(POLYLINE_POINTS)]
    out = []
    for point in points:
        try:
            lng, lat = (float(v) for v in point.split(",", 1))
        except ValueError:
            continue
        out.append(f"{lng:.5f},{lat:.5f}")
    return ";".join(out)


def _name(value) -> str:
    return value.get("name", "") if isinstance(value, dict) and isinstance(value.get("name"), str) else ""


def _minutes(seconds) -> int | None:
    value = _number(seconds)
    return math.ceil(value / 60) if value is not None else None


def _path_detail(path: dict, kind: str) -> tuple[list[dict], list[str]]:
    points = [point for step in path.get("steps") or [] for point in _points(step.get("polyline"))]
    leg = {"kind": kind, "distance_meters": round(_number(path.get("distance")) or 0), "duration_minutes": _minutes(path.get("duration"))}
    return [leg], points


def _transit_detail(transit: dict) -> tuple[list[dict], list[str]]:
    legs, points = [], []
    for segment in transit.get("segments") or []:
        walking = segment.get("walking") if isinstance(segment.get("walking"), dict) else {}
        if _number(walking.get("distance")):
            legs.append({"kind": "walk", "distance_meters": round(_number(walking["distance"])), "duration_minutes": _minutes(walking.get("duration"))})
            points += [point for step in walking.get("steps") or [] for point in _points(step.get("polyline"))]
        bus = segment.get("bus") if isinstance(segment.get("bus"), dict) else {}
        lines = bus.get("buslines") or []
        if lines:
            line = lines[0]  # later entries are equivalent alternatives for the same hop
            name, _, terminals = str(line.get("name", "")).partition("(")
            ends = terminals.rstrip(")").split("--")
            via = _number(line.get("via_num"))
            legs.append({
                "kind": "subway" if "地铁" in str(line.get("type", "")) else "bus",
                "line": name, "direction": ends[-1] if len(ends) == 2 and ends[0] != ends[1] else "",  # loop lines have no direction
                "from_stop": _name(line.get("departure_stop")), "to_stop": _name(line.get("arrival_stop")),
                "stops": int(via) + 1 if via is not None else None,
                "entrance": _name(segment.get("entrance")), "exit": _name(segment.get("exit")),
                "distance_meters": round(_number(line.get("distance")) or 0), "duration_minutes": _minutes(line.get("duration")),
                "alternatives": [str(other.get("name", "")).partition("(")[0] for other in lines[1:3]],
            })
            points += _points(line.get("polyline"))
        railway = segment.get("railway") if isinstance(segment.get("railway"), dict) else {}
        if _name(railway):
            legs.append({"kind": "rail", "line": _name(railway), "from_stop": _name(railway.get("departure_stop")),
                         "to_stop": _name(railway.get("arrival_stop")), "duration_minutes": _minutes(railway.get("time"))})
    return legs, points


def _straight_meters(a, b) -> float:
    lat = math.radians((a.latitude + b.latitude) / 2)
    return math.hypot((a.longitude - b.longitude) * 111320 * math.cos(lat), (a.latitude - b.latitude) * 110570)


async def get_route(origin, destination, city: str, mode: str, *, destination_city: str | None = None) -> dict:
    """Query driving/walking/transit durations; never substitute straight-line distance."""
    start = f"{origin.longitude},{origin.latitude}"
    end = f"{destination.longitude},{destination.latitude}"
    if start == end:
        return {"duration_minutes": 0, "duration_seconds": 0, "distance_meters": 0}
    if mode == "transit" and _straight_meters(origin, destination) <= SHORT_WALK_METERS:
        # Amap returns zero transit plans for walkable hops; skip that paced request and walk.
        walking = await get_route(origin, destination, city, "walking")
        return {**walking, "mode": "walking", "note": "距离较近，按步行安排"}
    path = "direction/transit/integrated" if mode == "transit" else f"direction/{mode}"
    params = {"origin": start, "destination": end}
    if mode == "transit":
        params.update(city=city, cityd=destination_city or city, strategy=0, nightflag=0)
    data = await _get_json(path, params)
    route = data.get("route", {})
    choices = route.get("transits" if mode == "transit" else "paths", [])
    if not choices:
        if mode == "transit":
            # Query an actual walking route rather than fabricate travel time.
            walking = await get_route(origin, destination, city, "walking")
            if walking["distance_meters"] <= 2000:
                return {**walking, "mode": "walking", "note": "高德未提供公交方案（距离较近），按步行安排"}
            raise AMapServiceError("no_transit", "未查到公交方案，步行路线超过2公里；请调整地点或交通方式")
        raise AMapServiceError("no_route", "高德未返回可用路线，请调整地点或交通方式")
    try:
        chosen = min(choices, key=lambda item: float(item["duration"]))
        duration, distance = float(chosen["duration"]), float(chosen["distance"])
        if not math.isfinite(duration) or not math.isfinite(distance) or duration < 0 or distance < 0:
            raise ValueError()
        legs, points = _transit_detail(chosen) if mode == "transit" else _path_detail(chosen, "walk" if mode == "walking" else "drive")
        return {"duration_minutes": math.ceil(duration / 60), "duration_seconds": math.ceil(duration), "distance_meters": round(distance),
                "fare": _number(chosen.get("cost")) if mode == "transit" else None,
                "taxi_cost": _number(route.get("taxi_cost")), "legs": legs, "polyline": _compact(points)}
    except (ValueError, TypeError, KeyError):
        raise AMapServiceError('invalid_response',"交通服务没有返回有效的时间或距离") from None


def hotel_candidates(pois):
    """Exclude hotel ancillary POIs that are not independent lodging choices."""
    return [poi for poi in pois if not re.search(r"(大堂|大厅|停车场|会议室|餐厅|健身房|游泳池|商务中心)(?:\([^)]*\))?$", poi.get("name", ""))]
