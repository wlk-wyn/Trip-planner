"""Deterministic day timelines using queried travel times and explicit assumptions."""
import asyncio
import os
import time
import json
import sqlite3
import logging
import math
import threading
from dataclasses import dataclass
from contextlib import closing
from pathlib import Path
from collections import OrderedDict
from datetime import datetime, timezone
from ..models.schemas import DaySchedule, ScheduleItem, RouteSegment
from .amap_rest import get_route, AMapServiceError
from .opening_hours import parse_place_hours
from .venue_routing import endpoint_for, GateUnavailable

_route_cache = OrderedDict()
# Route structure changes slowly; results still carry checked_at and the UI says "查询时结果".
CACHE_TTL = int(os.getenv("ROUTE_CACHE_TTL_SECONDS", "21600"))
_inflight = {}
_cache_writes = set()
_disk_disabled_until = 0.0
_disk_thread_lock = threading.Lock()
_disk_gate_loop = None
_disk_gate_lock = None
logger = logging.getLogger(__name__)
DISK_TIMEOUT = .2
DISK_WRITE_QUEUE_TIMEOUT = 1.0
DISK_BACKOFF = 30
ROUTE_FIELDS = {"mode", "duration_minutes", "distance_meters", "fare", "taxi_cost", "legs", "polyline", "note"}
ROUTE_CACHE_PATH = Path(__file__).resolve().parents[2] / "var" / "route-cache.sqlite3"


@dataclass
class SharedQuery:
    task: asyncio.Task
    waiters: int = 0


def _validated_route(result):
    if not isinstance(result, dict):
        raise ValueError("交通查询未返回有效数据")
    duration = result.get("duration_minutes")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
        raise ValueError("交通查询缺少有效耗时")
    clean = {key: value for key, value in result.items() if key in ROUTE_FIELDS}
    if "mode" in clean and clean["mode"] not in {"walking", "driving", "transit"}:
        raise ValueError("交通方式无效")
    clean["duration_minutes"] = math.ceil(duration)
    for field in ("distance_meters", "fare", "taxi_cost"):
        value = clean.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError("交通查询数值无效")
    if clean.get("distance_meters") is not None:
        clean["distance_meters"] = math.ceil(clean["distance_meters"])
    # Validate nested details too; identifiers always come from this request.
    parsed = RouteSegment(from_id="", to_id="", from_name="", to_name="", status="verified", **{"mode": "transit", **clean})
    return {key: value for key, value in parsed.model_dump().items() if key in ROUTE_FIELDS and (key != "mode" or "mode" in clean)}


def _cached_route(candidate):
    try:
        result, checked_at = candidate
        stamp = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            return None
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if not 0 <= age < CACHE_TTL:
            return None
        return _validated_route(result), checked_at, age
    except (ValueError, TypeError, AttributeError):
        return None


def _remember(key, result, checked_at, age=0):
    _route_cache[key] = (time.monotonic() - age, result, checked_at)
    _route_cache.move_to_end(key)
    while len(_route_cache) > 512:
        _route_cache.popitem(last=False)


def _observe(task):
    if not task.cancelled():
        task.exception()


async def _cancel_pending(tasks):
    """Drain owned work even if the owner is cancelled again during cleanup."""
    for task in tasks:
        if not task.done() and not task.cancelling():
            task.cancel()
    drain = asyncio.gather(*tasks, return_exceptions=True)
    try:
        await asyncio.shield(drain)
    except asyncio.CancelledError:
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError:
                continue
        raise


