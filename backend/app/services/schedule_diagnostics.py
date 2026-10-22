"""Explain checked schedules without another model or map request.

These are time budgets under the validator's assumptions, not opening-hours or
booking guarantees. Missing travel time is never turned into free time.
"""
from datetime import datetime, timezone
from .opening_hours import parse_place_hours


def _checked_time(value):
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        # Old rows without an offset have no trustworthy timezone.
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def diagnose_day(day, request, now=None):
    now = now or datetime.now(timezone.utc)
    schedule = day.schedule
    places = [p for p in [*day.attractions, *day.meals] if (getattr(p, "restaurant", None) or p.name).strip()]
    hours_checked = sum(parse_place_hours(p, day.date).understood for p in places)
    base = {"date": day.date, "day_index": day.day_index, "status": "unchecked"}
    base.update(opening_hours_checked=hours_checked, opening_hours_total=len(places), opening_hours_unknown=len(places)-hours_checked)
    if schedule is None:
        return {**base, "headline": "尚未校验，暂不能判断当天是否走得完"}
    known = [s for s in schedule.segments if s.status == "verified" and s.duration_minutes is not None]
    missing = len(schedule.segments) - len(known)
    visits = sum(max(0, item.duration_minutes) for item in schedule.items if item.kind == "attraction")
    meals = sum(max(0, item.duration_minutes) for item in schedule.items if item.kind == "meal")
    travel = sum(max(0, s.duration_minutes) for s in known)
    buffer = (day.buffer_minutes if day.buffer_minutes is not None else request.buffer_minutes) * len(schedule.segments)
    # minimum_minutes also includes waits for appointments and solver meal windows.
    waits = max(0, schedule.minimum_minutes - visits - meals - travel - buffer)
    complete = not missing and schedule.status != "unverified"
    overrun = max(0, schedule.minimum_minutes - schedule.available_minutes)
    remaining = max(0, schedule.available_minutes - schedule.minimum_minutes) if complete and not schedule.conflicts else None
    timestamps = [_checked_time(s.checked_at) for s in known]
    dated = [stamp for stamp in timestamps if stamp is not None]
    stale = sum(stamp is not None and (now - stamp).total_seconds() > 24 * 3600 for stamp in timestamps)
    # 24h is a UI reminder, separate from cache TTL and never a live-data guarantee.
    unknown_age = len(timestamps) - len(dated)
    longest = max(known, key=lambda s: s.duration_minutes, default=None)
    bottleneck = None if longest is None else {
        "from_name": longest.from_name, "to_name": longest.to_name,
        "duration_minutes": longest.duration_minutes, "mode": longest.mode,
    }
    if overrun:
        headline = f"{'已知安排至少' if missing else '当天安排'}超出 {overrun} 分钟"
    elif schedule.conflicts:
        headline = "预约或时间安排有冲突，不能仅凭总时长判断可行"
    elif not complete:
        headline = "交通数据不完整，暂不能判断剩余时间"
    else:
        headline = f"按已知条件可安排，剩余 {remaining} 分钟"
    return {**base, "status": schedule.status, "headline": headline,
            "visit_minutes": visits, "meal_minutes": meals, "travel_minutes": travel,
            "buffer_minutes": buffer, "waiting_minutes": waits,
            "minimum_minutes": schedule.minimum_minutes, "available_minutes": schedule.available_minutes,
            "overrun_minutes": overrun, "remaining_minutes": remaining,
            "verified_routes": len(known), "total_routes": len(schedule.segments), "missing_routes": missing,
            "stale_routes": stale, "unknown_age_routes": unknown_age,
            "oldest_checked_at": min(dated).isoformat() if dated else None,
            "longest_leg": bottleneck}


def repair_diagnostics(plan, request):
    """Compact numbers for bounded repair, with no new outside queries."""
    return [diagnose_day(day, request) for day in plan.days if day.schedule and day.schedule.conflicts]
