"""Persistent planning jobs with shared leases and atomic fenced completion."""
import asyncio
import json
import time
import logging
import sqlite3
from contextlib import closing
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4
from ..models.schemas import TripPlan, TripRequest
from .trip_repository import TripRepository, VersionConflict
from .run_leases import RunLeaseLost, claim, renew, release, owned_update, require_lease
from .queue_admission import require_capacity
from ..config import get_settings

_tasks = {}
_recovery_tasks = {}
_recovery_wakeups = {}
_checkpoint_namespace = ContextVar('planning_checkpoint_namespace', default=None)
logger = logging.getLogger(__name__)
_listeners: dict[str, dict] = {}
TERMINAL = {"succeeded", "failed", "conflict", "cancelled"}


def subscribe(run_id):
    """In-process change signal for SSE listeners; each listener owns one Event."""
    event = asyncio.Event()
    _listeners.setdefault(run_id, {})[event] = asyncio.get_running_loop()
    return event


def unsubscribe(run_id, event):
    listeners = _listeners.get(run_id)
    if listeners:
        listeners.pop(event, None)
        if not listeners:
            _listeners.pop(run_id, None)


def notify(run_id):
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None
    for event, loop in tuple(_listeners.get(run_id, {}).items()):
        if current_loop is loop:
            event.set()
        elif not loop.is_closed():
            loop.call_soon_threadsafe(event.set)


def now(): return datetime.now(timezone.utc).isoformat()


