"""Reviewed admission references, not live inventory or reservation receipts.

Identity, city and coordinates must match. References expire after 30 days;
expired rules cannot generate precise booking times. Unknown venues have no
invented lead time. This service performs no network or model call.
"""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json

CHINA = timezone(timedelta(hours=8))
REVIEWED_ON = date(2026, 10, 18)
REFERENCE_DAYS = 30
POLICIES = (
    dict(key='palace', city='北京', poi_id='B000A8UIN8', names=('故宫博物院', '故宫'),
         bounds=(116.387, 39.909, 116.409, 39.927),
         source_url='https://www.dpm.org.cn/singles_detail/259831.html',
         message='需实名预约，不售当日票；普通门票于参观前7日20:00开始预约。请携带预约证件原件，从午门入院。年票等特殊规则请查看官方须知。',
         opens_days=7, opens_time='20:00'),
    dict(key='national', city='北京', poi_id='B000A83U0P', names=('中国国家博物馆', '国家博物馆'),
         bounds=(116.395, 39.898, 116.407, 39.912),
         source_url='https://m.chnmuseum.cn/cg/',
         message='需实名免费预约，按预约时段凭证件原件入馆；免费不代表免预约。官网说明可预约7日内参观，每日17:00放票，具体日期以预约页面为准。',
         opens_days=None, opens_time=None),
)


BOOKING_HINTS = (
    (("博物馆", "博物院", "纪念馆", "美术馆", "故宫", "遗址", "陵", "科技馆"), "博物馆、纪念馆等场馆常需提前实名预约"),
    (("剧院", "剧场", "音乐厅", "演出", "大剧院"), "演出场馆通常需提前购票"),
    (("乐园", "迪士尼", "环球影城", "欢乐谷", "长隆", "方特"), "主题乐园旺季可能限流，建议提前购票"),
)
def _booking_hint(place):
    text = f"{place.category or ''}{place.name}"
    for words, reason in BOOKING_HINTS:
        if any(word in text for word in words):
            return reason
    return None


def matching_policy(city, place, *, policies=None):
    def field(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    location = field(place, 'location')
    try:
        if location is None:
            lon, lat = map(float, field(place, 'coordinates', (None, None)))
        else:
            lon, lat = float(field(location, 'longitude')), float(field(location, 'latitude'))
    except (TypeError, ValueError):
        return None
    for policy in POLICIES if policies is None else policies:
        left, bottom, right, top = policy['bounds']
        if (city.removesuffix('市') == policy['city'] and (field(place, 'poi_id') or field(place, 'id')) == policy['poi_id']
                and field(place, 'name') in policy['names']
                and left <= lon <= right and bottom <= lat <= top):
            return policy
    return None


def scoped_restaurant(poi):
    """A map-listed child of a known reservation venue needs access review.

    This is a conservative automatic-choice policy, not proof the restaurant
    is closed or inaccessible. Explicit choices may still be kept.
    """
    coords = poi.get('coordinates')
    if not isinstance(coords, (list, tuple)) or len(coords) != 2:
        return None
    try:
        lon, lat = map(float, coords)
    except (TypeError, ValueError):
        return None
    for policy in POLICIES:
        left, bottom, right, top = policy['bounds']
        if poi.get('parent_poi_id') == policy['poi_id'] and left <= lon <= right and bottom <= lat <= top:
            return policy['names'][0]
    return None


def candidate_reference(city, poi):
    from .park_scopes import matching_park
    policy = matching_policy(city, poi) or matching_park(city,poi)
    if not policy:
        return None
    reviewed=date.fromisoformat(policy.get('reviewed_on',REVIEWED_ON.isoformat()))
    fresh = 0 <= (datetime.now(CHINA).date() - reviewed).days <= REFERENCE_DAYS
    return dict(status='reviewed' if fresh else 'expired', reviewed_on=reviewed.isoformat(),
                source_url=policy['source_url'], booking_status='unverified',
                message=policy['message'] if fresh else '规则参考已过期，需查官方当前政策；不得据此断言可入内。')


def reminder_id(day, place, policy=None):
    # A different venue, date or fixed slot must not inherit a checked box.
    payload = [day.date, place.poi_id, place.name, place.address,
               place.location.model_dump() if place.location else None,
               place.fixed_start_time, policy['key'] if policy else None,
               policy.get('reviewed_on',REVIEWED_ON.isoformat()) if policy else None]
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:32]


def visit_reminders(plan, *, now=None):
    from .park_scopes import matching_park,park_message
    now = now or datetime.now(CHINA)
    if now.tzinfo is None:
        raise ValueError('Reference time must have a timezone')
    today = now.astimezone(CHINA).date()
    reminders = []
    for day in plan.days:
        for place in day.attractions:
            policy = matching_policy(plan.city, place) or matching_park(plan.city,place)
            hint = _booking_hint(place)
            if not policy and not hint and not place.fixed_start_time:
                continue
            item = dict(id=reminder_id(day, place, policy), date=day.date, day_index=day.day_index,
                        name=place.name, kind='likely_booking', confirm_by=None,
                        booking_status='unverified', booking_opens_at=None,
                        policy_source_url=None, policy_reviewed_on=None,
                        policy_status='unknown', fixed_start_time=place.fixed_start_time)
            if policy:
                reviewed=date.fromisoformat(policy.get('reviewed_on',REVIEWED_ON.isoformat()))
                fresh = 0 <= (today - reviewed).days <= REFERENCE_DAYS
                item.update(kind='official_reference', policy_source_url=policy['source_url'],
                            policy_reviewed_on=reviewed.isoformat(), policy_status='reviewed' if fresh else 'expired')
                message=park_message(policy,day.date) if fresh and 'inner_names' in policy else policy['message']
                item['message'] = (message if fresh else '参观规则参考已过复核期限，请先打开官方须知核对当前要求。')
                if fresh and policy['opens_days'] is not None:
                    opening_day = date.fromisoformat(day.date) - timedelta(days=policy['opens_days'])
                    item['booking_opens_at'] = f"{opening_day}T{policy['opens_time']}:00+08:00"
            elif place.fixed_start_time:
                item.update(kind='appointment', message='已设置固定游览时间，请核对是否需要预约及是否已成功；设置时间不等于订票。')
            else:
                item['message'] = '该地点的入内范围、预约要求与放票时间尚未核实，请查看官方渠道。'
            if place.fixed_start_time:
                item['message'] += f' 行程固定时间为 {place.fixed_start_time}，预约成功情况仍未核实。'
            if policy and policy['key'] == 'palace' and day.date == today.isoformat():
                item['message'] += ' 行程为今天，请先核对是否已持有效预约。'
            reminders.append(item)
    return reminders
