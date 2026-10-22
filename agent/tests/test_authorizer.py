"""SQLite authorizers replace the Postgres role grants (ARCHITECTURE 4.3)."""
import sqlite3

import pytest

from conftest import NOW, seed_basics
from jobforge_agent.db.connect import ALL_COLUMNS, GRANTS, open_connection

FORBIDDEN = [
    # The core guarantee: nothing but explicit user actions writes or verifies experience.
    ("evaluation", f"UPDATE experience_items SET status = 'verified', verified_at_ms = {NOW} WHERE id = 1"),
    ("evaluation", "INSERT INTO experience_items (kind, statement, source) VALUES ('skill', 'Ansible', 'model')"),
    ("discovery", f"UPDATE experience_items SET status = 'verified', verified_at_ms = {NOW} WHERE id = 1"),
    ("lifecycle", f"UPDATE experience_items SET status = 'verified', verified_at_ms = {NOW} WHERE id = 1"),
    ("readonly", "DELETE FROM experience_items"),
    ("evaluation", "UPDATE inferred_claims SET state = 'accepted'"),
    ("evaluation", "UPDATE profile_completeness SET cert_list_complete = 1"),
    # Applications are user-only.
    ("discovery", f"INSERT INTO applications (job_id, status, created_at_ms) VALUES (1, 'applied', {NOW})"),
    ("evaluation", f"INSERT INTO applications (job_id, status, created_at_ms) VALUES (1, 'applied', {NOW})"),
    # Column-level limits.
    ("discovery", "UPDATE sources SET default_interval_s = 5"),
    ("discovery", "UPDATE sources SET enabled = 1"),
    ("discovery", "UPDATE employers SET slug = 'x'"),
    ("lifecycle", "UPDATE source_fetches SET employer_id = 2"),
    # Settings and model configs are user decisions.
    ("discovery", "UPDATE settings SET value = '1' WHERE key = 'discovery_paused'"),
    ("evaluation", "UPDATE model_configs SET enabled = 1"),
    # Deletes are rare and explicit.
    ("user", "DELETE FROM jobs"),
    ("discovery", "DELETE FROM raw_postings"),
]


@pytest.mark.parametrize("component,sql", FORBIDDEN)
def test_forbidden_writes_are_denied(db, paths, component, sql):
    seed_basics(db)
    conn = open_connection(paths.db, component, readonly=(component == "readonly"))
    with pytest.raises(sqlite3.DatabaseError, match="not authorized|readonly"):
        conn.execute(sql)
    conn.close()


@pytest.mark.parametrize("component", [c for c in GRANTS])
def test_ddl_and_pragma_writes_denied(db, paths, component):
    conn = open_connection(paths.db, component, readonly=(component == "readonly"))
    for sql in ("CREATE TABLE evil (a INTEGER)", "DROP TRIGGER raw_postings_no_update",
                "ALTER TABLE jobs ADD COLUMN x TEXT", "PRAGMA foreign_keys = OFF",
                "ATTACH DATABASE ':memory:' AS other"):
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|readonly"):
            conn.execute(sql)
    conn.close()


def test_allowed_writes_succeed(db, paths):
    ids = seed_basics(db)
    disc = open_connection(paths.db, "discovery")
    disc.execute("UPDATE sources SET backoff_until_ms = ?, backoff_reason = '429' WHERE id = ?", (NOW, ids["source"]))
    disc.execute("UPDATE employers SET status = 'dead' WHERE id = ?", (ids["employer"],))
    user = open_connection(paths.db, "user")
    user.execute("UPDATE experience_items SET status = 'verified', verified_at_ms = ? WHERE id = ?", (NOW, ids["item"]))
    user.execute("UPDATE settings SET value = '1' WHERE key = 'discovery_paused'")
    ev = open_connection(paths.db, "evaluation")
    ev.execute("INSERT INTO inferred_claims (statement, kind, created_at_ms) VALUES ('DNS troubleshooting', 'skill', ?)", (NOW,))
    assert db.execute("SELECT status FROM experience_items WHERE id = ?", (ids["item"],)).fetchone()[0] == "verified"
    for c in (disc, user, ev):
        c.close()


def test_grants_name_real_tables_and_columns(db):
    """A typo in an allow-list would silently deny (or, worse, never be exercised)."""
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    for component, grants in GRANTS.items():
        for table, grant in grants.items():
            assert table in tables, f"{component}: unknown table {table}"
            if grant.update not in (False, ALL_COLUMNS):
                cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                assert grant.update <= cols, f"{component}.{table}: unknown columns {grant.update - cols}"


def test_every_table_has_a_writer(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    written = {t for grants in GRANTS.values() for t, g in grants.items() if g.insert or g.update is not False}
    assert tables - written == set(), "tables nobody can write (add a grant or remove the table)"
