"""Bound interactive HTTP work across workers sharing one local SQLite file.

Peer limits are resource controls, not account authentication. Job admission and
provider QPS remain separate; progress reads/cancellation never take these slots.
"""
import asyncio
import hashlib
import logging
import math
import sqlite3
import time
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from fastapi import HTTPException, Request

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Policy:
    running: int
    per_peer_running: int
    per_peer_minute: int
    per_minute: int


class GateFull(Exception):
    def __init__(self, retry_after):
        self.retry_after = max(1, math.ceil(retry_after))


class GateUnavailable(Exception):
    pass


def request_group(method, path):
    """Use the matched route template, not user-controlled concrete path text."""
    if method == 'POST' and path.startswith('/api/trip/'):
        return 'lookup' if path == '/api/trip/search-poi' else 'interactive'
    if path.startswith(('/api/poi/', '/api/map/')) and not path.endswith('/health'):
        return 'lookup'
    if path == '/api/trips/references/analyze' or (
        method == 'POST' and path in {
            '/api/trips/{trip_id}/versions', '/api/trips/{trip_id}/chat',
            '/api/trips/{trip_id}/validate', '/api/trips/{trip_id}/enrich',
            '/api/trips/{trip_id}/days/{date}/fixes',
        }
    ):
        return 'interactive'
    if method == 'GET' and path in {'/api/trips/{trip_id}/weather', '/api/trips/{trip_id}/prep'}:
        return 'lookup'
    return None


class RequestGate:
    def __init__(self, path, policies, ttl=60, clock=time.time):
        self.path, self.policies, self.ttl, self.clock = Path(path), policies, ttl, clock

    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=.25)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS http_leases (
                    id TEXT PRIMARY KEY, bucket TEXT NOT NULL,
                    peer TEXT NOT NULL, expires REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS http_leases_bucket ON http_leases(bucket,peer);
                CREATE TABLE IF NOT EXISTS http_windows (
                    bucket TEXT NOT NULL, scope TEXT NOT NULL,
                    start REAL NOT NULL, count INTEGER NOT NULL,
                    PRIMARY KEY(bucket,scope));
            ''')
            return db
        except BaseException:
            db.close()
            raise

    def acquire(self, token, group, peer):
        policy, instant = self.policies[group], self.clock()
        window = math.floor(instant / 60) * 60
        with closing(self.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM http_leases WHERE expires<=?', (instant,))
            db.execute('DELETE FROM http_windows WHERE start<?', (window-60,))
            global_count, own_count = db.execute(
                'SELECT COUNT(*),COALESCE(SUM(peer=?),0) FROM http_leases WHERE bucket=?',
                (peer, group)).fetchone()
            if global_count >= policy.running or own_count >= policy.per_peer_running:
                raise GateFull(3)
            for scope, ceiling in (('*', policy.per_minute), (peer, policy.per_peer_minute)):
                row = db.execute('SELECT start,count FROM http_windows WHERE bucket=? AND scope=?', (group, scope)).fetchone()
                if row and row[0] == window and row[1] >= ceiling:
                    raise GateFull(window+60-instant)
            db.execute('INSERT INTO http_leases VALUES (?,?,?,?)', (token, group, peer, instant+self.ttl))
            for scope in ('*', peer):
                db.execute('''INSERT INTO http_windows VALUES (?,?,?,1)
                    ON CONFLICT(bucket,scope) DO UPDATE SET
                    count=CASE WHEN start=excluded.start THEN count+1 ELSE 1 END,start=excluded.start''',
                           (group, scope, window))

    def renew(self, token):
        instant = self.clock()
        with closing(self.connect()) as db, db:
            changed = db.execute('UPDATE http_leases SET expires=? WHERE id=? AND expires>?',
                                 (instant+self.ttl, token, instant)).rowcount
            if changed != 1:
                raise GateUnavailable('request permit lost')

    def release(self, token):
        with closing(self.connect()) as db, db:
            db.execute('DELETE FROM http_leases WHERE id=?', (token,))

    def probe(self):
        with closing(self.connect()) as db:
            db.execute('SELECT count(*) FROM http_leases').fetchone()
        return True

    @staticmethod
    async def database_call(operation, *args):
        task = asyncio.create_task(asyncio.to_thread(operation, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try: await asyncio.shield(task)
                except asyncio.CancelledError: continue
            # Consume thread errors; cancellation still wins after the operation finishes.
            try: task.result()
            except Exception: pass
            raise

    async def heartbeat(self, token, owner_task):
        while True:
            await asyncio.sleep(self.ttl/3)
            try:
                await self.database_call(self.renew, token)
            except Exception:
                # Stop spending resources if shared ownership can no longer be proven.
                log.warning('HTTP resource permit could not be renewed; cancelling request')
                owner_task.cancel()
                return

    @asynccontextmanager
    async def permit(self, group, address):
        token, renewal = str(uuid4()), None
        peer = hashlib.sha256(address.encode()).hexdigest()
        try:
            try:
                await self.database_call(self.acquire, token, group, peer)
            except (OSError, sqlite3.Error):
                raise GateUnavailable('request coordination unavailable') from None
            renewal = asyncio.create_task(self.heartbeat(token, asyncio.current_task()))
            yield
        finally:
            if renewal:
                renewal.cancel()
                await asyncio.gather(renewal, return_exceptions=True)
            # Token is known before admission: cancellation during SQLite commit must
            # also clean up a permit whose success response never reached this task.
            try: await self.database_call(self.release, token)
            except (OSError, sqlite3.Error):
                log.warning('HTTP resource permit cleanup failed; it remains bounded by its lease')


def configure_request_gate(app, settings, *, path=None):
    app.state.request_gate = RequestGate(path or Path(__file__).resolve().parents[2]/'var'/'request-gate.sqlite3', {
        'interactive': Policy(settings.http_interactive_running, settings.http_interactive_per_peer_running,
                              settings.http_interactive_per_peer_minute, settings.http_interactive_per_minute),
        'lookup': Policy(settings.http_lookup_running, settings.http_lookup_per_peer_running,
                         settings.http_lookup_per_peer_minute, settings.http_lookup_per_minute),
    }, ttl=settings.http_request_lease_seconds)


async def protect_request(request: Request):
    gate = getattr(request.app.state, 'request_gate', None)
    group = request_group(request.method, request.scope['route'].path)
    # Small test/fixture apps opt into this gate explicitly; the real main app
    # always configures it, independently of lifespan/startup execution.
    if gate is None or group is None:
        yield
        return
    address = request.client.host if request.client else '__unknown_peer__'
    try:
        async with gate.permit(group, address):
            yield
    except GateFull as exc:
        raise HTTPException(429, '当前操作较多，请稍后重试；已接收的规划任务仍可查看或取消。',
                            headers={'Retry-After': str(exc.retry_after)}) from None
    except GateUnavailable:
        raise HTTPException(503, '资源协调服务暂时不可用，请稍后重试。') from None
