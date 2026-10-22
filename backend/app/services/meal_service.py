"""Screen impossible automatic meal slots before spending route queries.

Possible means only that the registered hours leave room for a meal; it does
not prove arrival, kitchen service, seating or same-day opening. Unknown hours
remain eligible. Explicit user clocks/order are not replaced by default windows.
"""
from .opening_hours import parse_place_hours
from .schedule_validator import minutes


def meal_service(place, day, meal_type, request=None):
    if meal_type not in ('breakfast', 'lunch', 'dinner'):
        return 'unknown'
    rule = parse_place_hours(place, day.date)
    if rule.closed or rule.outside_weekday:
        return 'excluded'
    if not rule.understood or not rule.windows:
        return 'unknown'
    duration = 30 if meal_type == 'breakfast' else 60
    start = minutes(day.start_time or (request.daily_start_time if request else '00:00'))
    end = minutes(day.end_time or (request.daily_end_time if request else '23:59')) - duration
    if request:
        from .meal_timing import WINDOWS, explicit_meal_times
        from .initial_planning import has_day_instructions
        if not explicit_meal_times(request) and not has_day_instructions(request):
            lo, hi = map(minutes, WINDOWS[meal_type])
            start, end = max(start, lo), min(end, hi)
    # Integer minute scheduling: arrival at the published last-admission cutoff
    # is already too late, even when a full meal would fit before closing.
    if rule.last_admission is not None:
        end = min(end, rule.last_admission - 1)
    return 'possible' if any(max(start, a) <= min(end, b-duration) for a,b in rule.windows) else 'excluded'
