"""Change detection (ARCHITECTURE 3.3): new / material / payload-only / unchanged / closed / reopened."""
import copy

import pytest

from conftest import NOW
from jobforge_agent.db.connect import open_connection
from jobforge_agent.discovery import changes
from jobforge_agent.discovery.models import Employer, RawJob


def _raw(native="1", title="NOC Engineer", desc="Monitor networks. Requirements: CCNA.", extra=None):
    item = {"id": native, "title": title, "content": desc, "updated_at": "2026-10-01"}
    item.update(extra or {})
    return RawJob(ats="greenhouse", slug="acme", native_id=native, company="Acme", title=title, location_text="Remote",
                  listing_url=f"https://x/{native}", apply_url=f"https://x/{native}?gh_src=abc", description=desc,
                  description_raw=f"<p>{desc}</p>", item=item)


@pytest.fixture
def ctx(db, paths):
    emp_id = db.execute("INSERT INTO employers (source_id, slug, display_name) VALUES ((SELECT id FROM sources WHERE kind='greenhouse'), 'acme', 'Acme') RETURNING id").fetchone()[0]
    conn = open_connection(paths.db, "discovery")
    employer = Employer(emp_id, "greenhouse", "acme", "Acme")
    seq = {"n": 0}

    def apply(raws, now=NOW):
        seq["n"] += 1
        fetch = conn.execute("INSERT INTO source_fetches (employer_id, started_at_ms, finished_at_ms, outcome) VALUES (?, ?, ?, 'OK') RETURNING id",
                             (emp_id, now, now)).fetchone()[0]
        conn.execute("BEGIN IMMEDIATE")
        counts = changes.apply_listing(conn, employer, raws, fetch, now)
        conn.execute("COMMIT")
        return counts
    yield db, conn, apply, emp_id
    conn.close()


def _count(db, table):
    return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


TEXT = "coalesce(rp.description_text, unpack_text(pt.description_text_z))"
FROM = "raw_postings rp LEFT JOIN posting_texts pt ON pt.raw_posting_id = rp.id"


def test_new_postings_store_compressed_text_and_no_payload(ctx):
    """Migration 0010 (P1.10 storage): the description goes to posting_texts, compressed, and the API
    object is not kept (payload_sha256 still is). Dropping it is the filter's job (test_filter_pipeline)."""
    db, _, apply, _ = ctx
    apply([_raw()])
    row = db.execute(f"SELECT rp.payload, rp.payload_sha256, rp.description_raw, rp.description_text, pt.description_raw_z, "
                     f"{TEXT}, coalesce(rp.description_raw, unpack_text(pt.description_raw_z)) FROM {FROM}").fetchone()
    assert row[0] == "null" and len(row[1]) == 64
    assert row[2] is None and row[3] is None and isinstance(row[4], bytes)
    assert row[5] == "Monitor networks. Requirements: CCNA." and row[6] == "<p>Monitor networks. Requirements: CCNA.</p>"


def test_new_posting_creates_raw_job_and_event(ctx):
    db, _, apply, emp = ctx
    c = apply([_raw()])
    assert (c.new, _count(db, "raw_postings"), _count(db, "jobs")) == (1, 1, 1)
    assert db.execute("SELECT employer_id FROM jobs").fetchone()[0] == emp
    assert [r[0] for r in db.execute("SELECT type FROM change_events")] == ["posting_new"]


def test_unchanged_listing_writes_no_raw_row_and_moves_last_seen(ctx):
    db, _, apply, _ = ctx
    apply([_raw()])
    c = apply([_raw()], now=NOW + 900_000)
    assert c.unchanged == 1 and _count(db, "raw_postings") == 1
    assert db.execute("SELECT last_seen_at_ms FROM jobs").fetchone()[0] == NOW + 900_000
    assert _count(db, "change_events") == 1


