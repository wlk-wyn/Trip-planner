"""Local SQLite trips with atomic version commits and owner isolation."""
from contextlib import contextmanager
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .run_leases import require_lease


class VersionConflict(Exception):
    pass


def change_summary(before, after):
    result = []
    if before.get('ticket_bookings', []) != after.get('ticket_bookings', []):
        result.append('更新用户录入的门票预订与退款记录')
    for index, day in enumerate(after.get("days", [])):
        old = next((d for d in before.get("days", []) if d.get("date") == day.get("date")), {})
        if old == day:
            continue
        old_names = {a["name"] for a in old.get("attractions", [])}
        names = {a["name"] for a in day.get("attractions", [])}
        details = []
        if names - old_names:
            details.append("新增：" + "、".join(sorted(names - old_names)))
        if old_names - names:
            details.append("移除：" + "、".join(sorted(old_names - names)))
        if [a["name"] for a in old.get("attractions", [])] != [a["name"] for a in day.get("attractions", [])] and old_names == names:
            details.append("调整景点顺序")
        old_by_name = {a["name"]: a for a in old.get("attractions", [])}
        for attraction in day.get("attractions", []):
            previous = old_by_name.get(attraction["name"])
            if not previous:
                continue
            if previous.get("visit_duration") != attraction.get("visit_duration"):
                details.append(f"{attraction['name']}游览时长：{previous.get('visit_duration')} → {attraction.get('visit_duration')} 分钟")
            if previous.get("fixed_start_time") != attraction.get("fixed_start_time"):
                details.append(f"调整{attraction['name']}固定预约")
            if previous.get('entry_window') != attraction.get('entry_window'):
                details.append(f"调整{attraction['name']}入场时段")
            if any(previous.get(key) != attraction.get(key) for key in ("ticket_price", "cost_status")):
                details.append(f"更新{attraction['name']}费用")
            if previous.get("description") != attraction.get("description"):
                details.append(f"更新{attraction['name']}的描述")
            if previous.get("address") != attraction.get("address"):
                details.append(f"更新{attraction['name']}的地址")
        if any(old.get(key) != day.get(key) for key in ("start_time", "end_time", "buffer_minutes")):
            details.append("调整每日可用时间或交通缓冲")
        if old.get("schedule") != day.get("schedule"):
            details.append("更新时间校验")
        if old.get("meals") != day.get("meals"):
            details.append("调整餐饮安排")
        if old.get("hotel") != day.get("hotel") or old.get("accommodation") != day.get("accommodation"):
            old_hotel,new_hotel=old.get('hotel') or {},day.get('hotel') or {}
            if old_hotel.get('nightly_quote')!=new_hotel.get('nightly_quote') and all(old_hotel.get(k)==new_hotel.get(k) for k in ('poi_id','name','address','location')):
                details.append('更新入住日期房型报价')
            else:details.append("调整住宿")
        result.append(f"第{index + 1}天 " + ("；".join(details) or "更新行程内容"))
    if before.get("overall_suggestions") != after.get("overall_suggestions"):
        result.append("更新旅行建议")
    if not result and before != after:
        result.append("更新行程信息")
    return result or ["行程未改变"]


_initialized = set()


