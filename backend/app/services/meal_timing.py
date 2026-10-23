"""Suggested meal start windows, distinct from restaurant opening hours.

These defaults are not universal user preferences. Explicit timing/order requests
and locked visits are never silently rearranged to satisfy them.
"""
import asyncio
import re
import time

from .schedule_validator import minutes, stops_for_day
from .schedule_integrity import check_integrity

WINDOWS = {'breakfast': ('07:00', '10:30'), 'lunch': ('11:30', '14:00'), 'dinner': ('17:00', '20:00')}
LABELS = {'breakfast': '早餐', 'lunch': '午餐', 'dinner': '晚餐'}


def explicit_meal_times(request):
    text = request.free_text_input or ''
    return bool(re.search(r'(?:早饭|早餐|午饭|午餐|晚饭|晚餐|吃饭|用餐).{0,12}(?:\d|[一二三四五六七八九十两半]+点)|(?:\d{1,2}[:：点时]|[一二三四五六七八九十两半]+点).{0,12}(?:吃|餐|饭)', text))


def meal_timing_issues(plan, request):
    issues = []
    if explicit_meal_times(request):
        # User-specific clocks are not parsed by this default-window heuristic.
        return [dict(code='meal_time_custom_unverified', severity='warning', day_index=None,
            message='你指定了用餐时间，系统保留自定义要求，不按常规时段自动挪动餐次；目前尚未自动核实这项自定义时间是否已落实，请对照当天时间线确认。')]
    for day in plan.days:
        if not day.schedule or check_integrity(day, request):
            continue
        for meal in day.meals:
            if meal.type not in WINDOWS:
                continue
            item = next((i for i in day.schedule.items if i.kind == 'meal' and (i.id == meal.item_id or i.name == (meal.restaurant or meal.name))), None)
            if not item or not item.start_time or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', item.start_time):
                continue
            lo, hi = WINDOWS[meal.type]
            if not minutes(lo) <= minutes(item.start_time) <= minutes(hi):
                issues.append(dict(code='meal_time_outside_reference', severity='warning', day_index=day.day_index,
                    message=f'第{day.day_index+1}天{LABELS[meal.type]}安排在{item.start_time}开始，超出常规用餐参考时段{lo}—{hi}；请调整顺序或告知你的用餐习惯。',
                    item_id=meal.item_id, meal_type=meal.type))
    return issues


def prepare_meal_times(plan, request):
    """NEW drafts only: lower bounds prevent breakfast/lunch/dinner too early."""
    from .initial_planning import has_day_instructions
    if explicit_meal_times(request) or has_day_instructions(request):
        return plan
    result = plan.model_copy(deep=True)
    for day in result.days:
        day.identify_visits()
        for meal in day.meals:
            if meal.type in WINDOWS and not meal.locked:
                day.planned_start_times.setdefault(meal.item_id, WINDOWS[meal.type][0])
    return result


