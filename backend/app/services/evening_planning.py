"""Evening coverage is a clock check, never proof of sunset or lighting hours."""
import asyncio
import re
import time

from .schedule_validator import minutes, stops_for_day
from .schedule_integrity import check_integrity

EVENING_START = 18 * 60
NIGHT_WORDS = r'夜景|夜游|夜市|灯光秀'


def wants_evening(request):
    # Only the user's own requirements, not suggestions quoted in guide material.
    text = '；'.join([request.free_text_input or '', *request.preferences])
    for clause in re.split(r'[，,。；;！!？?\n]|但是|不过|但', text):
        if (re.search(NIGHT_WORDS, clause)
                and not re.search(r'不想|不要|不看|不去|不安排|不需要|不用|取消|避免|有空|有时间|可选', clause)):
            return True
    return False


def night_candidate(place):
    text = place.name + ' ' + place.description
    # A model's prose cannot turn an ordinary museum visit into a night-view stop.
    if any(word in place.name + (place.category or '') for word in ('博物馆', '美术馆', '科技馆')):
        return False
    return bool(re.search(NIGHT_WORDS, text)) and not bool(re.search(r'不适合夜|无夜景|没有夜景|不看夜景', text))


def evening_coverage(plan, request):
    """None means absent/untrustworthy timings; False means no matching evening stop."""
    if not wants_evening(request):
        return None
    unknown = False
    for day in plan.days:
        if not day.schedule or check_integrity(day, request):
            unknown = True
            continue
        candidates = {p.item_id for p in day.attractions if night_candidate(p)}
        names = {p.name for p in day.attractions if night_candidate(p)}
        for item in day.schedule.items:
            if item.kind != 'attraction' or not (item.id in candidates or item.name in names):
                continue
            if (not isinstance(item.start_time, str) or not isinstance(item.end_time, str)
                    or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', item.start_time)
                    or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', item.end_time)):
                unknown = True
                continue
            if minutes(item.end_time) - minutes(item.start_time) != item.duration_minutes:
                unknown = True
                continue
            if minutes(item.end_time) - max(minutes(item.start_time), EVENING_START) >= 30:
                return True
    return None if unknown else False


def evening_issues(plan, request):
    if not wants_evening(request) or evening_coverage(plan, request) is True:
        return []
    return [dict(code='evening_preference_unmet', severity='warning', day_index=None,
                 message='你提出了夜景或夜游需求，但尚未确认有对应景点在18:00后安排至少30分钟游览；需要调整时段或地点。18:00仅是排程检查，日落和亮灯时间仍需另行确认。')]


def _editable(day):
    return not (day.visit_order or day.planned_start_times
                or any(p.locked or p.fixed_start_time for p in day.attractions)
                or any(p.locked for p in day.meals)
                or (day.hotel and day.hotel.locked))


def _set_evening_order(day, place):
    stops = stops_for_day(day)
    selected = next(s for s in stops if s['id'] == place.item_id)
    stops.remove(selected)
    position = len(stops) - int(bool(stops and stops[-1]['id'].endswith('-end')))
    stops.insert(position, selected)
    day.visit_order = [s['id'] for s in stops]
    live = {s['id'] for s in stops}
    day.planned_start_times = {k:v for k,v in day.planned_start_times.items() if k in live}
    day.planned_start_times[place.item_id] = max(day.planned_start_times.get(place.item_id, '18:00'), '18:00')
    for meal in day.meals:
        if meal.type == 'dinner':
            day.planned_start_times.setdefault(meal.item_id, '17:00')
    day.schedule = None


def prepare_evening(plan, request, *, with_meals=False, reapply=False):
    """NEW draft preparation before/after nearby meal matching; not a feasibility claim.

    Put a night candidate last before choosing dinner's geographic anchor. Then
    encode real time/order fields rather than relying on prose saying '18:00'.
    """
    from .initial_planning import has_day_instructions
    if not wants_evening(request) or has_day_instructions(request):
        return plan
    result = plan.model_copy(deep=True)
    for day in result.days:
        if (not _editable(day) and not reapply) or (reapply and (
                any(p.locked or p.fixed_start_time for p in day.attractions)
                or any(m.locked for m in day.meals) or (day.hotel and day.hotel.locked))):
            continue
        # Keep the same automatic night anchor across reorders. A different
        # last candidate must not acquire a second 18:00 constraint each round.
        timed = [p for p in day.attractions if night_candidate(p)
                 and day.planned_start_times.get(p.item_id, '') >= '18:00']
        choices = reversed(day.attractions) if with_meals else day.attractions
        place = timed[0] if timed else next((p for p in choices if night_candidate(p)), None)
        if place is None:
            continue
        day.attractions = [p for p in day.attractions if p is not place] + [place]
        if with_meals:
            _set_evening_order(day, place)
        return result
    return plan


async def repair_evening(plan, request, validator, progress=None):
    """At most two existing candidates, NEW trips only; accept a fully checked day.

    No new POIs, fabricated opening hours, deleted meals or extended day windows.
    Explicit day/order instructions, appointments and locked visits are preserved.
    """
    from .initial_planning import has_day_instructions
    from .opening_hours import hours_issues
    if not wants_evening(request) or evening_coverage(plan, request) is True or has_day_instructions(request):
        return plan, []
    attempts = 0
    deadline = time.monotonic() + 15
    for index, day in enumerate(plan.days):
        if not _editable(day):
            continue
        for place in day.attractions:
            if not night_candidate(place):
                continue
            if minutes(day.end_time or request.daily_end_time) < EVENING_START + place.visit_duration:
                continue
            if attempts >= 2:
                return plan, []
            attempts += 1
            candidate = day.model_copy(deep=True)
            _set_evening_order(candidate, place)
            partial = plan.model_copy(deep=True)
            partial.days = [candidate]
            if progress:
                await progress('核实夜游时段与返回路线')
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return plan, []
                await asyncio.wait_for(validator(partial, request, progress), timeout=remaining)
            except Exception:
                continue
            candidate = partial.days[0]
            if (not candidate.schedule or candidate.schedule.status != 'feasible' or candidate.schedule.conflicts
                    or check_integrity(candidate, request) or any(i['code'] in {'closed_day', 'opening_hours_conflict'} for i in hours_issues(candidate))):
                continue
            result = plan.model_copy(deep=True)
            result.days[index] = candidate
            if evening_coverage(result, request) is not True:
                continue
            return result, [f'第{day.day_index+1}天已将{place.name}调整至18:00后，保留晚餐与返回路线并重新校验交通；18:00为排程参考，日落、亮灯与预约时间仍需确认。']
    return plan, []