async def _best_effort_disk(key, value=None):
    global _disk_disabled_until
    if time.monotonic() < _disk_disabled_until:
        return None
    path = ROUTE_CACHE_PATH
    gate = _disk_gate()
    # Waiting behind other healthy cache operations is load, not store failure.
    # Skip an overloaded queue without disabling subsequent cache hits for 30s.
    try:
        # Background writes may queue longer without holding up route results.
        await asyncio.wait_for(gate.acquire(), DISK_TIMEOUT if value is None else DISK_WRITE_QUEUE_TIMEOUT)
    except asyncio.TimeoutError:
        from .observability import record_event
        record_event('route_disk_queue_skips')
        return None
    try:
        if time.monotonic() < _disk_disabled_until:
            return None
        # Capture the store before dispatch: a late worker cannot switch stores.
        return await asyncio.wait_for(asyncio.to_thread(_disk_cache, key, value, path), DISK_TIMEOUT)
    except (OSError, sqlite3.Error, ValueError, TypeError, asyncio.TimeoutError) as exc:
        # Cache is an optimization. Do not log locations, provider data or paths.
        if time.monotonic() >= _disk_disabled_until:
            logger.warning("Route disk cache unavailable; bypassing for %ss (%s)", DISK_BACKOFF, type(exc).__name__)
        _disk_disabled_until = time.monotonic() + DISK_BACKOFF
        from .observability import record_event
        record_event('route_disk_timeouts' if isinstance(exc, asyncio.TimeoutError) else 'route_disk_errors')
        return None
    finally:
        gate.release()


def _disk_gate():
    global _disk_gate_loop, _disk_gate_lock
    loop = asyncio.get_running_loop()
    if loop is not _disk_gate_loop:
        _disk_gate_loop, _disk_gate_lock = loop, asyncio.Lock()
    return _disk_gate_lock


def _persist(key, value):
    task = asyncio.create_task(_best_effort_disk(key, value))
    _cache_writes.add(task)
    task.add_done_callback(_cache_writes.discard)
    task.add_done_callback(_observe)


def _disk_cache(key, value=None, path=None):
    path = Path(path) if path is not None else ROUTE_CACHE_PATH
    # Also protect the direct synchronous adapter and workers surviving timeout.
    # Short SQLite lock retries handle another process initializing the WAL file.
    with _disk_thread_lock:
        for attempt in range(4):
            try:
                return _disk_operation(path, key, value)
            except sqlite3.OperationalError as exc:
                code = getattr(exc, 'sqlite_errorcode', 0) & 255
                if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or attempt == 3:
                    raise
                time.sleep(.005 * (attempt + 1))


def _disk_operation(path, key, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=.025)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE IF NOT EXISTS route_cache (key TEXT PRIMARY KEY, result TEXT NOT NULL, checked_at TEXT NOT NULL, expires REAL NOT NULL)")
        encoded=json.dumps(key)
        if value is not None:
            result, checked_at=value
            db.execute("INSERT OR REPLACE INTO route_cache VALUES (?,?,?,?)",(encoded,json.dumps(result),checked_at,time.time()+CACHE_TTL))
            db.execute("DELETE FROM route_cache WHERE expires<?",(time.time(),))
            db.execute("DELETE FROM route_cache WHERE key NOT IN (SELECT key FROM route_cache ORDER BY expires DESC LIMIT 512)")
            return None
        row=db.execute("SELECT result,checked_at FROM route_cache WHERE key=? AND expires>?",(encoded,time.time())).fetchone()
        return (json.loads(row[0]),row[1]) if row else None


async def _query_shared(key, a, b, city, mode, semaphore, provider):
    from .observability import record_event
    async def query():
        # Coalesce cache reads too: a cold burst needs one SQLite read, not N.
        hit = _cached_route(await _best_effort_disk(key))
        if hit:
            record_event('route_disk_hits')
            result, checked_at, age = hit
            _remember(key, result, checked_at, age)
            return result, checked_at
        async with semaphore:
            result = _validated_route(await provider(a,b,city,mode))
        checked_at=datetime.now(timezone.utc).isoformat()
        _remember(key, result, checked_at)
        _persist(key, (result, checked_at))
        return result,checked_at
    identity=(asyncio.get_running_loop(),key)
    entry = _inflight.get(identity)
    if entry is None:
        entry = SharedQuery(asyncio.create_task(query()))
        entry.task.add_done_callback(_observe)
        _inflight[identity] = entry
    else:
        record_event('route_shared_joins')
    entry.waiters += 1
    try:
        return await asyncio.shield(entry.task)
    finally:
        entry.waiters -= 1
        if entry.waiters == 0:
            if _inflight.get(identity) is entry:
                _inflight.pop(identity)
            if not entry.task.done():
                await _cancel_pending([entry.task])


