"""Bounded repair of auto-selected meals with known opening-hour conflicts.

New-trip generation only. A candidate must have explicit usable hours and is
accepted only after actual route validation; unknown hours never hide a conflict.
"""
import asyncio
import math
import re

from ..models.schemas import Location, Meal
from .amap_rest import search_nearby_restaurants
from .opening_hours import parse_hours
from .reference_service import normalized
from .place_facts import apply_provider_facts
from .schedule_validator import minutes, stops_for_day
from .schedule_integrity import check_integrity
from .restaurant_candidates import automatic_restaurant

MAX_MEAL_REPAIRS = 3
MAX_CANDIDATES = 2


def protected_meal_ids(plan, request):
    text = normalized('\n'.join([request.free_text_input or '', request.reference_content or '', *(s.text for s in request.reference_sources)]))
    required = [c for c in request.reference_candidates if c.priority == 'required']
    protected = set()
    for day in plan.days:
        for meal in day.meals:
            name = meal.restaurant or meal.name
            aliases = {normalized(name), normalized(re.sub(r'[（(].*?[)）]', '', name))}
            named = any(len(alias) >= 2 and alias in text for alias in aliases)
            matched = any((c.place and c.place.poi_id == meal.poi_id and meal.poi_id) or normalized(c.name) in aliases for c in required)
            if meal.locked or meal.cost_status == 'confirmed' or named or matched:
                protected.add(meal.item_id)
    return protected


def _fits(hours, day, start, duration):
    rule = parse_hours(hours, day.date)
    return rule.understood and not rule.closed and any(a <= start and start+duration <= b for a, b in rule.windows)


def _near(anchor, poi):
    try:
        lon, lat = poi['coordinates']
        if not all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v) for v in (lon, lat)) or not -180 <= lon <= 180 or not -90 <= lat <= 90:
            return None
        dx = (lon-anchor.longitude)*111.32*math.cos(math.radians((lat+anchor.latitude)/2))
        dy = (lat-anchor.latitude)*110.57
        distance = math.hypot(dx, dy)
        return distance if distance <= 1.5 else None
    except (KeyError, TypeError, ValueError):
        return None


def _overrun(day):
    return max(0, day.schedule.minimum_minutes-day.schedule.available_minutes)


def _ready_at(day, item):
    actual = minutes(item.start_time)
    index = day.schedule.items.index(item)
    ready = actual
    if index == 0:
        ready = minutes(day.schedule.window_start)
    elif index-1 < len(day.schedule.segments):
        previous = day.schedule.items[index-1]
        edge = day.schedule.segments[index-1]
        if previous.end_time and edge.status == 'verified' and edge.duration_minutes is not None:
            ready = minutes(previous.end_time) + edge.duration_minutes + (day.buffer_minutes or 0)
    # Respect deliberately planned starts; remove only opening-hours waiting.
    planned = day.planned_start_times.get(item.id)
    if planned:
        ready = max(ready, minutes(planned))
    return min(actual, ready)


def _no_worse(original, candidate, old_name):
    if _overrun(candidate) > _overrun(original):
        return False
    # Preserve every unrelated conflict; do not trade a late appointment for a
    # resolved meal. Overtime wording can change only when its amount decreases.
    previous = {text for text in original.schedule.conflicts if old_name not in text}
    for text in candidate.schedule.conflicts:
        if text not in previous and not text.startswith('当天至少超出可用时间'):
            return False
    return True