def test_volatile_field_change_is_not_material(ctx):
    db, _, apply, _ = ctx
    apply([_raw()])
    c = apply([_raw(extra={"updated_at": "2026-10-21", "internal_job_id": 99})])
    assert (c.payload_only, c.changed) == (1, 0)
    assert _count(db, "raw_postings") == 1


def test_tracking_parameter_change_is_not_material(ctx):
    db, _, apply, _ = ctx
    apply([_raw()])
    r = _raw()
    r.apply_url = "https://x/1?gh_src=zzz&utm_source=mail"
    assert apply([r]).changed == 0


def test_material_change_appends_raw_row_and_keeps_the_old_one(ctx):
    db, _, apply, _ = ctx
    apply([_raw()])
    first = db.execute(f"SELECT rp.id, {TEXT} FROM {FROM}").fetchone()
    c = apply([_raw(desc="Monitor networks. Requirements: CCNA and 2 years NOC.")])
    assert c.changed == 1 and _count(db, "raw_postings") == 2
    current = db.execute("SELECT current_raw_posting_id FROM jobs").fetchone()[0]
    assert current != first[0]
    assert db.execute(f"SELECT {TEXT} FROM {FROM} WHERE rp.id = ?", (first[0],)).fetchone()[0] == first[1]
    assert [r[0] for r in db.execute("SELECT type FROM change_events ORDER BY id")] == ["posting_new", "posting_changed"]


def test_revert_to_earlier_version_reuses_its_raw_row(ctx):
    db, _, apply, _ = ctx
    apply([_raw()])
    apply([_raw(desc="Changed text.")])
    apply([_raw()])
    assert _count(db, "raw_postings") == 2
    assert db.execute("SELECT current_raw_posting_id FROM jobs").fetchone()[0] == db.execute("SELECT min(id) FROM raw_postings").fetchone()[0]


def test_closes_only_after_two_complete_listings_without_it_and_reopens(ctx):
    db, _, apply, _ = ctx
    apply([_raw("1"), _raw("2", title="SOC Analyst")])
    apply([_raw("1")])
    assert db.execute("SELECT closed_at_ms, missing_polls FROM jobs WHERE source_job_id LIKE '%:2'").fetchone()[0] is None
    c = apply([_raw("1")], now=NOW + 1)
    assert c.closed == 1
    assert db.execute("SELECT closed_at_ms FROM jobs WHERE source_job_id LIKE '%:2'").fetchone()[0] == NOW + 1
    c = apply([_raw("1"), _raw("2", title="SOC Analyst")])
    assert c.reopened == 1
    assert tuple(db.execute("SELECT closed_at_ms, missing_polls FROM jobs WHERE source_job_id LIKE '%:2'").fetchone()) == (None, 0)
    types = [r[0] for r in db.execute("SELECT type FROM change_events ORDER BY id")]
    assert types[-2:] == ["job_closed", "job_reopened"]


def test_not_modified_never_closes(ctx):
    db, conn, apply, emp = ctx
    apply([_raw("1")])
    for i in range(3):
        changes.touch_unchanged_board(conn, emp, NOW + i)
    assert tuple(db.execute("SELECT closed_at_ms, missing_polls, last_seen_at_ms FROM jobs").fetchone()) == (None, 0, NOW + 2)


def test_collision_raises_and_writes_nothing(ctx):
    db, conn, apply, _ = ctx
    with pytest.raises(changes.CollisionError):
        apply([_raw("1", title="NOC Engineer"), _raw("1", title="Sales Manager")])
    conn.execute("ROLLBACK")
    assert _count(db, "jobs") == 0


def test_identical_duplicates_are_collapsed_not_an_error(ctx):
    db, _, apply, _ = ctx
    assert apply([_raw("1"), copy.deepcopy(_raw("1"))]).new == 1


def test_strip_tracking_keeps_meaningful_query():
    assert changes.strip_tracking("https://x/j?gh_jid=5&gh_src=a&utm_campaign=b") == "https://x/j?gh_jid=5"