async def close_route_queries():
    """Application shutdown owns remaining shared queries and cache writes."""
    tasks = [entry.task for entry in _inflight.values()] + list(_cache_writes)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _inflight.clear()
    _cache_writes.clear()



def minutes(value):
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def clock(value):
    return f"{value // 60:02d}:{value % 60:02d}" if 0 <= value < 1440 else None


def stops_for_day(day):
    day.identify_visits()
    stops = []
    def add(kind, place, duration, identity, fixed=None):
        stable_id = place.item_id + ("-start" if identity == "hotel-start" else "-end" if identity == "hotel-end" else "")
        stops.append({"id": stable_id, "kind": kind, "place": place, "duration": duration, "fixed": fixed,
                      "entry_window": getattr(place, 'entry_window', None)})
    if day.hotel and day.hotel.location:
        add("hotel", day.hotel, 0, "hotel-start")
    meals = {meal.type: meal for meal in day.meals if (meal.restaurant or meal.name).strip()}
    if "breakfast" in meals:
        add("meal", meals["breakfast"], 30, "breakfast")
    lunch_after = (len(day.attractions) + 1) // 2
    for index, attraction in enumerate(day.attractions):
        add("attraction", attraction, max(1, attraction.visit_duration), f"attraction-{index}", attraction.fixed_start_time)
        if index + 1 == lunch_after and "lunch" in meals:
            add("meal", meals["lunch"], 60, "lunch")
    if not day.attractions and "lunch" in meals:
        add("meal", meals["lunch"], 60, "lunch")
    for index, meal in enumerate(day.meals):
        if meal.type == "snack":
            add("meal", meal, 30, f"snack-{index}")
    if "dinner" in meals:
        add("meal", meals["dinner"], 60, "dinner")
    if day.hotel and day.hotel.location:
        add("hotel", day.hotel, 0, "hotel-end")
    if day.visit_order and len(day.visit_order) == len(stops) and set(day.visit_order) == {s["id"] for s in stops}:
        stops.sort(key=lambda stop: day.visit_order.index(stop["id"]))
    return stops


def display_name(stop):
    return getattr(stop["place"], "restaurant", None) or stop["place"].name


async def route_segment(first, second, city, mode, semaphore, provider, *, gate_lookup=None):
    from .observability import record_event
    base = dict(from_id=first["id"], to_id=second["id"], from_name=display_name(first), to_name=display_name(second), mode=mode)
    a, b = first["place"].location, second["place"].location
    if not a or not b or not (-180 <= a.longitude <= 180 and -90 <= a.latitude <= 90) or not (-180 <= b.longitude <= 180 and -90 <= b.latitude <= 90) or (a.longitude == 0 and a.latitude == 0) or (b.longitude == 0 and b.latitude == 0):
        return RouteSegment(**base, status="unverified", note="地点缺少有效坐标")
    try:
        async def unavailable_gate(_):
            return None
        lookup = gate_lookup or (None if provider is get_route else unavailable_gate)
        points = await asyncio.gather(endpoint_for(first,city,'exit',lookup),endpoint_for(second,city,'entrance',lookup),return_exceptions=True)
        for point in points:
            if isinstance(point,asyncio.CancelledError):raise point
            if isinstance(point,Exception):raise point
        origin,destination=points
        base.update(from_endpoint=origin,to_endpoint=destination)
        a,b=origin.location,destination.location
        if mode=='driving' and any(p.basis=='reviewed_gate' for p in points):
            return RouteSegment(**base,status='unverified',note='此段涉及步行参观门址；自驾须先明确停车或落客点及最后步行，尚未把这段步行计作0分钟')
        key = ("v2", city, mode, a.longitude, a.latitude, b.longitude, b.latitude)
        cached = _route_cache.get(key) if provider is get_route else None
        hit = _cached_route(cached[1:]) if cached and time.monotonic() - cached[0] < CACHE_TTL else None
        if hit:
            record_event('route_memory_hits')
            result, checked_at, _ = hit
            _route_cache.move_to_end(key)
        else:
            if provider is get_route:
                _route_cache.pop(key, None)
                result,checked_at=await _query_shared(key,a,b,city,mode,semaphore,provider)
            else:
                async with semaphore:
                    result=await provider(a,b,city,mode)
                checked_at=datetime.now(timezone.utc).isoformat()
        values = {**base, **_validated_route(result), "status": "verified", "checked_at": checked_at}
        return RouteSegment(**values)
    except GateUnavailable as exc:
        return RouteSegment(**base,status='unverified',note=str(exc))
    except AMapServiceError as exc:
        return RouteSegment(**base, status="unverified", note=str(exc))
    except Exception:
        return RouteSegment(**base, status="unverified", note="交通查询失败，请出行前核实")


