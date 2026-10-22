"""A factual itinerary outline, without inheriting stale generated prose."""
from .schedule_validator import stops_for_day, display_name


def day_outline(day):
    return ' → '.join(display_name(s) for s in stops_for_day(day.model_copy(deep=True)) if s['kind']=='attraction') or '暂无景点安排'


def plan_overview(plan):
    lines=[]
    for day in plan.days:
        lines.append(f'第{day.day_index+1}天（{day.date}）：'+day_outline(day)+'。')
    lines.append('地点、建议停留与交通请以当前时间线和冲突提示为准；开放、预约和价格仍需另行确认。')
    if plan.adjustment_notes:
        lines.append('调整记录（含移出路线的备选）：')
        lines.extend(plan.adjustment_notes)
    return '\n'.join(lines)
