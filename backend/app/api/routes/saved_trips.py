"""Persistent local trip endpoints. Browser owner token scopes every read/write."""
import asyncio
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo
from functools import lru_cache
from uuid import UUID
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from ...models.schemas import TripPlan, TripRequest, ChatTurn, ReferenceAnalysisRequest, TripCostContext, EntryWindow, TIME_PATTERN
from ...services.trip_repository import TripRepository, VersionConflict
from ...services.queue_admission import QueueCapacityError
from ...services.request_gate import protect_request
from ...services.reference_service import analyze_references
from ...services.schedule_validator import validate_schedule
from ...services.plan_patch import validate_changed_days
from ...services.ticket_bookings import BookingInput, BookingConflict
from ...agents.travel_agent import get_travel_agent, check_reference_constraints
from ...agents.langgraph_planner import get_langgraph_planner

router = APIRouter(prefix="/trips", tags=["保存的旅行"], dependencies=[Depends(protect_request)])


def owner_token(x_trip_owner: str = Header(...)):
    try:
        return str(UUID(x_trip_owner))
    except ValueError:
        raise HTTPException(422, "浏览器存储标识无效")


@lru_cache(maxsize=1)
def repository():
    return TripRepository()


class CreateTrip(BaseModel):
    request: TripRequest
    plan: TripPlan
    chat: list[ChatTurn] = Field(default_factory=list, max_length=20)


class CommitTrip(BaseModel):
    plan: TripPlan
    base_version: int = Field(..., ge=1)
    origin: Literal["manual", "diy", "route", "fix"] = "manual"


class SavedChat(BaseModel):
    message: str = Field(..., min_length=1, max_length=1500)
    base_version: int = Field(..., ge=1)


class ValidateTrip(BaseModel):
    base_version: int = Field(..., ge=1)


class PreviewCost(ValidateTrip):
    context: TripCostContext


from ...services.hotel_quotes import HotelQuoteInput


class UpdateHotelQuotes(ValidateTrip):
    quotes: list[HotelQuoteInput] = Field(min_length=1,max_length=30)


class RestoreTrip(BaseModel):
    base_version: int = Field(..., ge=1)
    target_version: int = Field(..., ge=1)


class TicketQuery(BaseModel):
    base_version: int = Field(ge=1)
    day_date: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    item_id: str = Field(min_length=1, max_length=200)
    visit_date: date


class EntryTiming(ValidateTrip):
    day_date: date
    item_id: str = Field(min_length=1, max_length=200)
    fixed_start_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    entry_window: EntryWindow | None = None
    choice: Literal['keep', 'earlier'] = 'keep'


class SaveBooking(BookingInput, ValidateTrip):
    pass


class CancelBooking(ValidateTrip):
    booking_id: str = Field(min_length=1, max_length=80)
    refunded_total: float = Field(ge=0, le=1000000, allow_inf_nan=False, strict=True)
    clear_time: bool = Field(default=False, strict=True)


class EntryOptions(EntryTiming):
    allow_cross_day: bool = Field(default=False, strict=True)


class ApplyEntryOption(EntryOptions):
    selection_id: Literal['reorder-1', 'reorder-2', 'move-flex']


