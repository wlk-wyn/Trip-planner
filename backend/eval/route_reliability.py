"""Reproducible route fan-out/fault benchmark with synthetic data and no network.

Run: python -X utf8 -m eval.route_reliability --concurrency 32
This measures the query/cache orchestration only, not real Amap or model latency.
"""
import argparse
import asyncio
import json
import math
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.models.schemas import Attraction, Location
from app.services import schedule_validator as routes


async def measure(case, concurrency, folder):
    await routes.close_route_queries()
    routes._route_cache.clear()
    routes._disk_disabled_until = 0
    first = {"id": "a", "kind": "attraction", "place": Attraction(name="合成地点甲", location=Location(longitude=116.4, latitude=39.9), address="", description="", visit_duration=90)}
    second = {"id": "b", "kind": "attraction", "place": Attraction(name="合成地点乙", location=Location(longitude=116.42, latitude=39.92), address="", description="", visit_duration=90)}
    release, ready = asyncio.Event(), asyncio.Event()
    calls = 0
    async def provider(*_):
        nonlocal calls
        calls += 1
        ready.set()
        if case == "cancel_one":
            await release.wait()
        await asyncio.sleep(.03)
        return {"duration_minutes": None if case == "invalid_duration" else 12, "distance_meters": 800}
    async def query():
        return await routes.route_segment(first, second, "合成城市", "walking", asyncio.Semaphore(4), provider)
    async def timed():
        started = time.perf_counter()
        stamp = None
        try:
            result = await query()
            status = result.status
            stamp = result.checked_at
        except asyncio.CancelledError:
            status = "cancelled"
        return {"status": status, "checked_at": stamp, "ms": (time.perf_counter() - started) * 1000}

    with patch.object(routes, 'get_route', provider), patch.object(routes, 'ROUTE_CACHE_PATH', Path(folder) / f'{case}.sqlite3'):
        original_stamp = None
        if case in ("warm_memory", "warm_disk"):
            original_stamp = (await query()).checked_at
            await asyncio.gather(*routes._cache_writes)
            calls = 0
            if case == 'warm_disk':
                routes._route_cache.clear()
        # Failure is injected only into the cache; synthetic provider still works.
        def unavailable(*_):
            raise OSError('synthetic cache outage')
        cache_patch = patch.object(routes, '_disk_cache', side_effect=unavailable) if case == "cache_unavailable" else None
        if cache_patch:
            cache_patch.start()
        try:
            tasks = [asyncio.create_task(timed()) for _ in range(concurrency)]
            if case == "cancel_one":
                async with asyncio.timeout(5):
                    await ready.wait()
                    while next(iter(routes._inflight.values())).waiters < concurrency:
                        await asyncio.sleep(0)
                tasks[0].cancel()
                release.set()
            rows = await asyncio.wait_for(asyncio.gather(*tasks), 10)
            await asyncio.gather(*routes._cache_writes)
        finally:
            if cache_patch:
                cache_patch.stop()
            await routes.close_route_queries()
    statuses = {status: sum(row['status'] == status for row in rows) for status in ['verified', 'unverified', 'cancelled']}
    values = sorted(row['ms'] for row in rows if row['status'] != 'cancelled')
    expected = {'verified': concurrency, 'unverified': 0, 'cancelled': 0}
    if case == 'invalid_duration':
        expected.update(verified=0, unverified=concurrency)
    elif case == 'cancel_one':
        expected.update(verified=concurrency-1, cancelled=1)
    preserved = all(row['checked_at'] == original_stamp for row in rows) if original_stamp else None
    passed = statuses == expected and calls == (0 if case in ('warm_memory','warm_disk') else 1) and not routes._inflight and not routes._cache_writes and preserved is not False
    return {"case": case, "requests": concurrency, "provider_calls": calls, **statuses,
            "p50_ms": round(values[math.ceil(len(values)*.5)-1], 2),
            "p95_ms": round(values[math.ceil(len(values)*.95)-1], 2), "original_timestamp_preserved":preserved, "passed": passed}


async def run(concurrency):
    with tempfile.TemporaryDirectory() as folder:
        rows = [await measure(case, concurrency, folder) for case in ['cold_shared', 'warm_memory', 'warm_disk', 'cancel_one', 'cache_unavailable', 'invalid_duration']]
    return {"generated_at": datetime.now(timezone.utc).isoformat(), "synthetic": True, "network_calls": 0,
            "provider_delay_ms": 30, "scope": "Route orchestration only; not real provider or full planning latency", "cases": rows, "passed": all(row['passed'] for row in rows)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--concurrency', type=int, default=32)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if not 2 <= args.concurrency <= 256:
        parser.error('concurrency must be between 2 and 256')
    report = asyncio.run(run(args.concurrency))
    output = args.output or Path(__file__).parent / 'results' / f'route-reliability-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f'Report: {output}')
    raise SystemExit(0 if report['passed'] else 1)
