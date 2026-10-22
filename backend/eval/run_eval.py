"""Score saved plans read-only, or evaluate the complete persistent planning job.

python -X utf8 -m eval.run_eval score
python -X utf8 -m eval.run_eval run --cases cs-walk-1d,hz-slow-elder-2d --repeat 2
Online mode uses configured model/map services; saves only to a temporary database.
"""
import argparse
import asyncio
import json
import math
import sqlite3
import sys
import tempfile
import time
from statistics import median
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.models.schemas import TripPlan, TripRequest
from app.services.plan_quality import evaluate_plan
from app.services.plan_checks import known_cost
from app.services.cost_breakdown import build_cost_report
RESULTS = Path(__file__).resolve().parent / "results"
EXPECT_KEYS = {"max_attractions_per_day", "exclude_keywords", "include_keywords", "include_all_keywords", "budget", "activity_after", "allowed_route_modes"}


def constraint_results(plan, expect, request=None):
    """True = supported; False = contradicted; None = insufficient evidence."""
    unsupported = set(expect) - EXPECT_KEYS
    if unsupported:
        raise ValueError(f"Unsupported expectations: {sorted(unsupported)}")
    texts = [f"{a.name}{a.category or ''}" for d in plan.days for a in d.attractions]
    results = {}
    if "max_attractions_per_day" in expect:
        results["max_attractions_per_day"] = bool(plan.days) and all(len(d.attractions) <= expect["max_attractions_per_day"] for d in plan.days)
    if "exclude_keywords" in expect:
        results["exclude_keywords"] = bool(texts) and not any(word in text for word in expect["exclude_keywords"] for text in texts)
    if "include_keywords" in expect:
        results["include_keywords"] = any(word in text for word in expect["include_keywords"] for text in texts)
    if "include_all_keywords" in expect:
        results["include_all_keywords"] = all(any(word in text for text in texts) for word in expect["include_all_keywords"])
    if "budget" in expect:
        # Listed item costs are not a complete trip total (party size, all fares,
        # arrival/departure and the exact lodging nights are not fully modeled).
        # Zero unknown prices must never prove the trip is within budget.
        cost = build_cost_report(plan, request)
        results["budget"] = False if not cost['assumptions'] and cost['known_total'] > expect["budget"] else None
    if 'allowed_route_modes' in expect:
        segments = [s for day in plan.days if day.schedule for s in day.schedule.segments]
        complete = bool(plan.days) and all(day.schedule for day in plan.days) and all(s.status == 'verified' and s.duration_minutes is not None for s in segments)
        results['allowed_route_modes'] = all(s.mode in expect['allowed_route_modes'] for s in segments) if complete else None
    if 'activity_after' in expect:
        threshold = datetime.strptime(expect['activity_after'], '%H:%M').time()
        activities = [i for day in plan.days if day.schedule for i in day.schedule.items if i.kind == 'attraction']
        times = []
        for item in activities:
            try:
                start = datetime.strptime(item.start_time or '', '%H:%M').time()
                end = datetime.strptime(item.end_time or '', '%H:%M').time()
                times.append(end > threshold and end > start)
            except ValueError:
                times.append(None)
        results['activity_after'] = True if any(v is True for v in times) else False if times and all(v is not None for v in times) and all(day.schedule for day in plan.days) else None
    return results


