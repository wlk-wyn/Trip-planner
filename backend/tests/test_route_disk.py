import asyncio
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

from app.services import schedule_validator as routes

RESULT={'duration_minutes':12,'distance_meters':800}


class DiskConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'cache.sqlite3'
        self.setting=patch.object(routes,'ROUTE_CACHE_PATH',self.path);self.setting.start()
        routes._disk_disabled_until=0
        self.stamp=datetime.now(timezone.utc).isoformat()

    async def asyncTearDown(self):
        await routes.close_route_queries();self.setting.stop();self.tmp.cleanup();routes._disk_disabled_until=0

    async def test_cold_parallel_read_write_roundtrip_has_no_wal_initialization_race(self):
        keys=[('synthetic',i) for i in range(32)]
        await asyncio.gather(*(asyncio.to_thread(routes._disk_cache,key) for key in keys))
        await asyncio.gather(*(asyncio.to_thread(routes._disk_cache,key,(RESULT,self.stamp)) for key in keys))
        rows=await asyncio.gather(*(asyncio.to_thread(routes._disk_cache,key) for key in keys))
        self.assertEqual(rows,[(RESULT,self.stamp)]*32)
        self.path.unlink()  # No live connection may hold the Windows cache file.

    async def test_queue_pressure_skips_reads_without_disabling_a_healthy_store(self):
        gate=routes._disk_gate();await gate.acquire()
        try:
            with patch.object(routes,'DISK_TIMEOUT',.01),patch.object(routes,'DISK_WRITE_QUEUE_TIMEOUT',.5):
                writer=asyncio.create_task(routes._best_effort_disk(('write',),(RESULT,self.stamp)))
                self.assertIsNone(await routes._best_effort_disk(('read',)))
                self.assertEqual(routes._disk_disabled_until,0);self.assertFalse(writer.done())
        finally:gate.release()
        await writer
        self.assertEqual(await routes._best_effort_disk(('write',)),(RESULT,self.stamp))

    async def test_queued_cache_work_does_not_occupy_executor_threads(self):
        gate=routes._disk_gate();await gate.acquire()
        try:
            with patch.object(routes,'_disk_cache') as disk:
                self.assertIsNone(await routes._best_effort_disk(('read',)))
                disk.assert_not_called();self.assertEqual(routes._disk_disabled_until,0)
        finally:gate.release()

    async def test_cache_worker_uses_the_captured_store_if_configuration_changes(self):
        started,release=threading.Event(),threading.Event()
        actual=routes._disk_cache
        def blocked(key,value,path):
            started.set();release.wait(2);return actual(key,value,path)
        with patch.object(routes,'_disk_cache',side_effect=blocked):
            writer=asyncio.create_task(routes._best_effort_disk(('test',),(RESULT,self.stamp)))
            await asyncio.to_thread(started.wait,1)
            other=self.path.with_name('other.sqlite3')
            with patch.object(routes,'ROUTE_CACHE_PATH',other):
                release.set();await writer
        self.assertTrue(self.path.exists());self.assertFalse(other.exists())

    async def test_external_writer_contention_is_bounded_and_recovers_after_backoff(self):
        routes._disk_cache(('seed',),(RESULT,self.stamp))
        with closing(sqlite3.connect(self.path)) as external:
            external.execute('BEGIN IMMEDIATE')
            with self.assertLogs(routes.logger,level='WARNING'):
                self.assertIsNone(await routes._best_effort_disk(('blocked',),(RESULT,self.stamp)))
            self.assertGreater(routes._disk_disabled_until,time.monotonic())
            external.rollback()
        routes._disk_disabled_until=0
        await routes._best_effort_disk(('retry',),(RESULT,self.stamp))
        self.assertEqual(await routes._best_effort_disk(('retry',)),(RESULT,self.stamp))
