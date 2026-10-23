"""Replay user entry constraints against the saved route snapshot, without I/O."""
from ..models.schemas import RouteSegment
from .schedule_validator import stops_for_day, build_schedule, minutes, clock
from .schedule_integrity import check_integrity
from .meal_timing import meal_timing_issues


def entry_preview(plan, request, day_date, item_id, fixed_time=None, entry_window=None):
    day = next((day for day in plan.days if day.date == day_date), None)
    place = next((p for p in day.attractions if p.item_id == item_id), None) if day else None
    if not place:
        raise KeyError('景点不存在')
    old = day.model_copy(deep=True)
    candidate = day.model_copy(deep=True)
    target = next(p for p in candidate.attractions if p.item_id == item_id)
    target.fixed_start_time, target.entry_window = fixed_time, entry_window
    candidate.planned_start_times.pop(item_id, None)
    stops = stops_for_day(candidate)
    # Never reuse a stale/mismatched route graph to claim a feasible appointment.
    usable = bool(old.schedule) and not check_integrity(old, request)
    if usable:
        segments = [s.model_copy(deep=True) for s in old.schedule.segments]
    else:
        mode = 'walking' if day.transportation == '步行' else 'driving' if day.transportation == '自驾' else 'transit'
        segments = [RouteSegment(from_id=a['id'], to_id=b['id'], from_name=a['place'].name,
                     to_name=b['place'].name, mode=mode, status='unverified',
                     note='原交通时间线缺失或不一致，请重新校验交通') for a, b in zip(stops, stops[1:])]

    def replay(value):
        value.schedule = build_schedule(value, stops_for_day(value), [s.model_copy(deep=True) for s in segments], request)
        scoped = plan.model_copy(update={'days': [value]})
        for issue in meal_timing_issues(scoped, request):
            value.schedule.warnings.append(issue['message'])
        value.schedule.warnings.append('本次预览复用已保存的交通查询，不含新的路况、排队或余票查询；入场时间由你填写。')
        return value

    replay(candidate)
    arrival = next((i.start_time for i in candidate.schedule.items if i.id == item_id), None)
    deadline = fixed_time or (entry_window.end if entry_window else None)
    earlier = None
    if usable and arrival and deadline and candidate.schedule.status == 'conflict':
        advance = minutes(arrival) - minutes(deadline)
        start = minutes(candidate.start_time or request.daily_start_time)
        # A bounded, explicit departure change; no dropped visits or shorter stays.
        if 0 < advance <= 120 and start >= advance:
            adjusted = candidate.model_copy(deep=True)
            adjusted.start_time = clock(start - advance)
            replay(adjusted)
            if adjusted.schedule.status == 'feasible':
                earlier = adjusted
    return candidate, earlier
