"""Bounded preparation for NEW trips only, before real-route validation.

Geometry screens regional assignments; it never supplies travel durations.
Suggested durations are relaxed only after checked timing proves a conflict.
"""
import math
import asyncio
import re
from itertools import combinations

from .reference_service import normalized
from .schedule_validator import minutes

MAX_PLACES = 40
MAX_REGION_PASSES = 4
MAX_SWAP_EVALUATIONS = 256


def _reference_text(request):
    return '\n'.join([request.free_text_input or '', request.reference_content or '',
                      *(source.text or '' for source in request.reference_sources)])


def has_day_instructions(request):
    text = _reference_text(request)
    return request.reference_mode == 'strict' or bool(re.search(
        r'第[\d一二三四五六七八九十两]+[天日晚站]|首日|末日|最后一天|当天|次日|隔天|上午|下午|晚上|早上|中午|'
        r'单独|专门|留给|Day\s*\d|D\d|'
        r'\d{1,2}月\d{1,2}[日号]|\d{4}-\d{1,2}-\d{1,2}|周[一二三四五六日天]|星期|先.*再|然后', text))


def planning_advice(request):
    from .evening_planning import wants_evening
    from .daily_limits import daily_attraction_limit
    available = max(0, minutes(request.daily_end_time) - minutes(request.daily_start_time))
    # Planning reserves, explicitly not queried travel times. Validator remains authoritative.
    travel_reserve = max(30, round(available * .2))
    rows = []
    for count in (1, 2, 3, 4):
        legs = count + 4  # hotel endpoints + three meals, a conservative default
        buffer = legs * request.buffer_minutes
        rows.append({'attractions': count, 'meal_reserve_minutes': 150,
                     'buffer_reserve_minutes': buffer,
                     'suggested_total_visit_minutes': max(0, available - 150 - buffer - travel_reserve)})
    return {'daily_window': [request.daily_start_time, request.daily_end_time],
            'explicit_max_attractions_per_day': daily_attraction_limit(request),
            'evening_requirement': ('用户要求夜景或夜游：选择有夜游特点的真实候选，并在18:00后保留至少30分钟，安排晚餐及返回交通；不要仅在白天景点描述中写夜景。日落、亮灯、开放和预约需另核实。' if wants_evening(request) else None),
            'meal_start_guidance': '默认早餐07:00—10:30、午餐11:30—14:00、晚餐17:00—20:00开始。按上午实际停留与交通安排午餐，不要机械放在一半景点之后；保留三餐与缓冲，用户明确用餐时刻优先。餐厅营业时段还需独立核对。',
            'available_minutes': available, 'traffic_planning_reserve_minutes': travel_reserve,
            'note': '交通预留仅用于控制初稿密度，不是真实耗时；真实交通核验后仍可能冲突。先按相邻区域分日，远郊单独安排，保留明确日期和先后要求。不要填满全天，也不要靠延长窗口解决超时。',
            'visit_budgets': rows}


def _km(a, b):
    x, y = a.location, b.location
    lat = math.radians((x.latitude + y.latitude) / 2)
    return math.hypot((x.longitude-y.longitude)*111.32*math.cos(lat), (x.latitude-y.latitude)*110.57)


def _cost(places, hotel=None):
    anchors = ([hotel] if hotel and hotel.location else [])
    route = anchors + list(places) + anchors
    return sum(_km(a, b) for a, b in zip(route, route[1:]))


def _ordered(places, hotel=None):
    if len(places) < 2:
        return list(places)
    candidates = [list(places)]
    starts = [min(places, key=lambda p: _km(hotel, p))] if hotel and hotel.location else places
    for start in starts:
        path, remaining = [start], [p for p in places if p is not start]
        while remaining:
            next_place = min(remaining, key=lambda p: _km(path[-1], p))
            path.append(next_place)
            remaining.remove(next_place)
        candidates.append(path)
    return min(candidates, key=lambda p: _cost(p, hotel))