class TripRepository:
    def __init__(self, path=None):
        self.path = Path(path) if path else Path(__file__).resolve().parents[2] / "var" / "trips.sqlite3"
        if self.path.resolve() in _initialized:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            # WAL lets status reads proceed while a planning run writes.
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS trips (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, request TEXT NOT NULL,
                plan TEXT NOT NULL, chat TEXT NOT NULL, version INTEGER NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS planning_runs (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, trip_id TEXT NOT NULL,
                base_version INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
                stage TEXT NOT NULL, payload TEXT NOT NULL, result TEXT,
                error TEXT NOT NULL DEFAULT '', queries INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS versions (
                trip_id TEXT NOT NULL, version INTEGER NOT NULL, plan TEXT NOT NULL,
                summary TEXT NOT NULL, origin TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(trip_id, version));
            """)
            # Additive, transactionally serialized migration for existing stores.
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(planning_runs)')}
            for column, definition in (('lease_token', 'TEXT'), ('lease_until', 'REAL'), ('attempt_count', 'INTEGER NOT NULL DEFAULT 0')):
                if column not in columns:
                    db.execute(f'ALTER TABLE planning_runs ADD COLUMN {column} {definition}')
            db.execute('CREATE INDEX IF NOT EXISTS planning_run_recovery ON planning_runs(status,lease_until)')
        _initialized.add(self.path.resolve())

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def read(self, owner, trip_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM trips WHERE owner=? AND id=?", (owner, trip_id)).fetchone()
        if row is None:
            raise KeyError(trip_id)
        result = dict(row)
        for field in ("request", "plan", "chat"):
            result[field] = json.loads(result[field])
        result.pop("owner")
        return result

    def list(self, owner):
        with self.connect() as db:
            rows = db.execute("SELECT id, plan, version, updated_at FROM trips WHERE owner=? ORDER BY updated_at DESC LIMIT 100", (owner,)).fetchall()
        return [{"id": r["id"], "city": json.loads(r["plan"])["city"],
                 "start_date": json.loads(r["plan"])["start_date"], "end_date": json.loads(r["plan"])["end_date"],
                 "version": r["version"], "updated_at": r["updated_at"]} for r in rows]

    def create(self, owner, request, plan, chat=None, run_id=None, extra=None, lease_token=None):
        """Insert a trip; with run_id the trip and the run's success are committed together."""
        if plan.get('ticket_bookings'):
            plan = {**plan, 'ticket_bookings': []}
        trip_id, now = str(uuid.uuid4()), datetime.now(timezone.utc).isoformat()
        encode = lambda v: json.dumps(v, ensure_ascii=False)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if run_id:
                run = db.execute("SELECT * FROM planning_runs WHERE id=? AND owner=?", (run_id, owner)).fetchone()
                if not run or run["status"] == "cancelled":
                    raise ValueError("任务已取消")
                if run["status"] == "succeeded":
                    return json.loads(run["result"])
                require_lease(run, lease_token)
            db.execute("INSERT INTO trips VALUES (?,?,?,?,?,?,?)", (trip_id, owner, encode(request), encode(plan), encode(chat or []), 1, now))
            db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?)", (trip_id, 1, encode(plan), encode(["初始行程"]), "initial", now))
            if run_id:
                require_lease(run, lease_token)
                result = dict(id=trip_id, request=request, plan=plan, chat=chat or [], version=1, updated_at=now, **(extra or {}))
                db.execute("UPDATE planning_runs SET status='succeeded', stage='完成', lease_token=NULL,lease_until=NULL,trip_id=?, result=?, updated_at=? WHERE id=?",
                           (trip_id, encode(result), now, run_id))
        return self.read(owner, trip_id)

    def commit(self, owner, trip_id, base_version, plan, origin, chat=None, run_id=None, extra=None, lease_token=None):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM trips WHERE owner=? AND id=?", (owner, trip_id)).fetchone()
            if row is None:
                raise KeyError(trip_id)
            if run_id:
                run = db.execute("SELECT * FROM planning_runs WHERE id=? AND owner=? AND trip_id=?", (run_id,owner,trip_id)).fetchone()
                if not run or run["status"] == "cancelled":
                    raise ValueError("任务已取消")
                if run["status"] == "succeeded":
                    return json.loads(run["result"])
                require_lease(run, lease_token)
            if row["version"] != base_version:
                raise VersionConflict("行程已在其他页面更新，请重新打开后再试")
            before = json.loads(row["plan"])
            from ..models.schemas import TripPlan
            old_plan, new_plan = TripPlan.model_validate(before), TripPlan.model_validate(plan)
            if origin not in {'ticket_booking', 'restore'}:
                from .ticket_bookings import preserve_bookings
                preserve_bookings(new_plan, old_plan)
                plan = {**plan, 'ticket_bookings': [b.model_dump() for b in new_plan.ticket_bookings]}
            comparable_before=old_plan.model_dump()
            comparable_after=new_plan.model_dump()
            changed = comparable_before != comparable_after
            version = base_version + int(changed)
            summary = change_summary(comparable_before, comparable_after)
            encoded = json.dumps(plan, ensure_ascii=False)
            if changed:
                db.execute("INSERT INTO versions VALUES (?,?,?,?,?,?)", (trip_id, version, encoded, json.dumps(summary, ensure_ascii=False), origin, now))
            db.execute("UPDATE trips SET plan=?, chat=?, version=?, updated_at=? WHERE id=?", (encoded, json.dumps(chat[-20:] if chat is not None else json.loads(row["chat"]), ensure_ascii=False), version, now, trip_id))
            if run_id:
                require_lease(run, lease_token)
                result = dict(row)
                result.update(plan=plan, request=json.loads(row["request"]), chat=chat if chat is not None else json.loads(row["chat"]), version=version, updated_at=now, change_summary=summary)
                result.pop("owner", None)
                result.update(extra or {})
                db.execute("UPDATE planning_runs SET status='succeeded', stage='完成', lease_token=NULL,lease_until=NULL,result=?, updated_at=? WHERE id=?", (json.dumps(result,ensure_ascii=False),now,run_id))
        result = self.read(owner, trip_id)
        result.update(extra or {})
        result["change_summary"] = summary
        return result

    def versions(self, owner, trip_id):
        self.read(owner, trip_id)
        with self.connect() as db:
            rows = db.execute("SELECT version, summary, origin, created_at FROM versions WHERE trip_id=? ORDER BY version DESC", (trip_id,)).fetchall()
        return [{**dict(row), "summary": json.loads(row["summary"])} for row in rows]

    def restore(self, owner, trip_id, base_version, target):
        self.read(owner, trip_id)
        with self.connect() as db:
            row = db.execute("SELECT plan FROM versions WHERE trip_id=? AND version=?", (trip_id, target)).fetchone()
        if not row:
            raise KeyError(target)
        return self.commit(owner, trip_id, base_version, json.loads(row["plan"]), "restore")