def build_schedule(day, stops, segments, request):
    start = day.start_time or request.daily_start_time
    end = day.end_time or request.daily_end_time
    buffer = day.buffer_minutes if day.buffer_minutes is not None else request.buffer_minutes
    day.start_time, day.end_time, day.buffer_minutes = start, end, buffer
    current = minutes(start)
    end_minutes = minutes(end)
    conflicts = []
    hours_warnings = []
    hours_missing = 0
    if end_minutes <= current:
        conflicts.append("结束时间必须晚于当天出发时间")
    certain = True
    items = []
    minimum = sum(stop["duration"] for stop in stops) + buffer * len(segments)
    minimum += sum(segment.duration_minutes or 0 for segment in segments)
    for index, stop in enumerate(stops):
        if index:
            segment = segments[index - 1]
            current += (segment.duration_minutes or 0) + buffer
            certain = certain and segment.status == "verified"
        planned = day.planned_start_times.get(stop["id"])
        if planned and certain and not stop["fixed"]:
            target = minutes(planned)
            if target > current:
                minimum += target-current
                current=target
        fixed = stop["fixed"]
        entry = stop.get('entry_window')
        if entry and current < minutes(entry.start):
            minimum += minutes(entry.start) - current
            current = minutes(entry.start)
        if fixed:
            target = minutes(fixed)
            if current > target:
                conflicts.append(f"{display_name(stop)}预约{fixed}，按已知时间至少晚到{current - target}分钟")
            elif target > current:
                minimum += target - current
                current = target
            if target < minutes(start) or target + stop["duration"] > end_minutes:
                conflicts.append(f"{display_name(stop)}的固定预约超出当天可用时间")
        if stop["kind"] != "hotel":
            rule = parse_place_hours(stop["place"], day.date)
            name = display_name(stop)
            if rule.outside_weekday:
                conflicts.append(f'{name}的登记周营业日未覆盖当天，请先核实或调整日期/地点')
            elif rule.closed:
                conflicts.append(f"{name}登记营业时间显示当天不开放，请核对官方公告后调整日期或地点")
            elif rule.windows and certain:
                fits = any(a <= current and current + stop["duration"] <= b for a, b in rule.windows)
                if not fits:
                    following = [a for a, b in rule.windows if a >= current and a + stop["duration"] <= b]
                    if following and not fixed:
                        wait = min(following) - current
                        minimum += wait; current += wait
                    else:
                        conflicts.append(f"{name}的到达及停留不在登记营业时段内，请调整顺序、停留或地点")
            elif not rule.understood:
                hours_missing += 1
                if stop["place"].open_hours:
                    hours_warnings.append(f"{name}：{rule.reason}")
            if certain and rule.last_admission is not None and current >= rule.last_admission:
                conflicts.append(f'{name}的到达时间已到或晚于登记停止入馆时间，请调整顺序或地点')
        if entry and current > minutes(entry.end):
            conflicts.append(f'{display_name(stop)}最晚{entry.end}入场，按已知时间至少晚到{current - minutes(entry.end)}分钟')
        if entry and (minutes(entry.end) < minutes(start) or minutes(entry.start) + stop['duration'] > end_minutes):
            conflicts.append(f'{display_name(stop)}的入场时段与当天可用时间不兼容')
        item_start = clock(current) if certain else fixed
        current += stop["duration"]
        items.append(ScheduleItem(id=stop["id"], kind=stop["kind"], name=display_name(stop),
                                  duration_minutes=stop["duration"], start_time=item_start,
                                  end_time=clock(current) if certain else None, fixed=bool(fixed or entry)))
    available = max(0, end_minutes - minutes(start))
    if current > end_minutes:
        conflicts.append(f"当天至少超出可用时间{current - end_minutes}分钟；建议减少可选地点或延长时间")
    warnings = [f"游览时长为建议值；用餐按早餐30分钟、午晚餐各60分钟，每段交通额外留{buffer}分钟缓冲。", "营业时段仅按可解析的登记信息检查，不代表官方实时核实；预约名额仍需确认，固定预约时间以你的填写为准。"]
    warnings.append("交通耗时为查询时结果，不预测旅行日期的路况或公交班次；请在出行前重新校验。")
    warnings.append('已标注门址时，交通查询到入口、从出口出发；未标注门址的场所按地图定位点参考。参观时长包含场内游览建议，不是已测量的场内步行或排队耗时。')
    if hours_missing:
        warnings.append(f"{hours_missing} 处地点缺少可明确解析的营业时段，时间可安排不代表当天一定开放。")
    warnings.extend(hours_warnings)
    if not certain:
        warnings.append("部分交通未核实，耗时是已知下限；无法确认当天是否走得完。")
    return DaySchedule(status="conflict" if conflicts else "feasible" if certain else "unverified",
                       window_start=start, window_end=end, minimum_minutes=minimum,
                       available_minutes=available, items=items, segments=segments,
                       conflicts=list(dict.fromkeys(conflicts)), warnings=warnings)


