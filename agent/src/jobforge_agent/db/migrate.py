"""Forward-only SQL migrations (SPEC D12, ARCHITECTURE 4.4).

- Files are migrations/NNNN_name.sql, applied in order; the version is PRAGMA user_version.
- An existing database is backed up and the backup integrity-checked before any migration.
- Each migration runs in one transaction with its user_version bump, so a crash or an error
  midway leaves the previous version intact.
- A database newer than this agent is refused (DatabaseNewer): never run on an unknown schema.
- After migrating, every append-only table must still have both of its triggers.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from jobforge_agent.db import backup

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

# table -> trigger names that must exist (ARCHITECTURE 4.3)
APPEND_ONLY_TRIGGERS = {
    "raw_postings": ("raw_postings_no_update", "raw_postings_no_delete"),
    "search_profiles": ("search_profiles_no_update", "search_profiles_no_delete"),
    "profile_snapshots": ("profile_snapshots_no_update", "profile_snapshots_no_delete"),
    "profile_snapshot_items": ("profile_snapshot_items_no_update", "profile_snapshot_items_no_delete"),
    "application_events": ("application_events_no_update", "application_events_no_delete"),
    "change_events": ("change_events_no_update", "change_events_prune_only_old"),
    "golden_labels": ("golden_labels_no_update", "golden_labels_no_delete"),
}
# The migration that creates each table; tables not listed come from 0001. A database at an older
# version (or a test running a partial migration list) is checked only for the tables it has.
TABLE_SINCE_VERSION = {"golden_labels": 8}


class DatabaseNewer(RuntimeError):
    def __init__(self, db_version: int, agent_version: int):
        super().__init__(f"database schema v{db_version} is newer than this agent (v{agent_version})")
        self.db_version = db_version
        self.agent_version = agent_version


class MigrationFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


@dataclass(frozen=True)
class MigrateResult:
    from_version: int
    to_version: int
    backup: Path | None


def bundled_migrations() -> list[Migration]:
    folder = resources.files("jobforge_agent.db") / "migrations"
    return load_migrations(folder)


def load_migrations(folder) -> list[Migration]:
    found = []
    for entry in folder.iterdir():
        m = _NAME.match(entry.name)
        if m:
            found.append(Migration(int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort(key=lambda mg: mg.version)
    expected = list(range(1, len(found) + 1))
    if [mg.version for mg in found] != expected:
        raise MigrationFailed(f"migration numbers must be 1..N without gaps, got {[mg.version for mg in found]}")
    return found


def target_version(migrations: list[Migration] | None = None) -> int:
    migrations = bundled_migrations() if migrations is None else migrations
    return migrations[-1].version if migrations else 0


def migrate(conn: sqlite3.Connection, db_path: Path, backups: Path, migrations: list[Migration] | None = None) -> MigrateResult:
    """Bring the database to the newest bundled version. `conn` must be a 'migration' connection."""
    migrations = bundled_migrations() if migrations is None else migrations
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    target = target_version(migrations)
    if current > target:
        raise DatabaseNewer(current, target)
    if current == target:
        check_append_only_triggers(conn)
        return MigrateResult(current, current, None)
    backup_path = backup.pre_migrate_backup(db_path, backups, current) if current > 0 else None
    for mg in migrations:
        if mg.version <= current:
            continue
        script = f"BEGIN IMMEDIATE;\n{mg.sql}\nPRAGMA user_version = {mg.version};\nCOMMIT;"
        try:
            conn.executescript(script)
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationFailed(f"{mg.name}: {exc}") from exc
    check_append_only_triggers(conn)
    return MigrateResult(current, target, backup_path)


def check_append_only_triggers(conn: sqlite3.Connection) -> None:
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    missing = [t for table, names in APPEND_ONLY_TRIGGERS.items() if TABLE_SINCE_VERSION.get(table, 1) <= version
               for t in names if t not in present]
    if missing:
        raise MigrationFailed(f"append-only triggers missing after migration: {missing}")
