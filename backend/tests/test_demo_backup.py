import importlib.util
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

path=Path(__file__).resolve().parents[2]/'scripts/backup-trips.py'
spec=importlib.util.spec_from_file_location('demo_backup',path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class BackupTests(unittest.TestCase):
    def test_wal_snapshot_contains_committed_data_without_changing_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.sqlite3';dest=Path(folder)/'backup.sqlite3'
            with closing(sqlite3.connect(source)) as db:
                db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE sample(value)')
                db.execute('INSERT INTO sample VALUES (1)');db.commit()
                db.execute('INSERT INTO sample VALUES (2)') # not committed yet
                module.backup(source,dest)
                with closing(sqlite3.connect(dest)) as copied:
                    self.assertEqual(copied.execute('SELECT value FROM sample').fetchall(),[(1,)])
                self.assertEqual(db.execute('SELECT COUNT(*) FROM sample').fetchone()[0],2)

    def test_existing_destination_and_same_source_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'source.sqlite3';dest=Path(folder)/'existing.txt'
            with closing(sqlite3.connect(source)) as db:db.execute('CREATE TABLE sample(value)')
            dest.write_text('keep',encoding='utf-8')
            with self.assertRaises(FileExistsError):module.backup(source,dest)
            self.assertEqual(dest.read_text(encoding='utf-8'),'keep')
            with self.assertRaises(ValueError):module.backup(source,source)