def prepare_regions(plan, request):
    """Keep counts and all visits, swap nearby regions only when instructions permit.

    Returns a copy. No endpoint, meal, protected visit or existing trip is moved.
    A fixed number of strictly improving swaps avoids unbounded clustering loops.
    """
    result = plan.model_copy(deep=True)
    places = [p for day in result.days for p in day.attractions]
    if (has_day_instructions(request) or not places or len(places) > MAX_PLACES
            or len(result.days) > 14 or any(not p.location for p in places)):
        return result, []
    # A named meal, fixed visit or retained visit anchors its whole day.
    editable = [i for i, day in enumerate(result.days) if 0 < len(day.attractions) <= 6 and not day.meals
                and not any(p.locked or p.has_time_constraint for p in day.attractions)
                and not day.visit_order and not day.planned_start_times]
    before = [[p.item_id for p in d.attractions] for d in result.days]
    for i in editable:
        day = result.days[i]
        day.attractions = _ordered(day.attractions, day.hotel)
    # Pair swaps preserve each day's count; do not turn a short day into a packed day.
    evaluated = 0
    for _ in range(MAX_REGION_PASSES):
        best = None
        best_gain = .5  # Ignore sub-500m screening noise.
        swaps = ((i, j, ai, bi) for i, j in combinations(editable, 2)
                 for ai in range(len(result.days[i].attractions)) for bi in range(len(result.days[j].attractions)))
        for i, j, ai, bi in swaps:
            if evaluated >= MAX_SWAP_EVALUATIONS:
                break
            evaluated += 1
            a, b = result.days[i], result.days[j]
            # No increase in either day's suggested visit load after a swap.
            if a.attractions[ai].visit_duration != b.attractions[bi].visit_duration:
                continue
            baseline = _cost(a.attractions, a.hotel) + _cost(b.attractions, b.hotel)
            left, right = list(a.attractions), list(b.attractions)
            left[ai], right[bi] = right[bi], left[ai]
            left, right = _ordered(left, a.hotel), _ordered(right, b.hotel)
            gain = baseline - _cost(left, a.hotel) - _cost(right, b.hotel)
            if gain > best_gain and gain > baseline * .05:
                best, best_gain = (i, j, left, right), gain
        if best is None:
            break
        i, j, left, right = best
        result.days[i].attractions, result.days[j].attractions = left, right
    changed = [i for i, day in enumerate(result.days) if before[i] != [p.item_id for p in day.attractions]]
    for i in changed:
        day = result.days[i]
        day.description = ' → '.join(p.name for p in day.attractions)
        day.schedule, day.visit_order, day.planned_start_times = None, [], {}
    return result, changed