async def repair_meal_timing(plan, request, validator, progress=None, *, allow_duration_fit=False, local_first=False):
    """NEW trips: move meals, keep attraction order, validate at most four proposals.

    No venue replacement, removed meals or extended windows. Optional duration
    fitting uses the existing bounded policy and explicitly reports every cut.
    Rank insertion positions by already checked clocks, never invented routes.
    """
    from .initial_planning import has_day_instructions
    from .opening_hours import hours_issues
    from .evening_planning import evening_coverage
    if explicit_meal_times(request) or has_day_instructions(request):
        return plan, []
    # New-trip callers explicitly opt into the existing bounded duration policy.
    # Try its unchanged-place/order replay before buying new edges for a reorder.
    # Nested venue/restaurant replacements retain their own preservation rules.
    # Only the top-level new-plan pass opts into local-first fitting.
    result, notes = await asyncio.to_thread(fit_meal_durations, plan, request) if allow_duration_fit and local_first else (plan.model_copy(deep=True), [])
    if notes:
        from .observability import record_event
        record_event('meal_local_fit_days', len(notes))
    attempts, deadline = 0, time.monotonic()+15
    for index in range(len(result.days)):
        day = result.days[index]
        if (not day.schedule or check_integrity(day, request)
                or any(p.locked or p.has_time_constraint for p in day.attractions)
                or any(m.locked for m in day.meals) or (day.hotel and day.hotel.locked)):
            continue
        problems = meal_timing_issues(result.model_copy(update={'days': [day]}), request)
        for problem in problems:
            day = result.days[index]
            identity, slot = problem['item_id'], problem['meal_type']
            current_problems = meal_timing_issues(result.model_copy(update={'days': [day]}), request)
            if identity not in {p['item_id'] for p in current_problems}:
                continue
            lo, hi = (minutes(t) for t in WINDOWS[slot])
            planned = day.planned_start_times.get(identity)
            if planned and minutes(planned) > hi:
                continue
            stops = stops_for_day(day.model_copy(deep=True))
            meal_stop = next(s for s in stops if s['id'] == identity)
            original_order = [s['id'] for s in stops]
            others = [s for s in stops if s['id'] != identity]
            current_times = {i.id: i for i in day.schedule.items}
            proposals = []
            current_item = next(i for i in day.schedule.items if i.id == identity)
            if current_item.start_time and minutes(current_item.start_time) < lo:
                # An early dinner usually needs a wait, not a different route.
                proposals.append((-1, 0, original_order))
            ranks = {'breakfast': 0, 'lunch': 1, 'dinner': 2}
            for position in range(len(others)+1):
                order = [*others[:position], meal_stop, *others[position:]]
                ids = [s['id'] for s in order]
                if ids == original_order:
                    continue
                if stops[0]['id'].endswith('-start') and order[0]['id'] != stops[0]['id']:
                    continue
                if stops[-1]['id'].endswith('-end') and order[-1]['id'] != stops[-1]['id']:
                    continue
                meal_ranks = [ranks[s['place'].type] for s in order if s['kind'] == 'meal' and s['place'].type in ranks]
                if meal_ranks != sorted(meal_ranks):
                    continue
                previous = current_times.get(others[position-1]['id']) if position else None
                clock = previous.end_time if previous else day.schedule.window_start
                if not clock:
                    continue
                # Ranking estimate only. Actual new edges must be queried below.
                gap = max(0, lo-minutes(clock), minutes(clock)-hi)
                proposals.append((gap, abs(position-original_order.index(identity)), ids))
            for _, _, ids in sorted(proposals)[:2]:
                if attempts >= 4 or time.monotonic() >= deadline:
                    return result, notes
                attempts += 1
                candidate = day.model_copy(deep=True)
                candidate.visit_order = ids
                candidate.planned_start_times[identity] = planned or WINDOWS[slot][0]
                for meal in candidate.meals:
                    if meal.type in WINDOWS:
                        candidate.planned_start_times.setdefault(meal.item_id, WINDOWS[meal.type][0])
                candidate.schedule = None
                partial = result.model_copy(deep=True)
                partial.days = [candidate]
                if progress:
                    await progress(f'核实调整{LABELS[slot]}顺序后的时间与路线')
                try:
                    await asyncio.wait_for(validator(partial, request, progress), timeout=max(.001, deadline-time.monotonic()))
                except Exception:
                    continue
                checked = partial.days[0]
                duration_notes = []
                if (allow_duration_fit and checked.schedule and checked.schedule.status == 'conflict'
                        and checked.schedule.conflicts and all('超出可用时间' in text or '到达及停留不在登记营业时段内' in text for text in checked.schedule.conflicts)
                        and not meal_timing_issues(partial, request)):
                    from .initial_planning import fit_suggested_durations
                    try:
                        partial, duration_notes = await asyncio.wait_for(
                            fit_suggested_durations(partial, request, validator, progress, allow_opening_fit=True),
                            timeout=max(.001, deadline-time.monotonic()))
                    except Exception:
                        continue
                    checked = partial.days[0]
                if (not checked.schedule or checked.schedule.status != 'feasible' or checked.schedule.conflicts
                        or check_integrity(checked, request)
                        or any(i['code'] in {'closed_day', 'opening_hours_conflict'} for i in hours_issues(checked))):
                    continue
                remaining = meal_timing_issues(partial, request)
                old_ids = {p['item_id'] for p in current_problems}
                if any(i['item_id'] == identity or i['item_id'] not in old_ids for i in remaining):
                    continue
                full = result.model_copy(deep=True)
                full.days[index] = checked
                if evening_coverage(result, request) is True and evening_coverage(full, request) is not True:
                    continue
                result = full
                item = next(i for i in checked.schedule.items if i.id == identity)
                notes.append(f'第{day.day_index+1}天调整{LABELS[slot]}安排至{item.start_time}开始，保留全部景点，已重新查询相邻交通。'
                             + (' 停留时长未改变。' if not duration_notes else ''))
                notes.extend(duration_notes)
                break
    return result, notes


