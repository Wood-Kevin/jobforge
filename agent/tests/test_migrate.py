"""Forward-only migrations with backup first and refuse-newer (SPEC D12)."""
from importlib import resources
import sqlite3

import pytest

from jobforge_agent.db import backup, migrate
from jobforge_agent.db.connect import open_connection
from jobforge_agent.db.migrate import Migration


def _v1() -> Migration:
    return migrate.bundled_migrations()[0]


def test_fresh_database_reaches_latest_version(paths):
    conn = open_connection(paths.db, "migration")
    result = migrate.migrate(conn, paths.db, paths.backups)
    assert (result.from_version, result.to_version, result.backup) == (0, migrate.target_version(), None)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == migrate.target_version()


def test_second_run_is_a_noop(db, paths):
    result = migrate.migrate(db, paths.db, paths.backups)
    assert result.from_version == result.to_version
    assert list(paths.backups.glob("*.db")) == []


def test_failed_migration_leaves_previous_version_intact(paths):
    conn = open_connection(paths.db, "migration")
    migrate.migrate(conn, paths.db, paths.backups, [_v1()])
    broken = Migration(2, "0002_broken.sql", "CREATE TABLE half_done (a INTEGER) STRICT;\nINSERT INTO no_such_table VALUES (1);")
    with pytest.raises(migrate.MigrationFailed, match="0002_broken"):
        migrate.migrate(conn, paths.db, paths.backups, [_v1(), broken])
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name = 'half_done'").fetchone()[0] == 0
    assert not conn.in_transaction


def test_upgrade_backs_up_first_and_keeps_data(paths):
    conn = open_connection(paths.db, "migration")
    migrate.migrate(conn, paths.db, paths.backups, [_v1()])
    conn.execute("UPDATE settings SET value = '1' WHERE key = 'discovery_paused'")
    v2 = Migration(2, "0002_add.sql", "CREATE TABLE extra (a INTEGER) STRICT;")
    result = migrate.migrate(conn, paths.db, paths.backups, [_v1(), v2])
    assert (result.from_version, result.to_version) == (1, 2)
    assert result.backup is not None and result.backup.name.startswith("pre-migrate-v1-")
    assert backup.integrity(result.backup) == "ok"
    old = sqlite3.connect(result.backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 1
    assert old.execute("SELECT value FROM settings WHERE key = 'discovery_paused'").fetchone()[0] == "1"
    old.close()


def test_newer_database_is_refused(db, paths):
    db.execute("PRAGMA user_version = 99")
    with pytest.raises(migrate.DatabaseNewer) as exc:
        migrate.migrate(db, paths.db, paths.backups)
    assert (exc.value.db_version, exc.value.agent_version) == (99, migrate.target_version())


def test_migration_numbers_must_be_contiguous(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0003_c.sql").write_text("SELECT 1;")
    with pytest.raises(migrate.MigrationFailed, match="without gaps"):
        migrate.load_migrations(tmp_path)


def test_schema_version_matches_the_bundled_files():
    """Adding migration NNNN means raising SCHEMA_VERSION to NNNN in db/migrate.py."""
    files = migrate.load_migrations(resources.files("jobforge_agent.db") / "migrations")
    assert [mg.version for mg in files] == list(range(1, migrate.SCHEMA_VERSION + 1))


def test_a_leftover_newer_migration_file_does_not_hide_a_newer_database(tmp_path, paths):
    """P1.9 drill: a downgrade left 0008 in the install dir and the older agent ran on the newer DB."""
    for mg in migrate.bundled_migrations():
        (tmp_path / mg.name).write_text(mg.sql, encoding="utf-8")
    (tmp_path / f"{migrate.SCHEMA_VERSION + 1:04d}_from_a_newer_install.sql").write_text("CREATE TABLE x (a INTEGER) STRICT;", encoding="utf-8")
    kept = migrate.load_migrations(tmp_path, max_version=migrate.SCHEMA_VERSION)
    assert migrate.target_version(kept) == migrate.SCHEMA_VERSION
    conn = open_connection(paths.db, "migration")
    migrate.migrate(conn, paths.db, paths.backups)
    conn.execute(f"PRAGMA user_version = {migrate.SCHEMA_VERSION + 1}")
    with pytest.raises(migrate.DatabaseNewer):
        migrate.migrate(conn, paths.db, paths.backups, kept)
