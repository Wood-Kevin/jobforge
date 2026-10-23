"""Append-only history is enforced by triggers on every connection (SPEC D9)."""
import json
import sqlite3
import time

import pytest

from conftest import NOW, seed_basics
from jobforge_agent.db import migrate


def _seed_append_only(db):
    ids = seed_basics(db)
    db.execute("INSERT INTO search_profiles (version, rules, created_at_ms) VALUES (1, '{}', ?)", (NOW,))
    snap = db.execute("INSERT INTO profile_snapshots (sha256, completeness, created_at_ms) VALUES ('s', '{}', ?) RETURNING id", (NOW,)).fetchone()[0]
    db.execute("INSERT INTO profile_snapshot_items (snapshot_id, item_id, kind, statement, status, attributes) VALUES (?, 1, 'cert', 'CCNA', 'self_reported', '{}')", (snap,))
    app = db.execute("INSERT INTO applications (job_id, status, created_at_ms) VALUES (?, 'saved', ?) RETURNING id", (ids["job"], NOW)).fetchone()[0]
    db.execute("INSERT INTO application_events (application_id, to_status, at_ms) VALUES (?, 'saved', ?)", (app, NOW))
    raw = db.execute("SELECT current_raw_posting_id FROM jobs WHERE id = ?", (ids["job"],)).fetchone()[0]
    db.execute("INSERT INTO golden_labels (job_id, raw_posting_id, recommendation, labeled_by, created_at_ms) "
               "VALUES (?, ?, 'APPLY', 'user', ?)", (ids["job"], raw, NOW))
    return ids


UPDATES = {
    "raw_postings": "UPDATE raw_postings SET description_text = 'edited'",
    "search_profiles": "UPDATE search_profiles SET rules = ?",
    "profile_snapshots": "UPDATE profile_snapshots SET sha256 = 'x'",
    "profile_snapshot_items": "UPDATE profile_snapshot_items SET status = 'verified'",
    "application_events": "UPDATE application_events SET to_status = 'offer'",
    "golden_labels": "UPDATE golden_labels SET recommendation = 'SKIP'",
}


@pytest.mark.parametrize("table", sorted(UPDATES))
def test_update_and_delete_abort_even_for_the_owner_connection(db, table):
    _seed_append_only(db)
    sql = UPDATES[table]
    params = (json.dumps({"x": 1}),) if "?" in sql else ()
    with pytest.raises(sqlite3.IntegrityError, match=f"{table} is append-only"):
        db.execute(sql, params)
    with pytest.raises(sqlite3.IntegrityError, match=f"{table} is append-only"):
        db.execute(f"DELETE FROM {table}")


def test_change_events_prune_only_after_seven_days(db):
    now = int(time.time() * 1000)
    db.execute("INSERT INTO change_events (type, created_at_ms) VALUES ('posting_new', ?)", (now,))
    db.execute("INSERT INTO change_events (type, created_at_ms) VALUES ('posting_new', ?)", (now - 8 * 86_400_000,))
    with pytest.raises(sqlite3.IntegrityError, match="younger than 7 days"):
        db.execute("DELETE FROM change_events WHERE created_at_ms = ?", (now,))
    db.execute("DELETE FROM change_events WHERE created_at_ms < ?", (now - 7 * 86_400_000,))
    assert db.execute("SELECT count(*) FROM change_events").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        db.execute("UPDATE change_events SET type = 'x'")


def test_all_append_only_triggers_present_after_migration(db):
    migrate.check_append_only_triggers(db)


def test_missing_trigger_is_detected(db):
    db.execute("DROP TRIGGER raw_postings_no_delete")
    with pytest.raises(migrate.MigrationFailed, match="raw_postings_no_delete"):
        migrate.check_append_only_triggers(db)


def test_table_since_version_matches_the_migration_that_creates_it():
    """The trigger check skips tables newer than the database; that needs the right version per table."""
    import re
    created = {}
    for mg in migrate.bundled_migrations():
        for name in re.findall(r"CREATE TABLE (\w+)", mg.sql):
            created.setdefault(name, mg.version)
    for table in migrate.APPEND_ONLY_TRIGGERS:
        assert created[table] == migrate.TABLE_SINCE_VERSION.get(table, 1), table
