"""Weather-aware suggestions: flag outdoor visits on wet days and offer nearby indoor POIs.

Read-only. Nothing here edits a plan; the user picks a candidate, which becomes an
ordinary draft edit that is re-validated on save.
"""
import asyncio

from .amap_rest import get_forecast, search_nearby_restaurants
from ..agents.langgraph_planner import _poi_as_alternative

WET = ("雨", "雪", "雷", "冰雹", "沙尘", "霾")
INDOOR = ("博物馆", "美术馆", "展览", "纪念馆", "科技馆", "天文馆", "图书馆", "剧院", "剧场", "音乐厅",
          "购物", "商场", "商城", "购物中心", "水族馆", "海洋馆", "室内", "艺术中心", "书店", "影院")
OUTDOOR = ("风景名胜", "公园", "广场", "植物园", "动物园", "海滩", "沙滩", "湿地", "森林", "长城", "古镇", "步道",
           "山", "湖", "岛", "峡", "瀑布", "江", "河", "园林", "景区", "古街", "老街", "胡同", "码头", "游乐园")
# Amap POI types: museums, exhibition halls, art galleries, science museums, shopping malls.
INDOOR_TYPES = "140100|140200|140400|140600|060100"


def classify(attraction) -> str:
    """'indoor', 'outdoor' or 'unknown'. Amap category first; name only as a fallback."""
    category = attraction.category or ""
    for text in (category, attraction.name):
        if any(word in text for word in INDOOR):
            return "indoor"
        if any(word in text for word in OUTDOOR):
            return "outdoor"
    return "unknown"


def is_wet(forecast: dict) -> bool:
    return any(word in f"{forecast.get('dayweather', '')}{forecast.get('nightweather', '')}" for word in WET)


async def weather_advice(plan, searcher=search_nearby_restaurants, forecaster=get_forecast) -> dict:
    try:
        forecasts = {item.get("date"): item for item in await forecaster(plan.city)}
    except RuntimeError as exc:
        return {"forecast_available": False, "note": f"暂时无法获取天气预报：{exc}", "days": []}
    used = {key for day in plan.days for p in day.attractions for key in (p.poi_id, p.name) if key}
    days, lookups = [], []
    for day in plan.days:
        forecast = forecasts.get(day.date)
        entry = {"date": day.date, "day_index": day.day_index, "suggestions": [],
                 "weather": ({"day": forecast.get("dayweather", ""), "night": forecast.get("nightweather", ""),
                              "high": forecast.get("daytemp"), "low": forecast.get("nighttemp"),
                              "source":forecast.get('source'),"observed_at":forecast.get('observed_at'),
                              "report_time":forecast.get('report_time'),"adcode":forecast.get('adcode')} if forecast else None),
                 "wet": bool(forecast and is_wet(forecast))}
        if entry["wet"]:
            for attraction in day.attractions:
                if attraction.locked or attraction.fixed_start_time or classify(attraction) != "outdoor":
                    continue
                suggestion = {"item_id": attraction.item_id, "name": attraction.name, "candidates": [],
                              "reason": f"预报{entry['weather']['day']}，{attraction.name}以户外游览为主"}
                entry["suggestions"].append(suggestion)
                lookups.append((suggestion, attraction))
        days.append(entry)

    async def candidates(attraction):
        pois = await searcher(attraction.location.longitude, attraction.location.latitude, "", 5000, INDOOR_TYPES)
        return [poi for poi in pois if poi.get("poi_id") not in used and poi["name"] not in used][:8]
    results = await asyncio.gather(*(candidates(a) for _, a in lookups), return_exceptions=True)
    offered: set[str] = set()
    for (suggestion, attraction), result in zip(lookups, results):
        if isinstance(result, Exception):
            suggestion["error"] = "附近室内地点查询失败，可稍后重试"
            continue
        # Prefer venues not already offered for an earlier wet stop, so consecutive rainy days differ.
        fresh = [poi for poi in result if (poi.get("poi_id") or poi["name"]) not in offered]
        chosen = (fresh + [poi for poi in result if poi not in fresh])[:3]
        if chosen:
            offered.add(chosen[0].get("poi_id") or chosen[0]["name"])  # only the top pick needs to differ
        suggestion["candidates"] = [{**_poi_as_alternative(poi, attraction.location), "poi_id": poi.get("poi_id", ""),
                                     "visit_duration": attraction.visit_duration} for poi in chosen]
    covered = [d for d in days if d["weather"]]
    note = ("天气为查询时的短期预报，出行前请再次确认。" if covered else
            "出行日期超出高德短期预报范围（约 4 天），临近出发时再检查。")
    if covered and len(covered) < len(days):
        note += f" 仅 {len(covered)}/{len(days)} 天有预报。"
    return {"forecast_available": bool(covered), "note": note, "days": days}