def summarize(plan, request, expect=None):
    quality = evaluate_plan(plan, request)
    issues = quality["rule_issues"]
    places = [p for d in plan.days for p in [*d.attractions, *d.meals, *([d.hotel] if d.hotel else [])]]
    unknown_prices = sum(p.cost_status == "unknown" or getattr(p, "ticket_price", getattr(p, "estimated_cost", None)) is None for p in places)
    row = {
        "city": plan.city, "days": len(plan.days),
        "attractions": sum(len(d.attractions) for d in plan.days),
        "routes": f"{quality['verified_routes']}/{quality['total_routes']}",
        "route_coverage": round(quality["verified_routes"] / quality["total_routes"], 3) if quality["total_routes"] else None,
        "provider_id_coverage": round(quality["places_with_provider_id"] / quality["total_places"], 3) if quality["total_places"] else None,
        "conflict_days": len(quality["conflict_dates"]), "schedule_passed": quality["schedule_passed"],
        "rule_errors": quality["rule_errors"], "rule_warnings": sum(i["severity"] == "warning" for i in issues),
        "rule_infos": sum(i["severity"] == "info" for i in issues),
        "issues": [f"[{i['severity']}] {i['message']}" for i in issues],
        "dates_valid": quality["dates_valid"], "city_valid": quality["city_valid"],
        "days_with_activities": quality["days_with_activities"],
        "opening_hours_conflicts": quality["opening_hours_conflicts"],
        "nested_attraction_groups": quality["nested_attraction_groups"],
        "opening_hours_unconfirmed_days": quality["opening_hours_unconfirmed_days"],
        "official_hours_verified": quality["official_hours_verified"],
        "booking_availability_verified": quality["booking_availability_verified"],
        "stale_place_facts": quality['stale_place_facts'],
        "undated_place_facts": quality['undated_place_facts'],
        "missing_place_facts": quality['missing_place_facts'],
        "known_listed_cost": known_cost(plan, request), "unknown_prices": unknown_prices,
        "cost_report": build_cost_report(plan, request),
        "day_assessments": [{"date": d.date, "status": d.schedule.status if d.schedule else "unchecked",
                             "conflicts": d.schedule.conflicts if d.schedule else [],
                             "missing_routes": [{"from": s.from_name, "to": s.to_name, "reason": s.note} for s in d.schedule.segments if s.status != 'verified'] if d.schedule else []} for d in plan.days],
    }
    if expect is not None:
        checks = constraint_results(plan, expect, request)
        row["constraints"] = checks
        row["constraints_status"] = "not_requested" if not checks else "failed" if any(v is False for v in checks.values()) else "unverified" if any(v is None for v in checks.values()) else "passed"
        row["constraints_passed"] = all(v is True for v in checks.values())
    row["reference_checks_passed"] = quality["schedule_passed"] and not row['rule_warnings'] and bool(places) and quality["places_with_provider_id"] == len(places) and row.get("constraints_passed", True)
    row['criteria_scope'] = 'schema, dates, city, schedule integrity, required POIs, rule warnings, declared case constraints; not all free-text preferences'
    # These explicit evidence fields prevent a draft check becoming a production claim.
    row["execution_ready"] = row["reference_checks_passed"] and quality["official_hours_verified"] and quality["booking_availability_verified"]
    return row


def aggregate(rows):
    groups = {}
    for row in rows:
        key = row.get("id", "saved")
        group = groups.setdefault(key, {"samples": 0, "completed": 0, "reference_passed": 0, "timeouts": 0, "successful_seconds": []})
        group["samples"] += 1
        group["completed"] += row.get("status") == "completed"
        group["reference_passed"] += row.get("reference_checks_passed") is True
        group["timeouts"] += row.get("status") == "timeout"
        if row.get("status") == "completed" and isinstance(row.get("seconds"), (int, float)):
            group["successful_seconds"].append(row["seconds"])
    for group in groups.values():
        values = sorted(group.pop("successful_seconds"))
        group["latency_sample_size"] = len(values)
        group["median_seconds"] = round(median(values), 2) if values else None
        # Twenty repeats per case is a minimum reporting gate, not statistical confidence.
        group["p95_seconds"] = round(values[math.ceil(len(values)*.95)-1], 2) if len(values) >= 20 else None
        group["p95_note"] = "successful samples only" if len(values) >= 20 else "样本不足20，未报告生产p95"
    return groups


