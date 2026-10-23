"""Create and verify a new SQLite snapshot; never overwrite the source/target."""
import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path


def backup(source: Path, destination: Path):
    source,destination=source.resolve(strict=True),destination.resolve()
    if source==destination:raise ValueError('Backup must use a different destination')
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Reserve a new path atomically, avoiding overwrite of existing backups.
    descriptor=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(descriptor)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as original:
        with closing(sqlite3.connect(destination)) as copied:
            original.backup(copied)
            copied.commit()
            if copied.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Backup integrity check failed')
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1]/'backend/var/trips.sqlite3')
    parser.add_argument('--destination',type=Path,required=True)
    args=parser.parse_args();print('Verified SQLite backup:',backup(args.source,args.destination))
