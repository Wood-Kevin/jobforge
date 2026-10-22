import pytest

from jobforge_agent.db import backup
from jobforge_agent.db.connect import open_connection


def test_daily_backup_once_per_day_and_keeps_seven(db, paths):
    made = [backup.daily_backup(paths.db, paths.backups, today=f"202610{d:02d}") for d in range(1, 11)]
    assert all(made)
    assert backup.daily_backup(paths.db, paths.backups, today="20261010") is None
    kept = sorted(p.name for p in paths.backups.glob("daily-*.db"))
    assert kept == [f"daily-202610{d:02d}.db" for d in range(4, 11)]
    assert all(backup.integrity(p) == "ok" for p in paths.backups.glob("daily-*.db"))


def test_restore_round_trip(db, paths):
    db.execute("UPDATE settings SET value = '1' WHERE key = 'discovery_paused'")
    saved = backup.backup_to(paths.db, paths.backups / "manual.db")
    db.execute("UPDATE settings SET value = '0' WHERE key = 'discovery_paused'")
    db.close()
    backup.restore(paths.db, saved)
    conn = open_connection(paths.db, "readonly", readonly=True)
    assert conn.execute("SELECT value FROM settings WHERE key = 'discovery_paused'").fetchone()[0] == "1"


def test_restore_refuses_a_corrupt_source(db, paths, tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4000)
    with pytest.raises(Exception):
        backup.restore(paths.db, bad)
    assert db.execute("SELECT count(*) FROM settings").fetchone()[0] >= 1  # live DB untouched
