"""Deterministic repair proposals for a day whose schedule does not fit.

Each proposal is a modified copy of the day, re-validated with real routes, so the
user sees whether it actually fits before choosing. Required places, locked visits
and fixed appointments are never touched.
"""
from .reference_service import normalized
from .schedule_validator import validate_schedule, stops_for_day
from .opening_hours import hours_issues, parse_place_hours
from .park_scopes import park_scope_issues

MIN_VISIT_MINUTES = 45
MAX_REMOVAL_PROPOSALS = 3
PREVIEW_ROUTE_TIMEOUT_SECONDS = 8
VENUE_CONFLICTS = {'closed_day', 'opening_hours_conflict', 'opening_weekday_uncovered',
                   'park_last_entry', 'park_closing_time', 'inner_venue_closed', 'inner_venue_last_entry'}


def _venue_issues(plan, day, request):
    scoped = plan.model_copy(update={'days': [day]})
    return [issue for issue in hours_issues(day) + park_scope_issues(scoped, request)
            if issue['code'] in VENUE_CONFLICTS]


def _overrun(day) -> int:
    schedule = day.schedule
    return max(0, schedule.minimum_minutes - schedule.available_minutes) if schedule else 0


def _required_names(request) -> set[str]:
    return {normalized(c.place.name if c.place else c.name) for c in request.reference_candidates if c.priority == "required"}


def _movable(day, request):
    required = _required_names(request)
    return [a for a in day.attractions if not a.locked and not a.has_time_constraint and normalized(a.name) not in required]


async def _check(plan, day, request, validator, original):
    day.identify_visits()
    # Removing a stop must not move lunch across the remaining attractions.
    # Derive order from the original places, not potentially stale timeline clocks.
    original_order = [stop['id'] for stop in stops_for_day(original.model_copy(deep=True))]
    remaining = {stop['id'] for stop in stops_for_day(day)}
    order = [identity for identity in original_order if identity in remaining]
    day.visit_order = order if len(order) == len(remaining) and len(set(order)) == len(order) else []
    day.schedule, day.planned_start_times = None, {}
    # Preview has a shorter wait than a full validation. Completed routes survive;
    # cancelled routes stay unverified and cannot make a proposal feasible.
    options = {'route_timeout_seconds': PREVIEW_ROUTE_TIMEOUT_SECONDS} if validator is validate_schedule else {}
    await validator(plan.model_copy(update={"days": [day]}), request, **options)
    conflicts = list(dict.fromkeys([*day.schedule.conflicts, *(i['message'] for i in _venue_issues(plan, day, request))]))
    if conflicts:
        day.schedule.status = 'conflict'
        day.schedule.conflicts = conflicts
    return {"feasible": day.schedule.status == "feasible", "status": day.schedule.status,
            "overrun_minutes": _overrun(day), "conflicts": conflicts}


async def propose_fixes(plan, request, day_index: int, validator=validate_schedule) -> list[dict]:
    day = plan.days[day_index]
    venue_issues = _venue_issues(plan, day, request)
    if not day.schedule or not (day.schedule.conflicts or venue_issues):
        return []
    movable = _movable(day, request)
    closed = {a.item_id for a in day.attractions if parse_place_hours(a, day.date).closed}
    closed.update(i['item_id'] for i in venue_issues if i['code'] == 'inner_venue_closed' and i.get('item_id'))
    proposals = []
    if len(day.attractions) > 1:
        targets = [a for a in movable if not closed or a.item_id in closed]
        for target in sorted(targets, key=lambda a: -a.visit_duration)[:MAX_REMOVAL_PROPOSALS]:
            candidate = day.model_copy(deep=True)
            candidate.attractions = [a for a in candidate.attractions if a.item_id != target.item_id]
            candidate.description = " → ".join(a.name for a in candidate.attractions)
            outcome = await _check(plan, candidate, request, validator, day)
            proposals.append({"kind": "remove", "title": f"去掉「{target.name}」",
                              "detail": ("当天闭馆，移除该参观安排并重新核对路线" if target.item_id in closed else f"释放约 {target.visit_duration} 分钟停留时间"), "day": candidate.model_dump(), **outcome})
    need = _overrun(day) + 10
    long_visits = [a for a in movable if a.visit_duration > MIN_VISIT_MINUTES]
    spare = sum(a.visit_duration - MIN_VISIT_MINUTES for a in long_visits)
    if not closed and need > 10 and spare:
        candidate = day.model_copy(deep=True)
        changes = []
        for place in candidate.attractions:
            if place.item_id in {a.item_id for a in long_visits}:
                cut = min(place.visit_duration - MIN_VISIT_MINUTES, round(need * (place.visit_duration - MIN_VISIT_MINUTES) / spare + 0.5))
                if cut > 0:
                    changes.append(f"{place.name} {place.visit_duration}→{place.visit_duration - cut} 分钟")
                    place.visit_duration -= cut
        if changes:
            outcome = await _check(plan, candidate, request, validator, day)
            proposals.append({"kind": "shorten", "title": "缩短停留时间", "detail": "；".join(changes), "day": candidate.model_dump(), **outcome})
    # Feasible first, then the least invasive (shorten before remove when both fit).
    proposals.sort(key=lambda p: (not p["feasible"], p["overrun_minutes"], p["kind"] != "shorten"))
    return proposals
