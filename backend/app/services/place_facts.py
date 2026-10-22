"""Map business facts, scoped official admission references and model fallback.

Every price carries its source in cost_source. A user-entered price (cost_status
"confirmed") is never overwritten; provider prices win over model estimates.
"""
import asyncio
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from .fact_evidence import fact_state, needs_refresh, record_fact

AMAP_MEAL_SOURCE = "高德人均参考，未核实"
AMAP_HOTEL_SOURCE = "高德最低价参考，未核实"
MODEL_TICKET_SOURCE = "模型估算，未核实"

TICKET_SYSTEM = """你是旅行费用助手。根据公开常识给出中国景点的成人门票参考价（人民币整数）。
免费开放的景点填 0；不确定、按项目分别收费或无法判断时填 null，不要猜。
不要解释，只输出一个 JSON 对象：键为给定的 id，值为整数或 null。"""


def apply_provider_facts(place, poi: dict, only_missing: bool = False) -> None:
    """Copy Amap business fields onto an Attraction / Meal / Hotel."""
    if poi.get("open_hours") and (not only_missing or fact_state(place, 'hours')['status'] != 'user_provided'):
        place.open_hours = poi["open_hours"]
        record_fact(place, 'hours', 'amap', poi.get('observed_at'))
    kind = type(place).__name__
    from .place_identity import parent_id
    place.parent_poi_id = parent_id(poi.get('parent_poi_id'), place.poi_id)
    record_fact(place, 'parent', 'amap', poi.get('observed_at'))
    if kind == 'Meal': place.category=poi.get('category') or None
    if poi.get("rating") is not None:
        if kind == "Hotel":
            place.rating = place.rating if only_missing and place.rating else str(poi["rating"])
        elif kind == "Attraction" and not (only_missing and place.rating):
            place.rating = poi["rating"]
    price = poi.get("avg_cost") if kind == "Meal" else poi.get("hotel_price") if kind == "Hotel" else None
    replaceable = place.cost_status == "unknown" or (
        place.cost_status == "estimated" and (not only_missing or fact_state(place, 'price')['status'] != 'user_provided'))
    if price is not None and replaceable:
        place.estimated_cost = price
        place.cost_status = "estimated"
        place.cost_source = AMAP_MEAL_SOURCE if kind == "Meal" else AMAP_HOTEL_SOURCE
        record_fact(place, 'price', 'amap', poi.get('observed_at'))


def _unpriced(attraction) -> bool:
    return attraction.cost_status == "unknown" and attraction.ticket_price is None


async def estimate_ticket_prices(plan, llm) -> int:
    """One model call for every attraction without a price; returns how many got an estimate."""
    targets = {a.item_id: a for d in plan.days for a in d.attractions if _unpriced(a)}
    if not targets:
        return 0
    items = [{"id": key, "name": a.name, "category": (a.category or "")[:30]} for key, a in targets.items()]
    response = await llm.ainvoke([SystemMessage(content=TICKET_SYSTEM),
                                  HumanMessage(content=f"城市：{plan.city}\n景点：{json.dumps(items, ensure_ascii=False)}")])
    text = response.content if isinstance(response.content, str) else str(response.content)
    start = text.find("{")
    if start < 0:
        return 0
    values, _ = json.JSONDecoder().raw_decode(text[start:])
    count = 0
    for key, value in values.items() if isinstance(values, dict) else []:
        place = targets.get(key)
        if place is None or isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 3000:
            continue
        place.ticket_price = round(value)
        place.cost_status = "estimated"
        place.cost_source = MODEL_TICKET_SOURCE
        record_fact(place, 'price', 'model')
        count += 1
    return count


async def fill_ticket_prices(plan, llm, official_lookup=None) -> dict:
    from .official_tickets import refresh_official_tickets
    official = await refresh_official_tickets(plan, official_lookup)
    try:
        estimated = await estimate_ticket_prices(plan, llm)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"⚠️ 门票估算失败：{type(exc).__name__}")
        estimated = 0
    return {**official, 'tickets_estimated': estimated}


async def enrich_plan(plan, llm, detail, official_lookup=None) -> dict:
    """Fill map fields, prefer supported official admission, then estimate unknown tickets."""
    places = [p for d in plan.days for p in [*d.attractions, *d.meals, *([d.hotel] if d.hotel else [])] if p.poi_id]
    wanted = [p for p in places if needs_refresh(p, 'hours') or (type(p).__name__ != "Attraction" and needs_refresh(p, 'price'))]
    ids = list(dict.fromkeys(p.poi_id for p in wanted))
    gate = asyncio.Semaphore(4)
    async def lookup(poi_id):
        async with gate:
            return await detail(poi_id)
    results = await asyncio.gather(*(lookup(poi_id) for poi_id in ids), return_exceptions=True)
    found = {poi_id: poi for poi_id, poi in zip(ids, results) if isinstance(poi, dict) and poi.get('poi_id') == poi_id}
    for place in wanted:
        if place.poi_id in found:
            apply_provider_facts(place, found[place.poi_id], only_missing=True)
    tickets = await fill_ticket_prices(plan, llm, official_lookup)
    return {"looked_up": len(ids), "found": len(found), "failed": len(ids) - len(found), **tickets}


WEEKDAYS = "一二三四五六日"
_CLOSED = re.compile(r"(?:每)?周([一二三四五六日天])[^，；。,;]{0,12}?(?:闭馆|不开放|休馆|休息|闭园)")


def closed_weekdays(open_hours: str | None) -> set[int]:
    """Weekday numbers (Monday=0) that published opening hours say are closed."""
    return {WEEKDAYS.index("日" if day == "天" else day) for day in _CLOSED.findall(open_hours or "")}