def write(mode, rows, header):
    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    (RESULTS / f"{mode}-{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (RESULTS / f"{mode}-{stamp}.summary.json").write_text(json.dumps(aggregate(rows), ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# {mode} {stamp}", "", "任务完成、参考检查通过和可直接执行为不同结论；价格/营业/预约缺失不视为通过。", "",
             "| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    safe = lambda value: str(value).replace("|", "/").replace("\n", " ")
    lines += ["| " + " | ".join(safe(row.get(key, "")) for key in header) + " |" for row in rows]
    lines += ["", "## 规则问题", ""] + [f"- **{row.get('id', row.get('city'))}**：{issue}" for row in rows for issue in row.get("issues", [])]
    path = RESULTS / f"{mode}-{stamp}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def score_saved(database=None):
    path = Path(database) if database else ROOT / "var" / "trips.sqlite3"
    rows = []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        records = db.execute("SELECT id,request,plan FROM trips ORDER BY updated_at DESC").fetchall()
    for trip_id, request, plan in records:
        try:
            row = summarize(TripPlan(**json.loads(plan)), TripRequest(**json.loads(request)))
        except Exception as exc:
            row = {"city": "?", "reference_checks_passed": False, "issues": [f"[error] 无法评分：{type(exc).__name__}"]}
        rows.append({"id": trip_id[:8], **row})
    header = ["id", "city", "days", "routes", "schedule_passed", "reference_checks_passed", "unknown_prices", "opening_hours_unconfirmed_days"]
    output = write("score", rows, header)
    print(f"已只读评分 {len(rows)} 份行程 → {output}")
    return rows


def load_cases(selected=None, limit=None):
    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("用例 ID 重复")
    if selected:
        missing = set(selected) - {c["id"] for c in cases}
        if missing:
            raise ValueError(f"找不到用例：{sorted(missing)}")
        cases = [c for c in cases if c["id"] in selected]
    if limit is not None:
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        cases = cases[:limit]
    if not cases:
        raise ValueError("没有可执行用例")
    for case in cases:
        unsupported = set(case.get("expect", {})) - EXPECT_KEYS
        if unsupported:
            raise ValueError(f"{case['id']} 包含未实现的约束：{sorted(unsupported)}")
    return cases


def case_request(case):
    start = date.fromisoformat(case['start_date']) if case.get('start_date') else date.today() + timedelta(days=21)
    end = start + timedelta(days=case["days"]-1)
    return TripRequest(city=case["city"], start_date=start.isoformat(), end_date=end.isoformat(), travel_days=case["days"],
                          transportation=case.get("transportation", "公共交通"), accommodation=case.get("accommodation", "舒适型酒店"),
                          preferences=case.get("preferences", []), free_text_input=case.get("free_text", ""),
                          **{key:case[key] for key in ('party_size','room_count','destination_budget','budget_mode') if key in case})


async def run_one(case, repeat, timeout, repo, owner):
    from app.services import planning_runs as jobs
    from app.services.observability import usage_scope
    request = case_request(case)
    run_id = str(uuid4())
    began, first_progress, stages = time.monotonic(), None, []
    with usage_scope() as usage:
        jobs.start_create_run(repo, owner, request.model_dump(), run_id)
        event = jobs.subscribe(run_id)
        task = jobs._tasks[(str(repo.path.resolve()), run_id)]
        try:
            async with asyncio.timeout(timeout):
                while True:
                    run = await asyncio.to_thread(jobs.read_run, repo, owner, run_id)
                    if not stages or run["stage"] != stages[-1]["stage"]:
                        elapsed = round(time.monotonic()-began, 2)
                        stages.append({"stage": run["stage"], "seconds": elapsed})
                        if run["status"] == "running" and first_progress is None:
                            first_progress = elapsed
                        print(f"{case['id']} #{repeat}: {run['stage']} ({elapsed}s)", flush=True)
                    if run["status"] in jobs.TERMINAL:
                        break
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), .5)
                    except asyncio.TimeoutError:
                        pass
            if run["status"] == "succeeded":
                saved = repo.read(owner, run["trip_id"])
                row = summarize(TripPlan(**saved["plan"]), request, case.get("expect", {}))
                row.update(status="completed", saved_version=saved["version"], plan_snapshot=saved["plan"])
            else:
                row = {"city": request.city, "status": run["status"], "reference_checks_passed": False, "issues": [run["error"] or "任务未完成"]}
        except TimeoutError:
            jobs.cancel_run(repo, owner, run_id)
            row = {"city": request.city, "status": "timeout", "reference_checks_passed": False, "issues": [f"完整任务超过 {timeout}s，已取消"]}
        except Exception as exc:
            row = {"city": request.city, "status": "failed", "reference_checks_passed": False, "issues": [f"完整任务评分失败：{type(exc).__name__}"]}
        finally:
            jobs.unsubscribe(run_id, event)
            if not task.done():
                jobs.cancel_run(repo, owner, run_id)
            await asyncio.gather(task, return_exceptions=True)
        row.update(id=case["id"], repeat=repeat, seconds=round(time.monotonic()-began, 2), first_progress_seconds=first_progress,
                   case_request=request.model_dump(), case_expect=case.get("expect", {}), timeout_seconds=timeout, cache_policy="reuse_existing_cache; cold/warm not classified",
                   stages=stages, model_calls=usage["calls"], prompt_tokens=usage["prompt"], completion_tokens=usage["completion"],
                   model_seconds=round(usage['seconds'],2), provider_events={k:round(v,3) for k,v in usage['events'].items()},
                   cache_hit_rate=round(usage["cached"]/usage["prompt"], 2) if usage["prompt"] else None,
                   pipeline="persistent_create_with_prices_and_save")
    return row