async def repair_meal_windows(plan, request, validator, attempted=None, progress=None, *, include_missing_routes=False, repair_limit=None, settle_overtime=None, include_timing=False):
    from .meal_timing import WINDOWS, explicit_meal_times, meal_timing_issues
    result = plan.model_copy(deep=True)
    attempts = list(attempted or [])
    protected = protected_meal_ids(result, request)
    limit = MAX_MEAL_REPAIRS if repair_limit is None else repair_limit
    for day_index in range(len(result.days)):
        for meal_index in range(len(result.days[day_index].meals)):
            day = result.days[day_index]
            meal = day.meals[meal_index]
            if len(attempts) >= limit:
                return result, attempts
            if not day.schedule or not meal.route_matched or meal.item_id in protected:
                continue
            item = next((i for i in day.schedule.items if i.id == meal.item_id), None)
            if not item or item.fixed:
                continue
            edges_before = [s for s in day.schedule.segments if meal.item_id in (s.from_id, s.to_id)]
            missing = [s for s in edges_before if s.status != 'verified' or s.duration_minutes is None]
            rule = parse_hours(meal.open_hours, day.date)
            start = _ready_at(day, item) if item.start_time and item.end_time else None
            hours_problem = rule.outside_weekday or (rule.understood and (rule.closed or (start is not None and not _fits(meal.open_hours, day, start, item.duration_minutes))))
            timing_before = meal_timing_issues(result.model_copy(update={'days':[day]}), request) if include_timing else []
            timing_problem = (include_timing and not explicit_meal_times(request)
                and any(i.get('item_id') == meal.item_id for i in timing_before))
            if not hours_problem and not (include_missing_routes and missing) and not timing_problem:
                continue
            context = '|'.join(f'{s.from_id}>{s.to_id}:{s.status}:{s.duration_minutes}' for s in edges_before)
            key = f'{day.date}:{meal.item_id}:{meal.poi_id}:{start}:{context}' + (':timing' if timing_problem else '')
            if key in attempts:
                continue
            stops = stops_for_day(day.model_copy(deep=True))
            position = next((i for i, stop in enumerate(stops) if stop['id'] == meal.item_id), None)
            if position is None:
                continue
            if timing_problem and position:
                previous = day.schedule.items[position-1]
                # A nearer restaurant cannot make travel negative or remove the
                # user's buffer. Skip this search when even zero travel is late.
                latest = minutes(WINDOWS[meal.type][1])
                if previous.end_time and minutes(previous.end_time) + (day.buffer_minutes or 0) > latest:
                    continue
            anchor = stops[position-1]['place'].location if position else meal.location
            if include_missing_routes and missing:
                edge = missing[0]
                other_id = edge.to_id if edge.from_id == meal.item_id else edge.from_id
                endpoint = next((stop for stop in stops if stop['id'] == other_id), None)
                if endpoint and endpoint['place'].location:
                    anchor = endpoint['place'].location
            if not anchor:
                continue
            attempts.append(key)
            if progress:
                await progress(f'寻找{"交通与时间更合适的" if missing or timing_problem else "营业时段合适的"}{dict(breakfast="早餐", lunch="午餐", dinner="晚餐").get(meal.type, "餐厅")}')
            try:
                pois = await search_nearby_restaurants(anchor.longitude, anchor.latitude,
                    '早餐' if meal.type == 'breakfast' else '', radius=1500, category='050000' if meal.type == 'breakfast' else '050100')
            except Exception:
                continue
            choices = []
            used = {m.poi_id for d in result.days for m in d.meals if m.poi_id}
            for poi in pois:
                if not automatic_restaurant(poi, meal.type, visit_parent_ids={a.poi_id for a in day.attractions if a.poi_id}):
                    continue
                distance = _near(anchor, poi)
                candidate_rule = parse_hours(poi.get('open_hours'), day.date)
                usable_hours = (not candidate_rule.closed and not candidate_rule.outside_weekday and (not rule.understood or candidate_rule.understood)
                                and (timing_problem or not candidate_rule.understood or start is None or _fits(poi.get('open_hours'), day, start, item.duration_minutes)))
                if distance is not None and poi.get('poi_id') and poi.get('name') and poi['poi_id'] not in used and usable_hours:
                    choices.append((distance, poi))
            for _, poi in sorted(choices, key=lambda row: row[0])[:MAX_CANDIDATES]:
                replacement = Meal(**{**meal.model_dump(), 'place_id': '', 'name': '', 'restaurant': poi['name'], 'poi_id': poi['poi_id'],
                    'address': poi.get('address', ''), 'location': Location(longitude=poi['coordinates'][0], latitude=poi['coordinates'][1]),
                    'open_hours': poi.get('open_hours'), 'estimated_cost': None, 'cost_status': 'unknown', 'cost_source': '',
                    'recommended_dish': '', 'price_evidence': None, 'hours_evidence': None,
                    'description': '根据路线及预计用餐时间重新匹配的附近餐厅；营业信息仍以官方为准。'})
                apply_provider_facts(replacement, poi)
                candidate = day.model_copy(deep=True)
                candidate.meals[meal_index] = replacement
                candidate.schedule = None
                candidate_plan = result.model_copy(update={'days': [candidate]})
                try:
                    await validator(candidate_plan, request, progress=progress)
                except Exception:
                    continue
                checked = candidate.schedule
                if (settle_overtime and missing and checked and checked.status == 'conflict' and checked.conflicts
                        and all('超出可用时间' in text for text in checked.conflicts)
                        and all(s.status == 'verified' and s.duration_minutes is not None for s in checked.segments)):
                    # Treat meal replacement plus an optional-visit adjustment as
                    # one proposal. Never commit the intermediate overtime draft.
                    try:
                        candidate = await settle_overtime(candidate_plan)
                    except Exception:
                        continue
                    checked = candidate.schedule
                updated = next((i for i in checked.items if i.id == meal.item_id), None) if checked else None
                edges = [s for s in checked.segments if meal.item_id in (s.from_id, s.to_id)] if checked else []
                previously_verified = {(s.from_id, s.to_id) for s in day.schedule.segments if s.status == 'verified' and s.duration_minutes is not None}
                now_verified = {(s.from_id, s.to_id) for s in checked.segments if s.status == 'verified' and s.duration_minutes is not None} if checked else set()
                retained = {i.id for i in checked.items} if checked else set()
                required_verified = {edge for edge in previously_verified if edge[0] in retained and edge[1] in retained}
                expected_edges = int(position is not None and position > 0) + int(position is not None and position < len(stops)-1)
                timing_after = meal_timing_issues(result.model_copy(update={'days':[candidate]}), request) if include_timing else []
                old_timing_ids = {i.get('item_id') for i in timing_before}
                timing_ok = (not timing_problem or not any(i.get('item_id') == meal.item_id for i in timing_after)) and not any(i.get('item_id') not in old_timing_ids for i in timing_after)
                if (checked and updated and updated.start_time and updated.end_time and len(edges) == expected_edges
                        and all(s.status == 'verified' and s.duration_minutes is not None for s in edges)
                        and required_verified <= now_verified
                        and (not parse_hours(replacement.open_hours, candidate.date).understood
                             or _fits(replacement.open_hours, candidate, minutes(updated.start_time), updated.duration_minutes))
                        and not check_integrity(candidate, request)
                        and timing_ok
                        and _no_worse(day, candidate, meal.restaurant or meal.name)):
                    result.days[day_index] = candidate
                    break
    return result, attempts


