"""Online backups and restore (ARCHITECTURE 4.4).

Uses SQLite's backup API from a read-only source connection, which is consistent under WAL
while the agent keeps writing. Every copy is integrity-checked before it counts.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DAILY_KEEP = 7
PRE_MIGRATE_KEEP = 5


class BackupError(RuntimeError):
    pass


def integrity(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()


def backup_to(db_path: Path, dest: Path) -> Path:
    """Copy the live database to dest and verify the copy. Raises BackupError if it is not 'ok'."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    result = integrity(tmp)
    if result != "ok":
        tmp.unlink(missing_ok=True)
        raise BackupError(f"backup copy failed integrity_check: {result}")
    tmp.replace(dest)
    return dest


def pre_migrate_backup(db_path: Path, backups: Path, from_version: int) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = backup_to(db_path, backups / f"pre-migrate-v{from_version}-{stamp}.db")
    _prune(backups, "pre-migrate-v*.db", PRE_MIGRATE_KEEP)
    return path


def daily_backup(db_path: Path, backups: Path, today: str | None = None) -> Path | None:
    """One backup per calendar day; returns the new file, or None if today's already exists."""
    today = today or time.strftime("%Y%m%d")
    dest = backups / f"daily-{today}.db"
    if dest.exists():
        return None
    backup_to(db_path, dest)
    _prune(backups, "daily-*.db", DAILY_KEEP)
    return dest


def _prune(folder: Path, pattern: str, keep: int) -> None:
    files = sorted(folder.glob(pattern))  # names sort chronologically
    for old in files[:-keep]:
        old.unlink(missing_ok=True)


class SourceNewer(BackupError):
    def __init__(self, source_version: int, max_version: int):
        super().__init__(f"backup schema v{source_version} is newer than this version of JobForge (v{max_version})")
        self.source_version, self.max_version = source_version, max_version


def schema_version(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def restore(db_path: Path, source: Path, backups: Path | None = None, max_version: int | None = None) -> Path | None:
    """Replace the database with `source`. The caller must hold the instance lock.

    With `backups`, the current database is first copied to `before-restore-v<N>-<time>.db` there and
    the copy's path returned: a rollback (ARCHITECTURE 7.2) then discards nothing for good, and
    installing the newer version again and restoring that copy brings the newer data back. Rejected
    alternative: replacing in place with only a warning, which made every rollback destructive.
    With `max_version`, a source newer than this agent's schema is refused before anything changes."""
    result = integrity(source)
    if result != "ok":
        raise BackupError(f"restore source failed integrity_check: {result}")
    if max_version is not None and (v := schema_version(source)) > max_version:
        raise SourceNewer(v, max_version)
    kept = None
    if backups is not None and db_path.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        kept = backup_to(db_path, backups / f"before-restore-v{schema_version(db_path)}-{stamp}.db")
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(db_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return kept
