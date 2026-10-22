"""Shared, no-burst permits for processes using one local SQLite store.

Permits are granted now, never reserved for future waiters. Failure to coordinate
is an error, not permission to send an unpaced provider request.
"""
import asyncio
import hashlib
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path


class LimiterUnavailable(RuntimeError):
    pass


_initialized = set()


def default_store():
    return Path(__file__).resolve().parents[2]/'var'/'provider-limits.sqlite3'


def scope_id(api_key, kind):
    return hashlib.sha256(('amap\0'+api_key+'\0'+kind).encode()).hexdigest()


def attempt(path, scope, qps, clock=time.time):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=.1)) as db:
        if path.resolve() not in _initialized:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS permits(scope TEXT PRIMARY KEY,last_grant REAL NOT NULL,interval REAL NOT NULL)')
            db.commit()
            _initialized.add(path.resolve())
        with db:
            db.execute('BEGIN IMMEDIATE')
            instant = clock()
            row = db.execute('SELECT last_grant,interval FROM permits WHERE scope=?', (scope,)).fetchone()
            interval = 1/qps
            if row:
                if row[0]-instant > 1:
                    raise LimiterUnavailable('共享限流时钟异常，请检查服务器时间')
                # During live traffic use the more conservative observed setting.
                if instant-row[0] < 60:
                    interval = max(interval, row[1])
                delay = row[0]+interval-instant
                if delay > 0:
                    return delay
            db.execute('INSERT INTO permits VALUES (?,?,?) ON CONFLICT(scope) DO UPDATE SET last_grant=excluded.last_grant,interval=excluded.interval',
                       (scope, instant, interval))
            return 0.0


async def acquire(api_key, kind, qps, *, path=None, timeout=60):
    if not api_key or kind not in {'route', 'poi', 'metadata'}:
        raise LimiterUnavailable('共享限流配置不完整')
    if isinstance(qps, bool) or not isinstance(qps, (int, float)) or not math.isfinite(qps) or not .1 <= qps <= 50:
        raise LimiterUnavailable('共享限流频率配置无效')
    path = path or default_store()
    scope = scope_id(api_key, kind)
    deadline = time.monotonic()+timeout
    while True:
        if time.monotonic() >= deadline:
            raise LimiterUnavailable('地图请求排队超时，请稍后重试')
        task = asyncio.create_task(asyncio.to_thread(attempt, path, scope, qps))
        try:
            delay = await asyncio.shield(task)
        except asyncio.CancelledError:
            # Do not abandon a thread with an open SQLite connection.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            try: task.result()
            except Exception: pass
            raise
        except sqlite3.OperationalError as exc:
            if getattr(exc, 'sqlite_errorcode', None) not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                raise LimiterUnavailable('共享限流存储不可用，请稍后重试') from None
            delay = .05
        except (OSError, sqlite3.Error):
            raise LimiterUnavailable('共享限流存储不可用，请稍后重试') from None
        if delay <= 0:
            return
        await asyncio.sleep(min(delay, 1, max(0, deadline-time.monotonic())))


async def readiness():
    """Exercise local storage with a separate scope, never a real API permit."""
    try:
        await asyncio.to_thread(attempt, default_store(), scope_id('__local_health__', 'health'), 50)
        return True
    except Exception:
        return False