async def fit_suggested_durations(plan, request, validator, progress=None, *, allow_opening_fit=False):
    """One conservative attempt per day. Accept only a real-route feasible copy.

    Opening conflicts without a daily overrun require explicit caller opt-in.
    Retain all places/meals/windows; cut each optional suggested visit by at most
    25%, to at least 90 minutes for museums and 60 minutes otherwise. Never cut
    required/locked/fixed visits or explicit user/reference duration instructions.
    """
    text = _reference_text(request)
    from .duration_fit import duration_proposal, explicit_duration_instructions
    from .schedule_integrity import check_integrity
    if request.reference_mode == 'strict' or explicit_duration_instructions(text):
        return plan, []
    result = plan.model_copy(deep=True)
    required = {normalized(c.place.name if c.place else c.name) for c in request.reference_candidates if c.priority == 'required'}
    required_ids = {c.place.poi_id for c in request.reference_candidates if c.priority == 'required' and c.place and c.place.poi_id}
    changes = []
    for index, day in enumerate(result.days):
        schedule = day.schedule
        if (not schedule or schedule.status != 'conflict' or not schedule.conflicts
                or any(s.status != 'verified' or s.duration_minutes is None for s in schedule.segments)
                or check_integrity(day, request)):
            continue
        overrun = schedule.minimum_minutes - schedule.available_minutes
        from .opening_hours import hours_issues
        opening_conflict = allow_opening_fit and any(i['code'] == 'opening_hours_conflict' for i in hours_issues(day))
        if overrun <= 0 and not opening_conflict:
            continue
        candidates = []
        for place in day.attractions:
            if place.locked or place.has_time_constraint or normalized(place.name) in required or (place.poi_id and place.poi_id in required_ids):
                continue
            floor = 90 if any(k in (place.category or '') + place.name for k in ('博物', '美术馆', '科技馆', '展览')) else 60
            spare = min(place.visit_duration // 4, max(0, place.visit_duration-floor))
            if spare:
                candidates.append((place.item_id, spare))
        if not candidates:
            continue
        # Fix the complete checked timetable together. An opening/meal conflict
        # cannot be waved away: the local replay must eliminate it too, using
        # unchanged queried edges. This avoids buying restaurant routes before
        # discovering that a bounded suggested-visit adjustment already fits.
        from .meal_timing import meal_timing_issues
        def accepts(candidate_day, candidate_schedule):
            checked = candidate_day.model_copy(update={'schedule': candidate_schedule})
            partial = result.model_copy(update={'days': [checked]})
            return (candidate_schedule.minimum_minutes <= candidate_schedule.available_minutes-10
                    and not any(i['code'] == 'meal_time_outside_reference' for i in meal_timing_issues(partial, request)))
        candidate = await asyncio.to_thread(duration_proposal, day, request, candidates, accept=accepts)
        if candidate is None:
            continue
        originals = {p.item_id:p for p in day.attractions}
        descriptions = []
        for place in candidate.attractions:
            previous = originals[place.item_id]
            cut = previous.visit_duration-place.visit_duration
            if cut:
                descriptions.append(f'{place.name} {previous.visit_duration}→{place.visit_duration}分钟')
        # Keep route order and solver appointment waits, if present.
        partial = result.model_copy(update={'days': [candidate]})
        try:
            await validator(partial, request, progress)
        except Exception:
            continue
        candidate = partial.days[0]
        if (candidate.schedule and candidate.schedule.status == 'feasible' and not candidate.schedule.conflicts
                and candidate.schedule.minimum_minutes <= candidate.schedule.available_minutes-10
                and not check_integrity(candidate, request) and accepts(candidate, candidate.schedule)):
            result.days[index] = candidate
            if any('超出可用时间' not in message for message in schedule.conflicts):
                from .observability import record_event
                record_event('duration_joint_fit_days')
            changes.append(f'第{day.day_index+1}天按已查询交通调整建议停留：' + '；'.join(descriptions))
    return result, changes


async def fit_optional_visits(plan, request, validator, progress=None, *, max_checks=3):
    """Last resort for new drafts: move one optional visit to a written alternative.

    At most three real-route validations per trip, two per day. Never accept a
    merely improved conflict, remove the day's only visit, or reinterpret an
    explicit user choice. Saved-trip edits must not call this function.
    """
    from .schedule_integrity import check_integrity
    text = _reference_text(request)
    if has_day_instructions(request) or re.search(r'全部|所有景点|一个不[删少]|(?:至少|正好|必须).*?(?:个景点|处景点)', text):
        return plan, []
    named = normalized(text)
    required_ids = {c.place.poi_id for c in request.reference_candidates
                    if c.priority == 'required' and c.place and c.place.poi_id}
    required_names = {normalized(c.place.name if c.place else c.name)
                      for c in request.reference_candidates if c.priority == 'required'}
    result = plan.model_copy(deep=True)
    changes, attempts = [], 0
    for index, day in enumerate(result.days):
        schedule = day.schedule
        if (len(day.attractions) < 2 or not schedule or schedule.status != 'conflict'
                or not schedule.conflicts or any('超出可用时间' not in m for m in schedule.conflicts)
                or any(s.status != 'verified' or s.duration_minutes is None for s in schedule.segments)):
            continue
        candidates = []
        # Retain the day's main visit rather than making a far-away highlight
        # the easiest deletion merely because its travel cost is high.
        anchor_id = max(day.attractions, key=lambda p: p.visit_duration).item_id
        for place in day.attractions:
            aliases = {normalized(place.name), normalized(re.sub(r'[（(].*?[)）]', '', place.name))}
            if (place.item_id == anchor_id or place.locked or place.has_time_constraint or place.item_id in day.planned_start_times
                    or place.poi_id in required_ids or aliases & required_names
                    or any(len(alias) >= 2 and alias in named for alias in aliases)):
                continue
            # An upper bound ranks attempts only; a fresh route check decides.
            adjacent = sum(s.duration_minutes for s in schedule.segments if place.item_id in (s.from_id, s.to_id))
            candidates.append(((place.visit_duration, -adjacent), place))
        for _, place in sorted(candidates, key=lambda row: row[0])[:2]:
            if attempts >= max_checks:
                return result, changes
            attempts += 1
            if progress:
                await progress('检查减少一个可选景点后能否按时完成')
            candidate = day.model_copy(deep=True)
            candidate.attractions = [p for p in candidate.attractions if p.item_id != place.item_id]
            candidate.visit_order = [identity for identity in candidate.visit_order if identity != place.item_id]
            candidate.schedule = None
            partial = result.model_copy(update={'days': [candidate]})
            try:
                await validator(partial, request, progress=progress)
            except Exception:
                continue
            checked = candidate.schedule
            if (not checked or checked.status != 'feasible' or checked.conflicts
                    or check_integrity(candidate, request)):
                continue
            candidate.description = ' → '.join(p.name for p in candidate.attractions)
            result.days[index] = candidate
            changes.append(f'第{day.day_index+1}天按真实交通校验，将「{place.name}」移出当天路线，保留为备选；'
                           '未缩短其余景点停留，也未延长出行时间。')
            result.budget = None
            break
    return result, changes


def enforce_initial_windows(plan, request):
    for day in plan.days:
        day.start_time, day.end_time = request.daily_start_time, request.daily_end_time
        day.buffer_minutes = request.buffer_minutes


def initial_repair_days(plan, request=None):
    days = {d.day_index for d in plan.days if d.schedule and d.schedule.conflicts}
    if request is not None:
        from .daily_limits import daily_limit_issues
        days.update(i['day_index'] for i in daily_limit_issues(plan,request))
        from .evening_planning import evening_issues, night_candidate
        if evening_issues(plan, request):
            candidates = {d.day_index for d in plan.days if any(night_candidate(p) for p in d.attractions)}
            days.update(candidates or {d.day_index for d in plan.days})
    return days


def restore_initial_timing(partial, previous, request, *, with_meals=True):
    """New-draft patch only; retain timing of unchanged visits, restore semantics.

    General saved-trip patch application keeps its existing behavior. This helper
    must never be applied to a user revision or to dates outside the patch scope.
    """
    from .schedule_validator import stops_for_day
    from .evening_planning import prepare_evening
    from .meal_timing import prepare_meal_times
    result = partial.model_copy(deep=True)
    for day in result.days:
        old = next((d for d in previous.days if d.date == day.date), None)
        if old is None:
            continue
        before = {s['id']:s['place'] for s in stops_for_day(old.model_copy(deep=True))}
        for stop in stops_for_day(day):
            identity, place = stop['id'], stop['place']
            old_place = before.get(identity)
            if (old_place and old_place.poi_id == place.poi_id and old_place.name == place.name
                    and old_place.location == place.location and identity in old.planned_start_times):
                day.planned_start_times.setdefault(identity, old.planned_start_times[identity])
    result = prepare_evening(result, request, with_meals=with_meals, reapply=True)
    return prepare_meal_times(result, request)


def check_initial_repair(patch, draft, request=None):
    indices = initial_repair_days(draft, request)
    conflict_dates = {d.date for d in draft.days if d.day_index in indices}
    for operation in patch.operations:
        if operation.date not in conflict_dates or operation.kind != 'attraction' or operation.action not in {'remove', 'replace', 'add', 'update_visit', 'reorder'}:
            raise ValueError('初稿修复只能调整冲突日期的景点，不得延长时间窗口、减少缓冲、删除餐饮或跨日移动')
        if request is not None and operation.action == 'update_visit':
            day = next(d for d in draft.days if d.date == operation.date)
            place = next((p for p in day.attractions if p.item_id == operation.item_id), None)
            text = _reference_text(request)
            from .duration_fit import explicit_duration_instructions
            explicit_duration = request.reference_mode == 'strict' or explicit_duration_instructions(text)
            required = {normalized(c.place.name if c.place else c.name) for c in request.reference_candidates if c.priority == 'required'}
            required_ids = {c.place.poi_id for c in request.reference_candidates if c.priority == 'required' and c.place and c.place.poi_id}
            if place and operation.visit_duration != place.visit_duration and (explicit_duration or normalized(place.name) in required or (place.poi_id and place.poi_id in required_ids)):
                raise ValueError('不能在初稿修复中改写用户明确的停留时长或必去地点时长，请调整其他可选项目')


def normalize_initial_repair(patch):
    """A reorder may retain IDs explicitly removed earlier in the same patch.

    Only filter those known removals; never guess missing visits or accept
    invented IDs. apply_patch still checks the remaining complete permutation.
    """
    result = patch.model_copy(deep=True)
    removed = {}
    for operation in result.operations:
        key = (operation.date, operation.kind)
        if operation.action == 'remove':
            removed.setdefault(key, set()).add(operation.item_id)
        elif operation.action == 'reorder':
            operation.order = [identity for identity in operation.order if identity not in removed.get(key, set())]
    return result
