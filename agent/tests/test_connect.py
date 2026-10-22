import sqlite3

import pytest

from jobforge_agent.db.connect import GRANTS, open_connection, pragma_values


def test_pragmas_are_set_and_verified(db, paths):
    for component in GRANTS:
        conn = open_connection(paths.db, component)
        assert pragma_values(conn) == {"journal_mode": "wal", "synchronous": 2, "foreign_keys": 1}
        conn.close()


def test_foreign_keys_enforced(db, paths):
    # SQLite defaults foreign_keys OFF; an orphan row must fail on every agent connection.
    conn = open_connection(paths.db, "user")
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute("INSERT INTO applications (job_id, status, created_at_ms) VALUES (999, 'saved', 1)")


def test_strict_types_and_enums(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO agent_runs (pid, version, elevated, started_at_ms, heartbeat_at_ms) VALUES ('not-an-int', 'x', 0, 1, 1)")
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.execute("INSERT INTO applications (job_id, status, created_at_ms) VALUES (1, 'hired', 1)")


def test_readonly_connection_cannot_write(db, paths):
    ro = open_connection(paths.db, "readonly", readonly=True)
    with pytest.raises(sqlite3.Error):
        ro.execute("INSERT INTO settings (key, value) VALUES ('x', 'y')")


def test_unknown_component_rejected(paths):
    with pytest.raises(ValueError):
        open_connection(paths.db, "superuser")


def test_source_cannot_be_enabled_without_terms_review(db):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.execute("INSERT INTO sources (kind, host, enabled) VALUES ('lever', 'api.lever.co', 1)")


def test_model_config_cannot_be_enabled_untested(db):
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        db.execute("INSERT INTO model_configs (name, provider_label, base_url, model_id, enabled) VALUES ('m', 'p', 'u', 'id', 1)")