async def database_call(operation, *args, **kwargs):
    """Finish a SQLite operation before acknowledging task cancellation."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await finish_database_task(task)
        raise


async def finish_database_task(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


def read_run(repo, owner, run_id):
    with repo.connect() as db:
        row = db.execute("SELECT * FROM planning_runs WHERE id=? AND owner=?",(run_id,owner)).fetchone()
    if not row: raise KeyError(run_id)
    result=dict(row)
    result.pop("owner"); result.pop("payload")
    for field in ('lease_token', 'lease_until', 'attempt_count'):
        result.pop(field, None)
    result["result"]=json.loads(result["result"]) if result["result"] else None
    ended=datetime.fromisoformat(result["updated_at"]) if result["status"] in TERMINAL else datetime.now(timezone.utc)
    result["elapsed_seconds"]=max(0,round((ended-datetime.fromisoformat(result["created_at"])).total_seconds()))
    return result


def start_run(repo, owner, trip_id, body, *, schedule=True):
    current=repo.read(owner,trip_id)
    run_id=body.get("request_id") or str(uuid4())
    stamp=now()
    payload=json.dumps({"current":current,"body":body},ensure_ascii=False)
    with repo.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing=db.execute("SELECT * FROM planning_runs WHERE id=?",(run_id,)).fetchone()
        if existing:
            if existing["owner"]!=owner or existing["trip_id"]!=trip_id or json.loads(existing["payload"])["body"]!=body:
                raise ValueError("任务标识已用于其他请求")
        else:
            latest=db.execute("SELECT version FROM trips WHERE id=? AND owner=?",(trip_id,owner)).fetchone()
            if not latest or latest["version"]!=body["base_version"]: raise VersionConflict("行程已更新，请重新打开后再试")
            active=db.execute("SELECT id,payload FROM planning_runs WHERE owner=? AND trip_id=? AND status IN ('queued','running')",(owner,trip_id)).fetchone()
            if active:
                previous=json.loads(active["payload"])["body"]
                if {k:v for k,v in previous.items() if k!="request_id"} == {k:v for k,v in body.items() if k!="request_id"}:
                    return read_run(repo,owner,active["id"])
                raise VersionConflict("另一项任务正在执行，请等待完成或取消后再试")
            require_capacity(db, owner, get_settings())
            db.execute("INSERT INTO planning_runs(id,owner,trip_id,base_version,kind,status,stage,payload,created_at,updated_at) VALUES (?,?,?,?,?,'queued','准备任务',?,?,?)",
                       (run_id,owner,trip_id,body["base_version"],body["kind"],payload,stamp,stamp))
    if schedule: launch(repo,owner,run_id)
    return read_run(repo,owner,run_id)


def start_create_run(repo, owner, request, request_id, *, schedule=True):
    """Queue a new-trip generation; on success the trip is created in the same transaction."""
    run_id=request_id
    body={"kind":"create","request_id":request_id,"request":request}
    stamp=now()
    with repo.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        existing=db.execute("SELECT owner,payload FROM planning_runs WHERE id=?",(run_id,)).fetchone()
        if existing:
            if existing["owner"]!=owner or json.loads(existing["payload"])["body"]!=body:
                raise ValueError("任务标识已用于其他请求")
        else:
            require_capacity(db, owner, get_settings())
            db.execute("INSERT INTO planning_runs(id,owner,trip_id,base_version,kind,status,stage,payload,created_at,updated_at) VALUES (?,?,'',0,'create','queued','准备任务',?,?,?)",
                       (run_id,owner,json.dumps({"body":body},ensure_ascii=False),stamp,stamp))
    if schedule: launch(repo,owner,run_id)
    return read_run(repo,owner,run_id)


async def enqueue_create_run(repo, owner, request, request_id):
    result = await database_call(start_create_run, repo, owner, request, request_id, schedule=False)
    if result['status'] not in TERMINAL: launch(repo, owner, result['id'])
    return result


async def enqueue_run(repo, owner, trip_id, body):
    result = await database_call(start_run, repo, owner, trip_id, body, schedule=False)
    if result['status'] not in TERMINAL: launch(repo, owner, result['id'])
    return result


async def _generate(request, run_id, progress):
    import os
    engine=os.getenv("PLANNER_ENGINE","agent")
    if engine=="agent":
        from ..agents.travel_agent import get_travel_agent
        plan,_,_,_=await get_travel_agent().run(request,run_id=_checkpoint_namespace.get() or run_id,progress=progress)
        return plan
    if engine=="langgraph":
        from ..agents.langgraph_planner import get_langgraph_planner
        plan=await get_langgraph_planner().plan_trip_async(request)
    else:
        from ..agents.trip_planner_agent import get_trip_planner_agent
        plan=await asyncio.to_thread(get_trip_planner_agent().plan_trip,request)
    from .schedule_validator import validate_schedule
    await validate_schedule(plan,request,progress=progress)
    return plan


async def _estimate_tickets(plan,progress):
    from ..agents.langgraph_planner import get_langgraph_planner
    from .place_facts import fill_ticket_prices
    try:
        await progress("查询门票参考与补充估算")
        await fill_ticket_prices(plan,get_langgraph_planner().fast_llm)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        print(f"⚠️ 门票估算失败：{exc}")


async def execute_create(repo,owner,run_id,payload,progress,lease_token):
    body=payload["body"]
    request=TripRequest.model_validate(body["request"])
    prepared = payload.get('prepared')
    if prepared:
        plan=TripPlan.model_validate(payload["prepared"]["plan"]); extra=payload["prepared"]["extra"]
    else:
        await progress("理解需求并核实地点")
        plan=await _generate(request,run_id,progress)
        from .visit_advice import normalize_generated_advice
        normalize_generated_advice(plan)
        await _estimate_tickets(plan,progress)
        extra={}
    from .plan_quality import evaluate_plan
    extra['quality'] = evaluate_plan(plan, request)
    if not prepared:
        payload["prepared"]={"plan":plan.model_dump(),"extra":extra}
        await database_call(owned_update, repo, owner, run_id, lease_token, {'payload':json.dumps(payload,ensure_ascii=False)})
    await progress("保存旅行")
    await database_call(repo.create,owner,request.model_dump(),plan.model_dump(),[],run_id,extra,lease_token=lease_token)


def combine_outcomes(outcomes):
    """One summary for a multi-day optimization; single-day results pass through unchanged."""
    if len(outcomes)==1: return outcomes[0]
    ok=all(o["status"]=="feasible_under_assumptions" for o in outcomes)
    failed=[o for o in outcomes if o["status"]!="feasible_under_assumptions"]
    note="已优化 "+"、".join(f"第{o['day_index']+1}天" for o in outcomes)+" 的路线"
    if failed:
        note+="；"+"；".join(f"第{o['day_index']+1}天：{o['note']}" for o in failed)
    return {"status":"feasible_under_assumptions" if ok else failed[0]["status"],"note":note,
            "queries":sum(o.get("queries",0) for o in outcomes),"days":outcomes}


def launch(repo,owner,run_id):
    key = (str(repo.path.resolve()), run_id)
    if key in _tasks: return
    task=asyncio.create_task(execute(repo,owner,run_id))
    _tasks[key]=task
    def finished(done):
        if _tasks.get(key) is done:
            _tasks.pop(key, None)
        if not done.cancelled():
            error = done.exception()
            if error:
                logger.warning('Planning executor stopped: %s; durable job remains recoverable', type(error).__name__)
    task.add_done_callback(finished)


async def execute(repo,owner,run_id):
    settings = get_settings()
    pending = asyncio.create_task(asyncio.to_thread(claim, repo, owner, run_id, ttl=settings.planning_lease_seconds,
                                    capacity=settings.planning_max_running, max_attempts=settings.planning_max_attempts,
                                    queue_timeout=settings.planning_queue_timeout_seconds))
    try:
        token = await asyncio.shield(pending)
    except asyncio.CancelledError:
        # A cancelled to_thread call can still acquire its SQL lease. Collect
        # its result and release that exact token rather than orphaning it.
        token = await finish_database_task(pending)
        if token:
            await database_call(release, repo, owner, run_id, token)
        return
    if not token:
        notify(run_id)
        return
    work = asyncio.create_task(_execute_claimed(repo, owner, run_id, token))
    async def heartbeat():
        try:
            while not work.done():
                await asyncio.sleep(min(1, settings.planning_lease_seconds/3))
                if not await database_call(renew, repo, owner, run_id, token, settings.planning_lease_seconds):
                    work.cancel()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A worker that cannot renew must not keep writing as an owner.
            logger.warning('Planning lease renewal stopped: %s', type(exc).__name__)
            work.cancel()
    pulse = asyncio.create_task(heartbeat())
    try:
        await asyncio.wait_for(work, settings.planning_run_timeout_seconds)
    except asyncio.TimeoutError:
        try:
            await database_call(owned_update, repo, owner, run_id, token,
                {'status':'failed','stage':'未完成','error':'任务处理超时，未保存本次结果，请重新发起','updated_at':now()})
        except RunLeaseLost:
            pass
    except (asyncio.CancelledError, RunLeaseLost):
        pass
    finally:
        pulse.cancel()
        await asyncio.gather(pulse, return_exceptions=True)
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        await database_call(release, repo, owner, run_id, token)
        notify(run_id)
        wake = _recovery_wakeups.get(str(repo.path.resolve()))
        if wake: wake.set()


async def _execute_claimed(repo,owner,run_id,lease_token):
    with repo.connect() as db:
        row=db.execute("SELECT * FROM planning_runs WHERE id=? AND owner=?",(run_id,owner)).fetchone()
    require_lease(row, lease_token)
    payload=json.loads(row["payload"]); body=payload["body"]; current=payload.get("current")
    namespace_token = _checkpoint_namespace.set(f"{run_id}:attempt:{row['attempt_count']}")
    def write_progress(stage,queries):
        owned_update(repo, owner, run_id, lease_token, {'stage':stage, 'queries':queries, 'updated_at':now()})
    progress_lock = asyncio.Lock()
    async def progress(stage,queries=0,total=0):
        # Cached route results can complete together. Serialize writes so a slow
        # earlier SQLite write cannot overwrite a later progress update.
        async with progress_lock:
            await database_call(write_progress,stage,queries)
            notify(run_id)
    try:
        if body["kind"]=="create":
            await execute_create(repo,owner,run_id,payload,progress,lease_token)
            return
        await progress("读取当前行程")
        latest=repo.read(owner,current["id"])
        if latest["version"]!=body["base_version"]: raise VersionConflict("行程已更新，本任务保留原行程，请重新发起")
        plan=TripPlan.model_validate(current["plan"]); request=TripRequest.model_validate(current["request"])
        extra={}; history=None
        if payload.get("prepared"):
            plan=TripPlan.model_validate(payload["prepared"]["plan"])
            extra=payload["prepared"]["extra"]; history=payload["prepared"]["history"]
        elif body["kind"]=="optimize":
            from .schedule_solver import optimize_day
            dates=body.get("dates") or [body["date"]]
            outcomes=[]
            for position,target in enumerate(dates):
                index=next((i for i,d in enumerate(plan.days) if d.date==target),None)
                if index is None: raise ValueError("指定日期不在行程中")
                async def day_progress(stage,queries=0,total=0,label=f"第{index+1}天"):
                    await progress(f"{label}：{stage}" if len(dates)>1 else stage,queries,total)
                plan.days[index], outcome=await optimize_day(plan.days[index],request,plan.city,progress=day_progress)
                outcomes.append({**outcome,"date":target,"day_index":index})
            extra={"optimization":combine_outcomes(outcomes)}
        else:
            from ..agents.travel_agent import get_travel_agent
            await progress("理解需求并核实地点")
            plan,reply,changed,actions=await get_travel_agent().run(request,plan,body["message"],current["chat"],run_id=_checkpoint_namespace.get(),progress=progress)
            if changed: await _estimate_tickets(plan,progress)
            extra={"reply":reply,"changed":changed,"actions":actions}
            history=[*current["chat"],{"role":"user","content":body["message"]},{"role":"assistant","content":reply,"changed":changed,"actions":actions}]
        from .plan_quality import evaluate_plan
        extra["quality"]=evaluate_plan(plan,request)
        # A prepared checkpoint avoids repeating all work if interrupted before commit.
        payload["prepared"]={"plan":plan.model_dump(),"extra":extra,"history":history}
        await database_call(owned_update,repo,owner,run_id,lease_token,{'payload':json.dumps(payload,ensure_ascii=False)})
        await progress("保存调整结果", read_run(repo,owner,run_id)["queries"])
        await database_call(repo.commit,owner,current["id"],body["base_version"],plan.model_dump(),body["kind"],history,run_id=run_id,extra=extra,lease_token=lease_token)
    except asyncio.CancelledError:
        # Keep running/queued on process shutdown so startup can recover. Explicit
        # cancellation already marks the row cancelled before stopping the task.
        raise
    except RunLeaseLost:
        raise
    except Exception as exc:
        from .model_errors import model_service_error
        status="conflict" if isinstance(exc,VersionConflict) else "failed"
        fallback="规划未能完成，请稍后重试，或减少旅行天数和参考内容。" if body["kind"]=="create" else "任务未完成，原行程保留。请稍后重试。"
        mapped=model_service_error(exc)
        error=str(exc) if isinstance(exc,(VersionConflict,ValueError)) else mapped[1] if mapped else fallback
        owned_update(repo,owner,run_id,lease_token,{'status':status,'stage':'未完成','error':error[:500],'updated_at':now()})
    finally:
        _checkpoint_namespace.reset(namespace_token)
        notify(run_id)


def cancel_run(repo,owner,run_id):
    row=read_run(repo,owner,run_id)
    if row["status"] not in TERMINAL:
        with repo.connect() as db:
            db.execute("UPDATE planning_runs SET status='cancelled',stage='已取消',lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=? AND owner=? AND status NOT IN ('succeeded','failed','conflict')",(now(),run_id,owner))
        task = _tasks.get((str(repo.path.resolve()), run_id))
        if task and not task.get_loop().is_closed():
            # FastAPI's synchronous cancel endpoints run in a worker thread.
            # Cancel and signal Events on the task's own event loop.
            task.get_loop().call_soon_threadsafe(task.cancel)
        notify(run_id)
    return read_run(repo,owner,run_id)


def recovery_rows(repo):
    with repo.connect() as db:
        rows=db.execute("SELECT id,owner FROM planning_runs WHERE status IN ('queued','running') AND (lease_until IS NULL OR lease_until<=?) ORDER BY created_at LIMIT ?",
                        (time.time(),get_settings().planning_max_running*2)).fetchall()
    return rows


def recover_runs(repo=None):
    repo=repo or TripRepository()
    rows=recovery_rows(repo)
    for row in rows: launch(repo,row["owner"],row["id"])


def start_recovery(repo=None):
    repo = repo or TripRepository()
    key = str(repo.path.resolve())
    existing = _recovery_tasks.get(key)
    if existing and not existing.done():
        return existing
    wake = asyncio.Event()
    _recovery_wakeups[key] = wake
    async def loop():
        last_warning = float('-inf')
        while True:
            wake.clear()
            try:
                rows = await database_call(recovery_rows, repo)
                for row in rows: launch(repo,row['owner'],row['id'])
            except Exception as exc:
                # A temporary store failure is not proof that a job has failed.
                if time.monotonic()-last_warning >= 30:
                    logger.warning('Planning recovery scan unavailable: %s', type(exc).__name__)
                    last_warning = time.monotonic()
            try:
                await asyncio.wait_for(wake.wait(),timeout=2)
            except asyncio.TimeoutError:
                pass
    task = asyncio.create_task(loop())
    _recovery_tasks[key] = task
    return task


async def readiness(repo=None):
    """Local readiness only; never spend model/map quota on a health probe."""
    worker = False
    try:
        repo = repo or TripRepository()
        task = _recovery_tasks.get(str(repo.path.resolve()))
        worker = bool(task and not task.done())
        def probe():
            uri = repo.path.resolve().as_uri()+'?mode=rw'
            with closing(sqlite3.connect(uri, uri=True, timeout=.2)) as db:
                db.execute('SELECT id,lease_token,lease_until,attempt_count FROM planning_runs LIMIT 0')
        await asyncio.to_thread(probe)
        storage = True
    except Exception:
        storage = False
    return {'status':'ready' if worker and storage else 'not_ready', 'planning_worker':worker,
            'storage':'available' if storage else 'unavailable', 'upstreams':'not_probed'}


async def shutdown_runs():
    recovery = list(_recovery_tasks.values())
    for task in recovery: task.cancel()
    await asyncio.gather(*recovery,return_exceptions=True)
    _recovery_tasks.clear()
    _recovery_wakeups.clear()
    tasks=list(_tasks.values())
    for task in tasks: task.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
