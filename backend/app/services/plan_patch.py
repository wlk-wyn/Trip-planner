"""Apply bounded edits to a copy; new places must reference map evidence."""
import json
from uuid import uuid4
from ..models.schemas import PlanPatch, TripPlan, Attraction, Meal, Hotel, Location
from .place_facts import apply_provider_facts


def apply_patch(current: TripPlan, patch: PlanPatch, evidence: dict, scope=None):
    plan = current.model_copy(deep=True)
    affected = set()
    keys = {"attraction": "attractions_info", "meal": "restaurants_info", "hotel": "hotels_info"}
    for op in patch.operations:
        index = next((i for i, d in enumerate(plan.days) if d.date == op.date), None)
        if index is None or (scope is not None and index not in scope):
            raise ValueError("修改超出指定日期范围")
        day = plan.days[index]
        affected.add(index)
        if op.action == "update_day":
            for key in ("start_time", "end_time", "buffer_minutes"):
                value = getattr(op, key)
                if value is not None:
                    setattr(day, key, value)
            continue
        places = day.attractions if op.kind == "attraction" else day.meals if op.kind == "meal" else ([day.hotel] if day.hotel else [])
        target = next((p for p in places if p.item_id == op.item_id), None)
        if op.action == "reorder":
            if op.kind == "hotel" or len(op.order) != len(places) or set(op.order) != {p.item_id for p in places}:
                raise ValueError("排序必须包含该类全部访问标识且不能重复")
            ordered = sorted(places, key=lambda p: op.order.index(p.item_id))
            if any(p.locked and ordered[i].item_id != p.item_id for i, p in enumerate(places)):
                raise ValueError("不能移动锁定地点")
            if op.kind == "attraction": day.attractions = ordered
            else: day.meals = ordered
            continue
        if op.action != "add" and target is None:
            raise ValueError("目标地点已不存在，请根据当前访问标识修改")
        if target and (target.locked or getattr(target, "fixed_start_time", None)):
            raise ValueError("不能更改锁定地点或固定预约")
        if op.action == "move_visit":
            target_index = next((i for i,d in enumerate(plan.days) if d.date==op.target_date),None)
            if target_index is None or target_index==index or (scope is not None and target_index not in scope) or op.kind=="hotel":
                raise ValueError("跨日移动超出范围或地点类型不支持")
            destination=plan.days[target_index]
            destination_places=destination.attractions if op.kind=="attraction" else destination.meals
            if op.kind=="meal" and target.type!="snack" and any(p.type==target.type for p in destination_places):
                raise ValueError("目标日期已有该餐次，请明确替换或改为加餐")
            places.remove(target); destination_places.append(target); affected.add(target_index)
            continue
        if op.action == "update_visit":
            if op.kind != "attraction" or op.visit_duration is None:
                raise ValueError("只能设置景点停留时长")
            target.visit_duration = op.visit_duration
            continue
        if op.action == "remove":
            if op.kind == "hotel":
                day.hotel = None; day.accommodation = "住宿待选择"
            else: places.remove(target)
            continue
        if op.action not in {"add", "replace"}:
            raise ValueError("不支持的修改")
        poi = next((p for p in json.loads(evidence.get(keys[op.kind]) or "[]") if op.poi_id and p.get("poi_id") == op.poi_id), None)
        if not poi:
            raise ValueError("新增地点需要从地图候选中选择真实 POI ID")
        common = dict(item_id=target.item_id if target else uuid4().hex, poi_id=op.poi_id,
                      name=poi["name"], address=poi.get("address", ""),
                      location=Location(longitude=poi["coordinates"][0], latitude=poi["coordinates"][1]))
        if op.kind == "attraction":
            new = Attraction(**common, visit_duration=op.visit_duration or 90, description="已查询的地点，停留时长为建议",
                             category=poi.get("category") or "景点")
        elif op.kind == "meal":
            slot = target.type if target else op.meal_type
            if not target and slot != "snack" and any(p.type == slot for p in places):
                raise ValueError("该餐次已有安排，请替换现有餐厅")
            new = Meal(**common, restaurant=poi["name"], type=slot)
        else:
            if op.action == "add" and day.hotel:
                raise ValueError("当天已有酒店，请使用替换")
            new = Hotel(**common)
            apply_provider_facts(new, poi)
            day.hotel = new; day.accommodation = new.name
            continue
        apply_provider_facts(new, poi)
        if target: places[places.index(target)] = new
        else: places.append(new)
    for index in affected:
        day = plan.days[index]
        if not day.attractions:
            raise ValueError("每天至少保留一个景点")
        day.description = " → ".join(p.name for p in day.attractions)
        day.schedule = None
        day.visit_order = []
        day.planned_start_times = {}
    return plan, affected


async def validate_changed_days(plan, previous, request, validator):
    """Reuse server-owned schedules for unchanged days, never client assertions."""
    previous = TripPlan.model_validate(previous) if isinstance(previous, dict) else previous
    if [d.date for d in plan.days] != [d.date for d in previous.days]:
        raise ValueError("不能添加、删除或更改旅行日期")
    changed = []
    for day, old in zip(plan.days, previous.days):
        if day.model_dump(exclude={"schedule"}) == old.model_dump(exclude={"schedule"}):
            day.schedule = old.schedule.model_copy(deep=True) if old.schedule else None
        else:
            changed.append(day)
    if changed:
        partial = plan.model_copy(update={"days": changed})
        await validator(partial, request)
    plan.budget = None
    return plan
