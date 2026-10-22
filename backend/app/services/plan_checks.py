"""Rule-based plan checks that need no model or network.

Inspired by the bad-case lists in hello-agents Extra12 and the hard/commonsense
constraints of ChinaTravel; every rule here is computed from the plan itself.
"""
import math
import re
from statistics import median

from .reference_service import normalized

from ..models.schemas import Location
from .opening_hours import hours_issues
from .schedule_integrity import check_integrity
from .cost_breakdown import build_cost_report, stated_budget
from .visit_advice import OPERATIONAL_CLAIM

# Mainland China, Hong Kong, Macau and Taiwan bounding box in GCJ-02 degrees.
CHINA_BOUNDS = (73.0, 136.0, 3.0, 54.0)
OUTLIER_KM = 80
MAX_ATTRACTIONS_PER_DAY = 6
LONG_VISIT_MINUTES = 360


def _km(a, b) -> float:
    lat = math.radians((a.latitude + b.latitude) / 2)
    dx = (a.longitude - b.longitude) * 111.32 * math.cos(lat)
    dy = (a.latitude - b.latitude) * 110.57
    return math.hypot(dx, dy)


def _issue(code, severity, message, day_index=None):
    return {"code": code, "severity": severity, "message": message, "day_index": day_index}




def known_cost(plan, request=None) -> float:
    return build_cost_report(plan, request)['known_total']


def run_checks(plan, request) -> list[dict]:
    issues = []
    from .venue_routing import gate_issues
    issues.extend(gate_issues(plan))
    from .park_scopes import park_scope_issues
    issues.extend(park_scope_issues(plan,request))
    text=request.free_text_input or ''
    three_meals=any(word in text for word in ('早午晚三餐','一日三餐','正常三餐','三餐都安排')) and not re.search(r'(?:不(?:用|必|需要)(?:安排|吃)?|不要(?:安排|吃)?|无需(?:安排)?|取消)\s*(?:早午晚)?三餐',text)
    from .attraction_candidates import overlap_issues
    issues.extend(overlap_issues(plan, request))
    from .daily_limits import daily_limit_issues
    issues.extend(daily_limit_issues(plan, request))
    from .restaurant_candidates import meal_identity_issues
    issues.extend(meal_identity_issues(plan,request))
    from .evening_planning import evening_issues
    issues.extend(evening_issues(plan, request))
    from .meal_timing import meal_timing_issues
    issues.extend(meal_timing_issues(plan, request))
    seen_attractions: dict[str, int] = {}
    seen_restaurants: dict[str, int] = {}
    located = []
    for day in plan.days:
        issues.extend(hours_issues(day))
        issues.extend(check_integrity(day, request))
        label = f"第{day.day_index + 1}天"
        for place in day.attractions:
            if OPERATIONAL_CLAIM.search(place.description or ''):
                issues.append({**_issue('visit_advice_claim','info',
                    f'{place.name}的游览建议或备注含时间、价格、交通或预约等操作信息，未凭此文字认定已核实；请对照对应来源。',day.day_index),
                    'item_id':place.item_id})
            key = place.poi_id or normalized(place.name)
            if key in seen_attractions:
                first = seen_attractions[key]
                where = "同一天" if first == day.day_index else f"第{first + 1}天和{label}"
                issues.append(_issue("duplicate_attraction", "warning", f"{place.name}在{where}重复安排", day.day_index))
            else:
                seen_attractions[key] = day.day_index
            if place.visit_duration > LONG_VISIT_MINUTES:
                issues.append(_issue("long_visit", "info", f"{label}{place.name}建议停留 {place.visit_duration} 分钟，超过 6 小时，请确认", day.day_index))
        for meal in day.meals:
            name = meal.restaurant or meal.name
            if not name:
                continue
            key = meal.poi_id or normalized(name)
            if key in seen_restaurants:
                issues.append(_issue("duplicate_restaurant", "info", f"{name}在第{seen_restaurants[key] + 1}天和{label}都有安排", day.day_index))
            else:
                seen_restaurants[key] = day.day_index
        slots = {meal.type for meal in day.meals if (meal.restaurant or meal.name)}
        required = (("breakfast", "早餐"), ("lunch", "午餐"), ("dinner", "晚餐")) if three_meals else (("lunch", "午餐"), ("dinner", "晚餐"))
        missing = [label_ for slot, label_ in required if slot not in slots
                   and not re.search(r'(?:不吃|不安排|无需安排|省略|跳过|不用安排)\s*' + label_, text)]
        if missing:
            issues.append(_issue("missing_meal", "warning" if three_meals else "info", f"{label}尚未安排{'、'.join(missing)}，可在编辑中补充", day.day_index))
        if len(day.attractions) > MAX_ATTRACTIONS_PER_DAY:
            issues.append(_issue("overloaded_day", "warning", f"{label}安排了 {len(day.attractions)} 个景点，节奏可能过紧", day.day_index))
        for place in [*day.attractions, *day.meals, *([day.hotel] if day.hotel else [])]:
            if place.location:
                located.append((day.day_index, getattr(place, "restaurant", None) or place.name, place.location))

    lo_lng, hi_lng, lo_lat, hi_lat = CHINA_BOUNDS
    inside = []
    for day_index, name, loc in located:
        if not (lo_lng <= loc.longitude <= hi_lng and lo_lat <= loc.latitude <= hi_lat):
            issues.append(_issue("invalid_location", "error", f"{name}的坐标不在国内范围，请重新选择地点", day_index))
        else:
            inside.append((day_index, name, loc))
    if len(inside) >= 3:
        center = Location(longitude=median(l.longitude for *_, l in inside), latitude=median(l.latitude for *_, l in inside))
        for day_index, name, loc in inside:
            distance = _km(center, loc)
            if distance > OUTLIER_KM:
                issues.append(_issue("location_outlier", "warning", f"{name}距离行程中心约 {round(distance)} 公里，可能不在{plan.city}市区或匹配到了同名地点", day_index))

    hotels = [(day.day_index, day.hotel) for day in plan.days[:-1]]  # the last night is usually not in the city
    named = [(i, h) for i, h in hotels if h]
    if named and len(named) < len(hotels):
        nights = "、".join(f"第{i + 1}天" for i, h in hotels if not h)
        issues.append(_issue("missing_hotel", "info", f"{nights}晚上还没有安排住宿"))
    for (i, a), (_, b) in zip(named, named[1:]):
        if (a.poi_id or a.name) != (b.poi_id or b.name):
            issues.append(_issue("hotel_change", "info", f"第{i + 2}天更换酒店（{a.name} → {b.name}），注意退房和行李安排", i + 1))

    cost_report = build_cost_report(plan, request)
    if cost_report['budget']['status'] == 'over_reference':
        issues.append(_issue("over_budget", "warning", f"已列参考费用 ¥{cost_report['known_total']:g}，超过目的地内全体预算 ¥{cost_report['budget']['limit']:g}；未知项目另计，实际以预订为准"))
    return issues