async def run_cases(selected=None, limit=None, repeat=1, timeout=180):
    from app.services.trip_repository import TripRepository
    from app.services.schedule_validator import close_route_queries
    from app.services.amap_rest import close_client
    if repeat < 1 or timeout <= 0:
        raise ValueError("repeat 和 timeout 必须大于 0")
    cases = load_cases(selected, limit)
    rows = []
    try:
        with tempfile.TemporaryDirectory() as folder:
            repo = TripRepository(Path(folder) / "eval.sqlite3")
            owner = str(uuid4())
            for n in range(1, repeat+1):
                for case in cases:
                    row = await run_one(case, n, timeout, repo, owner)
                    rows.append(row)
                    print(f"{row['id']} #{n}: {row['status']} {row['seconds']}s，参考检查 {row.get('reference_checks_passed', False)}", flush=True)
                    # Save incremental evidence even if a later case is interrupted.
                    write("run-partial", rows, ["id", "repeat", "status", "seconds", "schedule_passed", "constraints_status", "reference_checks_passed"])
    finally:
        await close_route_queries()
        await close_client()
    output = write("run", rows, ["id", "repeat", "status", "seconds", "model_calls", "routes", "schedule_passed", "constraints_status", "reference_checks_passed"])
    print(f"结果：{output}", flush=True)
    return rows


def replay(report):
    source = json.loads(Path(report).read_text(encoding='utf-8'))
    rows = []
    for original in source:
        if 'plan_snapshot' not in original or 'case_request' not in original:
            raise ValueError('报告缺少可重放快照，请使用新版 run 生成报告')
        row = summarize(TripPlan(**original['plan_snapshot']), TripRequest(**original['case_request']), original.get('case_expect', {}))
        row.update(id=original['id'], repeat=original.get('repeat', 1), status='replayed', previous_reference_checks_passed=original.get('reference_checks_passed'),
                   assessment_changed=row['reference_checks_passed'] != original.get('reference_checks_passed'))
        rows.append(row)
    if not rows:
        raise ValueError('报告没有可重放的行程')
    output = write('replay', rows, ['id', 'repeat', 'schedule_passed', 'constraints_status', 'reference_checks_passed', 'assessment_changed'])
    print(f'已离线重放 {len(rows)} 份快照 → {output}')
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["score", "run", "replay"])
    parser.add_argument("--cases", default="")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--database", type=Path, help="score 模式的只读 SQLite 文件")
    parser.add_argument("--report", type=Path, help="replay 模式的快照报告")
    parser.add_argument("--check", action='store_true', help="参考检查未全部通过时返回非零退出码，供回归门禁使用")
    args = parser.parse_args()
    if args.mode == "score":
        rows = score_saved(args.database)
    elif args.mode == 'replay':
        if not args.report: parser.error('replay 需要 --report')
        rows = replay(args.report)
    else:
        rows = asyncio.run(run_cases([c for c in args.cases.split(",") if c] or None, args.limit, args.repeat, args.timeout))
    if args.check and (not rows or not all(row.get('reference_checks_passed') is True for row in rows)):
        print('参考检查未全部通过；请查看报告中的缺失、冲突和约束。', flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