async def validate_schedule(plan, request, provider=None, progress=None, *, gate_lookup=None, route_timeout_seconds=None):
    if route_timeout_seconds is not None and (not math.isfinite(route_timeout_seconds) or route_timeout_seconds <= 0):
        raise ValueError('交通查询时间上限必须为正数')
    provider = provider or get_route
    semaphore = asyncio.Semaphore(4)
    rows = []
    tasks = []
    day_stops = [stops_for_day(day) for day in plan.days]
    total = sum(max(0, len(stops) - 1) for stops in day_stops)
    done_count = 0

    async def tracked(segment):
        # Report inside the task so a stale count never lands after later stages.
        nonlocal done_count
        result = await segment
        done_count += 1
        try:
            await progress(f"核实交通 {done_count}/{total} 段", done_count, total)
        except Exception:
            pass
        return result

    for day, stops in zip(plan.days, day_stops):
        mode = "walking" if day.transportation == "步行" else "driving" if day.transportation == "自驾" else "transit"
        day_tasks = []
        for a, b in zip(stops, stops[1:]):
            segment = route_segment(a, b, plan.city, mode, semaphore, provider, gate_lookup=gate_lookup)
            day_tasks.append(asyncio.create_task(tracked(segment) if progress else segment))
        rows.append((day, stops, day_tasks, mode))
        tasks.extend(day_tasks)
    if tasks:
        try:
            timeout = min(180, max(40, len(tasks) * 2.5))
            if route_timeout_seconds is not None:
                timeout = min(timeout, route_timeout_seconds)
            await asyncio.wait(tasks, timeout=timeout)
        finally:
            # asyncio.wait does not cancel its children when its caller is
            # cancelled. Do not leave queued provider requests spending quota.
            await _cancel_pending(tasks)
    for day, stops, day_tasks, mode in rows:
        segments = []
        for index, task in enumerate(day_tasks):
            if not task.cancelled():
                segments.append(task.result())
            else:
                segments.append(RouteSegment(from_id=stops[index]["id"], to_id=stops[index+1]["id"],
                    from_name=display_name(stops[index]), to_name=display_name(stops[index+1]),
                    mode=mode, status="unverified", note="本次交通校验超时，可稍后重新校验"))
        day.schedule = build_schedule(day, stops, segments, request)
    # Legacy aggregate budgets have no source coverage; show item-level known/unknown costs instead.
    plan.budget = None
    return plan