async def repair_final_meals(plan, request, validator, attempted=None, progress=None):
    """Two critical meal contexts, then one timing context; bounded candidates."""
    from .initial_planning import fit_optional_visits
    async def settle(partial):
        fitted, _ = await fit_optional_visits(partial, request, validator, progress, max_checks=1)
        return fitted.days[0]
    attempts = list(attempted or [])
    fixed, attempts = await repair_meal_windows(plan, request, validator, attempts, progress,
        include_missing_routes=True, repair_limit=len(attempts)+2, settle_overtime=settle)
    try:
        fixed, _ = await asyncio.wait_for(repair_meal_windows(fixed, request, validator, attempts, progress,
            include_timing=True, repair_limit=len(attempts)+1), timeout=15)
    except asyncio.TimeoutError:
        pass  # Keep only the completed critical repairs, never an interim meal.
    old_meals = {m.item_id:m for d in plan.days for m in d.meals}
    notes = []
    for old_day, day in zip(plan.days, fixed.days):
        for meal in day.meals:
            old = old_meals.get(meal.item_id)
            if old and old.poi_id != meal.poi_id:
                notes.append(f'第{day.day_index+1}天按路线校验将自动匹配餐厅「{old.restaurant or old.name}」换为「{meal.restaurant or meal.name}」；营业信息仍需核实。')
        removed = [p.name for p in old_day.attractions if p.item_id not in {p.item_id for p in day.attractions}]
        if removed:
            notes.append(f'第{day.day_index+1}天将「{"、".join(removed)}」移出当天路线，保留为备选；其余停留时长和出行窗口未改变。')
    return fixed, notes
