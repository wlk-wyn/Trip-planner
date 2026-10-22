"""Check that a stored timeline still describes the actual places and edges."""
import re
from .schedule_validator import stops_for_day, display_name, minutes


def _minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', value):
        raise ValueError('invalid clock')
    return minutes(value)


def check_integrity(day, request=None):
    schedule = day.schedule
    if schedule is None:
        return []
    stops = stops_for_day(day.model_copy(deep=True))
    aliases = {f"attraction-{i}": place.item_id for i, place in enumerate(day.attractions)}
    for i, place in enumerate(day.meals):
        aliases[f"meal-{i}"] = place.item_id
        if sum(p.type == place.type for p in day.meals) == 1:
            aliases[place.type] = place.item_id
    if day.hotel:
        aliases.update({'hotel-start': day.hotel.item_id + '-start', 'hotel-end': day.hotel.item_id + '-end'})
    canonical = lambda value: aliases.get(value, value)
    problems = []
    from .venue_routing import geometry_errors
    problems.extend(geometry_errors(stops,schedule.segments,request.city if request else None))
    if [canonical(item.id) for item in schedule.items] != [s['id'] for s in stops]:
        problems.append('时间线与当天地点或顺序不一致，请重新校验')
    elif any(item.kind != stop['kind'] or item.name != display_name(stop) or item.duration_minutes != stop['duration'] or item.fixed != bool(stop['fixed']) for item, stop in zip(schedule.items, stops)):
        problems.append('时间线的地点或停留时长已过期，请重新校验')
    expected = [(s['id'], t['id']) for s, t in zip(stops, stops[1:])]
    if [(canonical(s.from_id), canonical(s.to_id)) for s in schedule.segments] != expected:
        problems.append('交通路段与当天地点不对应，请重新校验')
    if any(s.status == 'verified' and (s.duration_minutes is None or s.duration_minutes < 0) for s in schedule.segments):
        problems.append('已核实交通缺少有效耗时，请重新校验')
    if schedule.status == 'feasible':
        try:
            start, end = _minutes(schedule.window_start), _minutes(schedule.window_end)
            if request and (schedule.window_start != (day.start_time or request.daily_start_time) or schedule.window_end != (day.end_time or request.daily_end_time)):
                raise ValueError()
            if not 0 <= start < end < 1440:
                raise ValueError()
            if schedule.available_minutes != end-start or schedule.minimum_minutes != (_minutes(schedule.items[-1].end_time)-start if schedule.items else 0):
                raise ValueError()
            for i, item in enumerate(schedule.items):
                if not item.start_time or not item.end_time:
                    raise ValueError()
                a, b = _minutes(item.start_time), _minutes(item.end_time)
                if i < len(stops) and stops[i]['fixed'] and item.start_time != stops[i]['fixed']:
                    raise ValueError()
                if not start <= a <= b <= end or b-a != item.duration_minutes:
                    raise ValueError()
                if i:
                    segment = schedule.segments[i-1]
                    if segment.status != 'verified' or segment.duration_minutes is None:
                        raise ValueError()
                    buffer = day.buffer_minutes if day.buffer_minutes is not None else request.buffer_minutes if request else 0
                    if a < _minutes(schedule.items[i-1].end_time) + segment.duration_minutes + buffer:
                        raise ValueError()
        except (ValueError, IndexError, TypeError, AttributeError):
            problems.append('时间线的到达或结束时间无法支持“可安排”结论，请重新校验')
    return [{'code': 'schedule_integrity', 'severity': 'error', 'message': text, 'day_index': day.day_index} for text in problems]
