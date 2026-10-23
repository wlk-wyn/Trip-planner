"""Bounded appointment repair candidates, rechecked using actual route queries."""
import time
from .schedule_validator import validate_schedule, stops_for_day
from .ticket_bookings import active_booking, preserve_bookings
from .reference_service import normalized
from .initial_planning import has_day_instructions
from .conflict_fix import _venue_issues
from .meal_timing import meal_timing_issues

MAX_ROUTE_EDGES = 24
MAX_TOTAL_SECONDS = 24


def candidates(plan, request, seed, item_id, allow_cross_day=False):
    index = next(i for i, d in enumerate(plan.days) if d.date == seed.date)
    target_index = next(i for i, p in enumerate(seed.attractions) if p.item_id == item_id)
    target = seed.attractions[target_index]
    if target.locked: return []
    protected = {normalized(c.place.name if c.place else c.name) for c in request.reference_candidates if c.priority == 'required'}
    options = []
    positions = [i for i in range(target_index - 1, -1, -1) if not seed.attractions[i].locked
                 and not seed.attractions[i].has_time_constraint
                 and not active_booking(plan, seed, seed.attractions[i])]
    for number, earlier_index in enumerate(positions[:2], 1):
        revised = plan.model_copy(deep=True)
        day = seed.model_copy(deep=True)
        other = day.attractions[earlier_index]
        order = [s['id'] for s in stops_for_day(day)]
        a, b = order.index(item_id), order.index(other.item_id)
        order[a], order[b] = order[b], order[a]
        day.visit_order = order
        day.attractions.sort(key=lambda p: order.index(p.item_id))
        day.planned_start_times.pop(other.item_id, None)
        day.schedule = None
        revised.days[index] = day
        options.append({'id': f'reorder-{number}', 'title': f'先去{target.name}',
                        'detail': f'与{other.name}交换同日顺序，保留全部地点、停留时长和入场约束。',
                        'plan': revised, 'dates': [seed.date]})
    # Cross-day movement is opt-in and never moves the booked target itself.
    if (not allow_cross_day or len(plan.days) < 2 or request.reference_mode == 'strict'
            or has_day_instructions(request) or len(seed.attractions) < 2):
        return options
    destination_index = index + 1 if index + 1 < len(plan.days) else index - 1
    destination = plan.days[destination_index]
    if (len(destination.attractions) >= 6 or any(p.locked for p in [*seed.attractions, *seed.meals, *destination.attractions, *destination.meals])
            or (seed.hotel and seed.hotel.locked) or (destination.hotel and destination.hotel.locked)):
        return options
    movable = [seed.attractions[i] for i in positions if normalized(seed.attractions[i].name) not in protected
               and seed.attractions[i].place_id not in {p.place_id for p in destination.attractions}]
    if not movable: return options
    moving = max(movable, key=lambda p: p.visit_duration)
    revised = plan.model_copy(deep=True)
    source, dest = seed.model_copy(deep=True), revised.days[destination_index]
    source.visit_order = [s['id'] for s in stops_for_day(source) if s['id'] != moving.item_id]
    source.attractions = [p for p in source.attractions if p.item_id != moving.item_id]
    source.planned_start_times.pop(moving.item_id, None)
    dest_order = [s['id'] for s in stops_for_day(dest)]
    insert = max((i + 1 for i, s in enumerate(stops_for_day(dest)) if s['kind'] == 'attraction'), default=0)
    dest_order.insert(insert, moving.item_id)
    dest.attractions.append(moving.model_copy(deep=True)); dest.visit_order = dest_order
    source.schedule = dest.schedule = None
    revised.days[index] = source
    options.append({'id': 'move-flex', 'title': f'将{moving.name}改到第{destination_index + 1}天',
                    'detail': f'{target.name}的日期及入场约束不变，仅移动一个未锁定、无预约的灵活景点；重新检查两天路线。',
                    'plan': revised, 'dates': [source.date, dest.date]})
    return options


async def checked_options(plan, request, seed, item_id, allow_cross_day=False, selection=None, validator=validate_schedule):
    from ..agents.travel_agent import check_reference_constraints
    rows = candidates(plan, request, seed, item_id, allow_cross_day)
    if selection is not None:
        rows = [row for row in rows if row['id'] == selection]
    result, edges = [], 0
    deadline = time.monotonic() + MAX_TOTAL_SECONDS
    for row in rows:
        revised = row['plan']
        changed = [d for d in revised.days if d.date in row['dates']]
        needed = sum(max(0, len(stops_for_day(d)) - 1) for d in changed)
        if any(len(stops_for_day(d)) > 12 for d in changed) or edges + needed > MAX_ROUTE_EDGES or time.monotonic() >= deadline:
            continue
        try:
            preserve_bookings(revised, plan)
            check_reference_constraints(revised, request)
        except ValueError:
            continue
        edges += needed
        await validator(revised.model_copy(update={'days': changed}), request,
                        route_timeout_seconds=max(.1, min(8, deadline-time.monotonic())))
        for day in changed:
            day.description = ' → '.join(p.name for p in day.attractions)
            venue = _venue_issues(revised, day, request)
            if venue:
                day.schedule.conflicts = list(dict.fromkeys(day.schedule.conflicts + [i['message'] for i in venue]))
                day.schedule.status = 'conflict'
            for issue in meal_timing_issues(revised.model_copy(update={'days':[day]}), request):
                day.schedule.warnings.append(issue['message'])
        revised.budget = None
        status = 'conflict' if any(d.schedule.status == 'conflict' for d in changed) else 'unverified' if any(d.schedule.status == 'unverified' for d in changed) else 'feasible'
        result.append({**row, 'status':status, 'days':[d.model_dump() for d in changed],
                       'conflicts':list(dict.fromkeys(text for d in changed for text in d.schedule.conflicts)),
                       'warnings':list(dict.fromkeys(text for d in changed for text in d.schedule.warnings if any(word in text for word in ('早餐','午餐','晚餐')) and not text.startswith('游览时长')))})
    return result
