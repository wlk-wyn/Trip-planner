"""Pre-departure checklist: booking reminders, lodging summary and a packing list.

Reviewed venue references and generic type reminders remain separate from
actual reservations. No live inventory or successful booking is implied.
"""
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json

from .weather_advice import classify, is_wet
from ..models.schemas import WeatherInfo

BASE_ITEMS = ["身份证件（多数景区和酒店需要实名）", "手机与充电宝", "常用药品"]


def saved_forecasts(plan, *, now=None):
    """Use fresh saved provider evidence; opening a checklist needs no API call."""
    now = now or datetime.now(timezone.utc)
    china = timezone(timedelta(hours=8))
    result = {}
    for weather in plan.weather_info:
        if weather.source != 'amap' or not weather.observed_at or not weather.report_time:
            continue
        try:
            queried = datetime.fromisoformat(weather.observed_at)
            reported = datetime.fromisoformat(weather.report_time)
            if queried.tzinfo is None:
                continue
            if reported.tzinfo is None:
                reported = reported.replace(tzinfo=china)
            if any(not -300 <= (now - stamp).total_seconds() <= 86400 for stamp in (queried, reported)):
                continue
        except (TypeError, ValueError):
            continue
        result[weather.date] = dict(date=weather.date, dayweather=weather.day_weather,
                                   nightweather=weather.night_weather, daytemp=weather.day_temp,
                                   nighttemp=weather.night_temp)
    return result


def build_prep(plan, forecasts: dict | None = None) -> dict:
    from .visitor_access import visit_reminders
    reminders = visit_reminders(plan)

    from .lodging_nights import lodging_dates, hotel_for_night
    from .hotel_quotes import applicable_quote
    dates=lodging_dates(plan)
    stays = []
    for night in dates or []:
        hotel = hotel_for_night(plan,night)
        key = [hotel.poi_id,hotel.name,hotel.address,hotel.location.model_dump() if hotel.location else None] if hotel else None
        quote=applicable_quote(hotel,night) if hotel else None
        terms=[night,quote.room_type,quote.price_per_room,quote.taxes_included] if quote else [night,None]
        if stays and stays[-1]["key"] == key:
            stays[-1]["nights"] += 1
            stays[-1]["check_out"] = (date.fromisoformat(night) + timedelta(days=1)).isoformat()
            stays[-1]['terms'].append(terms)
        else:
            stays.append({"key": key, "terms":[terms],"name": hotel.name if hotel else None, "address": hotel.address if hotel else "",
                          "check_in": night, "check_out": (date.fromisoformat(night) + timedelta(days=1)).isoformat(), "nights": 1})
    for stay in stays:
        payload=[stay['check_in'],stay['check_out'],stay['key'],stay['terms']]
        stay['id']=sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:32]
        stay.pop("key")
        stay.pop('terms')
    lodging_note=('住宿日期无效或超出支持范围，请先核对行程日期。' if dates is None else
                  '按当前日期为 0 晚住宿；如需前一晚抵达或返程后住宿，请另行安排。' if not dates else
                  f'按出发日至返程日前一晚计算，共 {len(dates)} 晚；前后延住另行安排。')

    outdoor = sum(classify(a) == "outdoor" for d in plan.days for a in d.attractions)
    total = sum(len(d.attractions) for d in plan.days) or 1
    packing = [{"item": item, "reason": "基础"} for item in BASE_ITEMS]
    if outdoor:
        packing.append({"item": "舒适的步行鞋", "reason": f"{outdoor} 处户外景点"})
    if outdoor / total >= 0.5:
        packing.append({"item": "防晒用品与饮用水", "reason": "户外游览占多数"})
    forecasts = forecasts or {}
    covered = [forecasts[d.date] for d in plan.days if d.date in forecasts]
    if any(is_wet(f) for f in covered):
        packing.append({"item": "雨伞或雨衣、防水鞋套", "reason": "预报有雨雪"})
    temps = [value for f in covered for t in (f.get('daytemp'),f.get('nighttemp')) if (value:=WeatherInfo.parse_temperature(t)) is not None]
    if temps and min(temps) <= 10:
        packing.append({"item": "保暖外套", "reason": f"预报最低约 {min(temps)}°C"})
    if temps and max(temps) >= 30:
        packing.append({"item": "轻薄透气衣物、防暑用品", "reason": f"预报最高约 {max(temps)}°C"})
    weather_note = (f"已参考 {len(covered)}/{len(plan.days)} 天的短期预报。" if covered
                    else "出行日期暂无短期预报，临近出发请查看天气后补充衣物。")
    return {"reminders": reminders, "stays": stays, "lodging_note":lodging_note,"packing": packing, "weather_note": weather_note,
            "note": "部分场馆提供已注明复核日期的官方规则参考；其余按类型提醒。未查询实时余票，预约成功情况需自行核对。"}