def fit_meal_durations(plan, request):
    """NEW plans only: local proposal search on at most two days with <=30 minute late meals.

    Keep every place, order, clock window and original queried route. A suggested
    visit may lose at most 25%, with museum/other floors of 90/60 minutes. Never
    reduce required, locked, fixed or explicitly timed visits. No provider/model
    calls; duration_proposal enforces its own one-second/1024-replay bound.
    """
    from .initial_planning import has_day_instructions, _reference_text
    from .duration_fit import duration_proposal, explicit_duration_instructions
    from .schedule_validator import build_schedule
    from .place_identity import normalized
    from .evening_planning import evening_coverage
    text = _reference_text(request)
    if (request.reference_mode == 'strict' or has_day_instructions(request) or explicit_meal_times(request)
            or explicit_duration_instructions(text)):
        return plan, []
    required_ids = {c.place.poi_id for c in request.reference_candidates if c.priority == 'required' and c.place}
    required_names = {normalized(c.place.name if c.place else c.name) for c in request.reference_candidates if c.priority == 'required'}
    result = plan.model_copy(deep=True)
    notes, attempts = [], 0
    for index, day in enumerate(result.days):
        if attempts >= 2:
            break
        if (not day.schedule or day.schedule.status != 'feasible' or day.schedule.conflicts or check_integrity(day, request)
                or any(s.status != 'verified' or s.duration_minutes is None for s in day.schedule.segments)
                or any(p.locked or getattr(p, 'has_time_constraint', False) for p in [*day.attractions, *day.meals, *([day.hotel] if day.hotel else [])])):
            continue
        problems = meal_timing_issues(result.model_copy(update={'days': [day]}), request)
        if not problems:
            continue
        items = {item.id: (position, item) for position, item in enumerate(day.schedule.items)}
        late_positions = []
        for issue in problems:
            if issue['item_id'] not in items:
                break
            position, item = items[issue['item_id']]
            hi = minutes(WINDOWS[issue['meal_type']][1])
            if not 0 < minutes(item.start_time) - hi <= 30:
                break
            late_positions.append(position)
        if len(late_positions) != len(problems):
            continue
        allowances = []
        for place in day.attractions:
            if (place.item_id not in items or items[place.item_id][0] >= min(late_positions)
                    or place.item_id in day.planned_start_times or place.poi_id in required_ids
                    or normalized(place.name) in required_names):
                continue
            floor = 90 if any(word in place.name + (place.category or '') for word in ('博物', '美术馆', '科技馆', '展览')) else 60
            spare = min(place.visit_duration // 4, max(0, place.visit_duration - floor))
            if spare:
                allowances.append((place.item_id, spare))
        if not allowances:
            continue
        attempts += 1
        def accepts(candidate, schedule):
            checked = candidate.model_copy(update={'schedule': schedule})
            clocks = {item.id: item.start_time for item in schedule.items}
            return (not meal_timing_issues(result.model_copy(update={'days': [checked]}), request)
                    and all(clocks.get(issue['item_id']) and minutes(clocks[issue['item_id']]) <= minutes(WINDOWS[issue['meal_type']][1])-3
                            for issue in problems))
        candidate = duration_proposal(day, request, allowances, margin=0, accept=accepts)
        if candidate is None:
            continue
        candidate.schedule = build_schedule(candidate, stops_for_day(candidate), day.schedule.segments, request)
        if check_integrity(candidate, request):
            continue
        full = result.model_copy(deep=True)
        full.days[index] = candidate
        if evening_coverage(result, request) is True and evening_coverage(full, request) is not True:
            continue
        before = {p.item_id: p for p in day.attractions}
        cuts = [f'{p.name} {before[p.item_id].visit_duration}→{p.visit_duration}分钟' for p in candidate.attractions
                if before[p.item_id].visit_duration != p.visit_duration]
        if cuts:
            notes.append(f'第{day.day_index+1}天为恢复常规用餐时段，调整建议停留：' + '；'.join(cuts)
                         + '。保留全部地点、顺序和出行窗口，使用原已查询交通重新计算时间。')
            result = full
    return result, notes