@router.post('/{trip_id}/entry-options', summary='有界查路线，预览同日排序与可选跨日调整，不保存')
async def entry_options(trip_id: str, body: EntryOptions, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.entry_options import checked_options
    current, plan, seed, _ = await run_in_threadpool(_entry_candidate, repo, owner, trip_id, body)
    rows = await checked_options(plan, TripRequest(**current['request']), seed, body.item_id, body.allow_cross_day)
    return {'version':body.base_version, 'item_id':body.item_id, 'day_date':body.day_date.isoformat(),
            'options':[{key:value for key,value in row.items() if key != 'plan'} for row in rows]}


@router.post('/{trip_id}/entry-option', summary='重新检查选定预约调整并保存，不采信客户端时间线')
async def apply_entry_option(trip_id: str, body: ApplyEntryOption, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.entry_options import checked_options
    current, plan, seed, _ = await run_in_threadpool(_entry_candidate, repo, owner, trip_id, body)
    rows = await checked_options(plan, TripRequest(**current['request']), seed, body.item_id, body.allow_cross_day, body.selection_id)
    if not rows or rows[0]['status'] != 'feasible':
        raise HTTPException(422, '选定方案暂不能确认可行，请重新查询；原行程未改动')
    return await run_in_threadpool(invoke, lambda: repo.commit(owner, trip_id, body.base_version, rows[0]['plan'].model_dump(), 'appointment'))


def _booking_plan(repo, owner, trip_id, body):
    current = invoke(lambda: repo.read(owner, trip_id))
    if current['version'] != body.base_version:
        raise HTTPException(409, '行程已更新，请重新打开预订记录')
    return current, TripPlan(**current['plan'])


@router.post('/{trip_id}/ticket-booking-preview', summary='预览用户实付金额替换对应参考费，不写入')
def booking_preview(trip_id: str, body: SaveBooking, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.ticket_bookings import record_booking
    from ...services.cost_breakdown import build_cost_report
    current, plan = _booking_plan(repo, owner, trip_id, body)
    revised = invoke(lambda: record_booking(plan, body))
    request = TripRequest(**current['request'])
    before, after = build_cost_report(plan, request), build_cost_report(revised, request)
    return {'version': body.base_version, 'item_id': body.item_id, 'day_date': body.day_date.isoformat(),
            'before_total': before['known_total'], 'after_total': after['known_total'],
            'cost_report': after}


@router.post('/{trip_id}/ticket-booking', summary='保存用户确认的门票记录，不代下单')
def save_booking(trip_id: str, body: SaveBooking, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.ticket_bookings import record_booking
    _, plan = _booking_plan(repo, owner, trip_id, body)
    revised = invoke(lambda: record_booking(plan, body))
    return invoke(lambda: repo.commit(owner, trip_id, body.base_version, revised.model_dump(), 'ticket_booking'))


@router.post('/{trip_id}/ticket-booking-cancel', summary='记录取消与实际已退金额，不发起平台退款')
def cancel_ticket_booking(trip_id: str, body: CancelBooking, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.ticket_bookings import cancel_booking
    _, plan = _booking_plan(repo, owner, trip_id, body)
    revised = invoke(lambda: cancel_booking(plan, body.booking_id, body.refunded_total, body.clear_time))
    return invoke(lambda: repo.commit(owner, trip_id, body.base_version, revised.model_dump(), 'ticket_booking'))


def _entry_candidate(repo, owner, trip_id, body):
    from ...services.entry_planning import entry_preview
    if body.fixed_start_time and body.entry_window:
        raise HTTPException(422, '请选择固定时间或入场时段之一')
    current = invoke(lambda: repo.read(owner, trip_id))
    if current['version'] != body.base_version:
        raise HTTPException(409, '行程已更新，请重新预览入场时间')
    plan, original = TripPlan(**current['plan']), TripRequest(**current['request'])
    day, earlier = invoke(lambda: entry_preview(plan, original, body.day_date.isoformat(), body.item_id,
                                              body.fixed_start_time, body.entry_window))
    return current, plan, day, earlier


@router.post('/{trip_id}/entry-preview', summary='预览入场时间与已保存路线的冲突，不调用外部服务')
def preview_entry(trip_id: str, body: EntryTiming, owner=Depends(owner_token), repo=Depends(repository)):
    _, _, day, earlier = _entry_candidate(repo, owner, trip_id, body)
    return {'version': body.base_version, 'item_id': body.item_id, 'day': day.model_dump(),
            'earlier_day': earlier.model_dump() if earlier else None}


@router.post('/{trip_id}/entry-timing', summary='保存用户录入的入场时间及选定调整，生成可恢复版本')
def save_entry(trip_id: str, body: EntryTiming, owner=Depends(owner_token), repo=Depends(repository)):
    _, plan, day, earlier = _entry_candidate(repo, owner, trip_id, body)
    if body.choice == 'earlier' and earlier is None:
        raise HTTPException(422, '提前出发方案已不可用，请重新预览')
    selected = earlier if body.choice == 'earlier' else day
    plan.days = [selected if d.date == selected.date else d for d in plan.days]
    plan.budget = None
    return invoke(lambda: repo.commit(owner, trip_id, body.base_version, plan.model_dump(), 'appointment'))


def invoke(operation):
    try:
        return operation()
    except KeyError:
        raise HTTPException(404, "保存的行程或版本不存在")
    except VersionConflict as exc:
        raise HTTPException(409, str(exc))
    except BookingConflict as exc:
        raise HTTPException(422, str(exc))


class StartCreateRun(BaseModel):
    request: TripRequest
    request_id: UUID


@router.post("/plan-runs", status_code=202, summary="后台生成新行程，完成后自动保存")
async def start_create_run(body: StartCreateRun, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.planning_runs import enqueue_create_run as start
    unresolved = [c.name for c in body.request.reference_candidates
                  if c.priority == "required" and (c.status != "matched" or not c.place)]
    if unresolved:
        raise HTTPException(422, "请先确认必去地点的地图匹配：" + "、".join(unresolved))
    try:
        return await start(repo, owner, body.request.model_dump(mode="json"), str(body.request_id))
    except QueueCapacityError as exc:
        raise HTTPException(429, str(exc), headers={'Retry-After':str(exc.retry_after)})
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _owned_run(repo, owner, run_id, kind=None):
    from ...services.planning_runs import read_run
    result = invoke(lambda: read_run(repo, owner, run_id))
    if kind and result["kind"] != kind:
        raise HTTPException(404, "任务不存在")
    return result


@router.get("/plan-runs/{run_id}")
def get_create_run(run_id: str, owner=Depends(owner_token), repo=Depends(repository)):
    return _owned_run(repo, owner, run_id, "create")


@router.post("/plan-runs/{run_id}/cancel")
def cancel_create_run(run_id: str, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.planning_runs import cancel_run
    _owned_run(repo, owner, run_id, "create")
    return cancel_run(repo, owner, run_id)


@router.get("/runs/{run_id}/events", summary="以 SSE 推送任务阶段与结果")
async def run_events(run_id: str, request: Request, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.planning_runs import read_run, subscribe, unsubscribe, TERMINAL
    await run_in_threadpool(_owned_run, repo, owner, run_id)

    async def stream():
        event = subscribe(run_id)
        last = None
        try:
            while True:
                event.clear()
                run = await run_in_threadpool(read_run, repo, owner, run_id)
                snapshot = (run["status"], run["stage"], run["queries"])
                if snapshot != last:
                    last = snapshot
                    yield f"event: run\ndata: {json.dumps(run, ensure_ascii=False)}\n\n"
                if run["status"] in TERMINAL or await request.is_disconnected():
                    break
                try:
                    # Signals arrive from the in-process worker; the timeout doubles as a heartbeat.
                    await asyncio.wait_for(event.wait(), timeout=10)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            unsubscribe(run_id, event)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/references/analyze")
async def references(request: ReferenceAnalysisRequest):
    try:
        candidates = await analyze_references(request.city, request.sources, get_langgraph_planner().fast_llm)
        return {"candidates": candidates}
    except Exception:
        raise HTTPException(502, "攻略地点解析暂时失败，请重试；原文仍保留")


@router.get("")
def list_trips(owner=Depends(owner_token), repo=Depends(repository)):
    return repo.list(owner)


@router.post("")
def create_trip(request: CreateTrip, owner=Depends(owner_token), repo=Depends(repository)):
    request.plan.ticket_bookings = []
    for day in request.plan.days:
        if day.schedule:
            for segment in day.schedule.segments:
                segment.from_endpoint=None
                segment.to_endpoint=None
        for place in [*day.attractions, *day.meals, *([day.hotel] if day.hotel else [])]:
            place.parent_poi_id = None
            place.parent_evidence = None
    for forecast in request.plan.weather_info:
        forecast.source='user'
        forecast.observed_at=None
        forecast.report_time=None
    if (request.plan.city, request.plan.start_date, request.plan.end_date) != (request.request.city, request.request.start_date, request.request.end_date):
        raise HTTPException(422, "行程与原始需求不一致")
    return repo.create(owner, request.request.model_dump(), request.plan.model_dump(), [t.model_dump() for t in request.chat])


@router.get("/{trip_id}")
def read_trip(trip_id: str, owner=Depends(owner_token), repo=Depends(repository)):
    result = invoke(lambda: repo.read(owner, trip_id))
    result["plan"] = TripPlan(**result["plan"]).model_dump()
    return result


@router.post('/{trip_id}/ticket-query', summary='按需查询美团门票与跳转链接，不下单')
async def ticket_query(trip_id: str, body: TicketQuery, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.meituan_travel import query_travel, MeituanError
    current = await run_in_threadpool(invoke, lambda: repo.read(owner, trip_id))
    if current['version'] != body.base_version:
        raise HTTPException(409, '行程已更新，请重新打开景点后查询')
    if body.visit_date < datetime.now(ZoneInfo('Asia/Shanghai')).date():
        raise HTTPException(422, '请选择今天或之后的购票日期')
    # Same identity normalization as read_trip, including older saved plans.
    plan = TripPlan(**current['plan']).model_dump()
    day = next((day for day in plan['days'] if day['date'] == body.day_date), None)
    place = next((place for place in (day or {}).get('attractions', []) if place.get('item_id') == body.item_id), None)
    if not place:
        raise HTTPException(404, '该景点已不在当前行程中，请重新打开后查询')
    city, name = current['plan']['city'], place['name']
    query = (f'查询{body.visit_date.isoformat()}参观{city}{name}的门票商品与价格，供用户先比较再跳转预订。'
             '优先逐项提供对应景点的具体商品：商品原名、原始价格及计价单位、数量条件、包含与不包含内容、'
             '适用人群和可打开的商品购票链接。每个商品使用独立的Markdown链接，金额与条件保持供给原文。'
             '区分普通门票、联票和讲解套餐，不以讲解服务冒充门票。'
             '若只能提供景点入口或政策票价，请明确标为参考，不冒充实时商品报价；'
             '无法核实的价格或余票请说明，不估算、不补造，不用其他景点替代。仅查询，不下单，不支付。')
    try:
        result = await query_travel(city, query)
    except MeituanError as error:
        raise HTTPException(error.status, str(error), headers={'Retry-After': '60'} if error.status == 429 else None) from None
    # Do not attach results to plan facts, prices, bookings or saved versions.
    return {**result, 'version': current['version'], 'item_id': body.item_id,
            'name': name, 'visit_date': body.visit_date.isoformat()}


@router.get('/{trip_id}/admission-guide', summary='读取已核实的官方预约摘要，不查询余票')
async def read_admission_guide(trip_id: str, version: int = Query(ge=1),
                               day_date: date = Query(), visit_date: date = Query(),
                               item_id: str = Query(min_length=1, max_length=200),
                               owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.admission_guides import admission_guide
    current = await run_in_threadpool(invoke, lambda: repo.read(owner, trip_id))
    if current['version'] != version:
        raise HTTPException(409, '行程已更新，请重新打开景点后查看须知')
    plan = TripPlan(**current['plan'])
    day = next((day for day in plan.days if day.date == day_date.isoformat()), None)
    place = next((place for place in day.attractions if place.item_id == item_id), None) if day else None
    if not place:
        raise HTTPException(404, '该景点已不在当前行程中，请重新打开后查看')
    return {'version': version, 'item_id': item_id, 'visit_date': visit_date.isoformat(),
            'guide': admission_guide(plan.city, place, visit_date)}


@router.post("/{trip_id}/versions")
async def commit_trip(trip_id: str, request: CommitTrip, owner=Depends(owner_token), repo=Depends(repository)):
    current = await run_in_threadpool(invoke, lambda: repo.read(owner, trip_id))
    if current["version"] != request.base_version:
        raise HTTPException(409, "行程已更新，请重新打开后再保存")
    old = current["plan"]
    if (request.plan.city, request.plan.start_date, request.plan.end_date) != (old["city"], old["start_date"], old["end_date"]):
        raise HTTPException(422, "更换城市或日期请创建新的旅行")
    try:
        check_reference_constraints(request.plan, TripRequest(**current["request"]))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    try:
        from ...services.fact_evidence import reconcile_manual_facts
        reconcile_manual_facts(request.plan, TripPlan(**current['plan']))
        await validate_changed_days(request.plan, current["plan"], TripRequest(**current["request"]), validate_schedule)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return await run_in_threadpool(invoke, lambda: repo.commit(owner, trip_id, request.base_version, request.plan.model_dump(), request.origin))


@router.get("/{trip_id}/versions")
def versions(trip_id: str, owner=Depends(owner_token), repo=Depends(repository)):
    return invoke(lambda: repo.versions(owner, trip_id))


@router.post("/{trip_id}/restore")
def restore(trip_id: str, request: RestoreTrip, owner=Depends(owner_token), repo=Depends(repository)):
    return invoke(lambda: repo.restore(owner, trip_id, request.base_version, request.target_version))


@router.post("/{trip_id}/chat")
async def chat(trip_id: str, request: SavedChat, owner=Depends(owner_token), repo=Depends(repository)):
    current = await run_in_threadpool(invoke, lambda: repo.read(owner, trip_id))
    if current["version"] != request.base_version:
        raise HTTPException(409, "行程已在其他页面更新，请重新打开后再试")
    try:
        plan, reply, changed, actions = await get_travel_agent().run(
            TripRequest(**current["request"]), TripPlan(**current["plan"]), request.message, current["chat"])
    except Exception:
        raise HTTPException(502, "本次回复未完成，原行程保留。请检查必去地点或重试")
    history = [*current["chat"], {"role": "user", "content": request.message},
               {"role": "assistant", "content": reply, "actions": actions, "changed": changed}]
    result = await run_in_threadpool(invoke, lambda: repo.commit(owner, trip_id, request.base_version, plan.model_dump(), "agent", history))
    result.update(reply=reply, changed=changed, actions=actions)
    return result


@router.post("/{trip_id}/validate")
async def revalidate(trip_id: str, request: ValidateTrip, owner=Depends(owner_token), repo=Depends(repository)):
    current = await run_in_threadpool(invoke, lambda: repo.read(owner, trip_id))
    if current["version"] != request.base_version:
        raise HTTPException(409, "行程已更新，请重新打开后再校验")
    plan = await validate_schedule(TripPlan(**current["plan"]), TripRequest(**current["request"]))
    return await run_in_threadpool(invoke, lambda: repo.commit(owner, trip_id, request.base_version, plan.model_dump(), "validation"))


class StartRun(BaseModel):
    base_version: int = Field(..., ge=1)
    kind: Literal["optimize", "chat"]
    date: str = ""
    dates: list[str] = Field(default_factory=list, max_length=14)
    message: str = Field(default="", max_length=1500)
    request_id: UUID


@router.post("/{trip_id}/runs", status_code=202)
async def start_planning_run(trip_id: str, request: StartRun, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.planning_runs import enqueue_run
    if request.kind == "chat" and not request.message.strip():
        raise HTTPException(422,"请输入调整需求")
    body=request.model_dump(mode="json")
    try: return await enqueue_run(repo,owner,trip_id,body)
    except QueueCapacityError as exc: raise HTTPException(429,str(exc),headers={'Retry-After':str(exc.retry_after)})
    except ValueError as exc: raise HTTPException(422,str(exc))
    except KeyError: raise HTTPException(404,"行程不存在")
    except VersionConflict as exc: raise HTTPException(409,str(exc))


@router.get("/{trip_id}/runs")
def list_planning_runs(trip_id: str, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.planning_runs import read_run
    invoke(lambda:repo.read(owner,trip_id))
    with repo.connect() as db:
        rows=db.execute("SELECT id FROM planning_runs WHERE owner=? AND trip_id=? ORDER BY created_at DESC LIMIT 5",(owner,trip_id)).fetchall()
    return [read_run(repo,owner,row["id"]) for row in rows]


@router.get("/{trip_id}/runs/{run_id}")
def get_planning_run(trip_id: str, run_id: str, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.planning_runs import read_run
    result=invoke(lambda:read_run(repo,owner,run_id))
    if result["trip_id"]!=trip_id: raise HTTPException(404,"任务不存在")
    return result


@router.post("/{trip_id}/runs/{run_id}/cancel")
def cancel_planning_run(trip_id: str, run_id: str, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.planning_runs import cancel_run,read_run
    result=invoke(lambda:read_run(repo,owner,run_id))
    if result["trip_id"]!=trip_id: raise HTTPException(404,"任务不存在")
    return cancel_run(repo,owner,run_id)


@router.get("/{trip_id}/weather", summary="按短期预报给出雨天室内替换建议，不修改行程")
async def weather_check(trip_id: str, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.weather_advice import weather_advice
    current = await run_in_threadpool(invoke, lambda: repo.read(owner, trip_id))
    return await weather_advice(TripPlan(**current["plan"]))


@router.get("/{trip_id}/checks", summary="规则校验：重复、缺餐、住宿、坐标离群、预算")
async def plan_checks(trip_id: str, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.plan_checks import run_checks
    from ...services.cost_breakdown import build_cost_report
    from ...services.fact_evidence import fact_report
    from ...services.schedule_diagnostics import diagnose_day
    from ...services.visitor_access import visit_reminders
    current = await run_in_threadpool(invoke, lambda: repo.read(owner, trip_id))
    plan, request = TripPlan(**current["plan"]), TripRequest(**current["request"])
    return {"version": current["version"], "issues": run_checks(plan, request),
            "cost_report": build_cost_report(plan, request),
            "fact_report": fact_report(plan),
            "visit_reminders": visit_reminders(plan),
            "days": [diagnose_day(day, request) for day in plan.days]}


@router.post('/{trip_id}/cost-preview', summary='按人数和房间数测算参考费用，不修改行程')
def cost_preview(trip_id: str, body: PreviewCost, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.cost_breakdown import build_cost_report
    current = invoke(lambda: repo.read(owner, trip_id))
    if current['version'] != body.base_version:
        raise HTTPException(409, '行程已更新，请重新打开后再测算')
    return {'version': current['version'], 'cost_report': build_cost_report(
        TripPlan(**current['plan']), TripRequest(**current['request']), body.context)}


@router.post('/{trip_id}/hotel-quotes',summary='保存指定日期与房型的用户报价，不代表供应商可订报价')
def save_hotel_quotes(trip_id: str,body: UpdateHotelQuotes,owner=Depends(owner_token),repo=Depends(repository)):
    from ...services.hotel_quotes import apply_hotel_quotes
    current=invoke(lambda:repo.read(owner,trip_id))
    if current['version']!=body.base_version:raise HTTPException(409,'行程已更新，请重新打开后保存报价')
    try:plan=apply_hotel_quotes(TripPlan(**current['plan']),body.quotes)
    except ValueError as exc:raise HTTPException(422,str(exc))
    return invoke(lambda:repo.commit(owner,trip_id,body.base_version,plan.model_dump(),'hotel_quote'))


@router.get("/{trip_id}/prep", summary="出行准备：预约提醒、住宿汇总、打包清单")
async def trip_prep(trip_id: str, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.trip_prep import build_prep, saved_forecasts
    current = await run_in_threadpool(invoke, lambda: repo.read(owner, trip_id))
    plan = TripPlan(**current["plan"])
    result = build_prep(plan, saved_forecasts(plan))
    result['weather_note'] += ' 准备清单只读取行程内仍有效的天气记录；刷新预报请使用“天气与雨天备选”。'
    return {"version": current["version"], **result}


@router.post("/{trip_id}/enrich", summary="补充地图参考、支持场馆的官网门票与估算（保存为新版本）")
async def enrich_trip(trip_id: str, request: ValidateTrip, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.place_facts import enrich_plan
    from ...services.amap_rest import place_detail
    current = await run_in_threadpool(invoke, lambda: repo.read(owner, trip_id))
    if current["version"] != request.base_version:
        raise HTTPException(409, "行程已更新，请重新打开后再试")
    plan = TripPlan(**current["plan"])
    def hours(day):
        return [p.open_hours for p in [*day.attractions, *day.meals]]
    before_hours = {day.date: hours(day) for day in plan.days}
    summary = await enrich_plan(plan, get_langgraph_planner().fast_llm, place_detail)
    changed = [day for day in plan.days if hours(day) != before_hours[day.date]]
    if changed:
        await validate_schedule(plan.model_copy(update={"days": changed}), TripRequest(**current["request"]))
    result = await run_in_threadpool(invoke, lambda: repo.commit(owner, trip_id, request.base_version, plan.model_dump(), "enrich"))
    result["enrichment"] = summary
    return result


@router.post("/{trip_id}/days/{date}/fixes", summary="为时间冲突的一天生成已校验的调整方案（不修改行程）")
async def day_fixes(trip_id: str, date: str, request: ValidateTrip, owner=Depends(owner_token), repo=Depends(repository)):
    from ...services.conflict_fix import propose_fixes
    current = await run_in_threadpool(invoke, lambda: repo.read(owner, trip_id))
    if current["version"] != request.base_version:
        raise HTTPException(409, "行程已更新，请重新打开后再试")
    plan = TripPlan(**current["plan"])
    index = next((i for i, d in enumerate(plan.days) if d.date == date), None)
    if index is None:
        raise HTTPException(404, "指定日期不在行程中")
    return {"date": date, "proposals": await propose_fixes(plan, TripRequest(**current["request"]), index)}
