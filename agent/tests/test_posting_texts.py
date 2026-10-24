"""Posting text kept only while wanted (migration 0010, P1.10; Kevin: "no reason to keep posts that just
rejected and don't meet filters"). Each poll runs the real transaction: apply the listing, then filter."""
import pytest

from conftest import NOW
from jobforge_agent.db.connect import open_connection
from jobforge_agent.discovery import changes
from jobforge_agent.discovery.models import Employer, RawJob
from jobforge_agent.filtering import pipeline
from test_filter_pipeline import AUTH, PROFILE, _run, add_profile

PASS_TITLE, REJECT_TITLE = "NOC Analyst", "Senior NOC Analyst"


def _raw(native, title, desc="Monitor the network. Remote - US."):
    item = {"id": native, "title": title, "content": desc}
    return RawJob(ats="greenhouse", slug="acme", native_id=native, company="Acme", title=title, location_text="Remote - US",
                  listing_url=f"https://x/{native}", apply_url=f"https://x/{native}", description=desc,
                  description_raw=f"<p>{desc}</p>", item=item)


@pytest.fixture
def ctx(db, paths):
    emp = db.execute("INSERT INTO employers (source_id, slug, display_name) VALUES "
                     "((SELECT id FROM sources WHERE kind='greenhouse'), 'acme', 'Acme') RETURNING id").fetchone()[0]
    db.execute("INSERT INTO employer_poll_state (employer_id, next_poll_at_ms, etag) VALUES (?, ?, 'W/\"e1\"')", (emp, NOW + 10**9))
    disc = open_connection(paths.db, "discovery")
    employer = Employer(emp, "greenhouse", "acme", "Acme")

    def poll(raws, now=NOW):
        fetch = disc.execute("INSERT INTO source_fetches (employer_id, started_at_ms, finished_at_ms, outcome) "
                             "VALUES (?, ?, ?, 'OK') RETURNING id", (emp, now, now)).fetchone()[0]
        disc.execute("BEGIN IMMEDIATE")
        counts = changes.apply_listing(disc, employer, raws, fetch, now)
        stats = pipeline.filter_jobs(disc, counts.touched, now)
        disc.execute("COMMIT")
        return counts, stats
    yield db, disc, poll, emp
    disc.close()


def _kept(db) -> dict[str, bool]:
    """title -> is the current posting's text kept."""
    return {r[0]: bool(r[1]) for r in db.execute(
        "SELECT j.title, pt.raw_posting_id IS NOT NULL FROM jobs j JOIN raw_postings rp ON rp.id = j.current_raw_posting_id "
        "LEFT JOIN posting_texts pt ON pt.raw_posting_id = rp.id")}


def _verdicts(db) -> list[tuple[str, int, str]]:
    return [tuple(r) for r in db.execute("SELECT j.title, sp.version, fr.verdict FROM filter_results fr JOIN jobs j ON j.id = fr.job_id "
                                         "JOIN search_profiles sp ON sp.id = fr.search_profile_id ORDER BY fr.id")]


def test_rejected_postings_keep_their_record_but_not_their_text(ctx):
    db, _, poll, _ = ctx
    add_profile(db)
    _, stats = poll([_raw("1", PASS_TITLE), _raw("2", REJECT_TITLE)])
    assert (stats.passed, stats.rejected, stats.texts_dropped) == (1, 1, 1)
    assert _kept(db) == {PASS_TITLE: True, REJECT_TITLE: False}
    assert db.execute("SELECT count(*) FROM raw_postings").fetchone()[0] == 2  # identity, URLs, hashes stay


def test_no_search_profile_means_nothing_has_passed_so_nothing_is_kept(ctx):
    db, _, poll, _ = ctx
    _, stats = poll([_raw("1", PASS_TITLE), _raw("2", REJECT_TITLE)])
    assert stats is None and _kept(db) == {PASS_TITLE: False, REJECT_TITLE: False}
    # The first full fetch after a profile exists brings the text back and filters it.
    add_profile(db)
    counts, stats = poll([_raw("1", PASS_TITLE), _raw("2", REJECT_TITLE)])
    assert counts.text_restored == 2 and (stats.passed, stats.rejected) == (1, 1)
    assert _kept(db) == {PASS_TITLE: True, REJECT_TITLE: False}


def test_a_job_without_text_is_never_filtered_on_missing_text(ctx):
    """The rules read the description; a verdict on missing text would be wrong and, being unique per
    (posting, profile, ruleset), would stick."""
    db, disc, poll, _ = ctx
    add_profile(db)
    poll([_raw("1", REJECT_TITLE)])
    add_profile(db, {**PROFILE, "title": {**PROFILE["title"], "exclude_seniority": []}})  # v2 would pass it
    job = db.execute("SELECT id FROM jobs").fetchone()[0]
    disc.execute("BEGIN IMMEDIATE")
    stats = pipeline.filter_jobs(disc, [job], NOW)
    disc.execute("COMMIT")
    assert stats.waiting_for_text == 1 and _verdicts(db) == [(REJECT_TITLE, 1, "reject")]
    # Re-filter clears the board's ETag so the next poll is a full fetch, which restores and filters.
    disc.execute("BEGIN IMMEDIATE")
    assert pipeline.request_full_fetch(disc, NOW) == 1
    disc.execute("COMMIT")
    # ...and makes it due now instead of at its next scheduled poll (fixture: NOW + 10**9).
    assert tuple(db.execute("SELECT etag, next_poll_at_ms FROM employer_poll_state").fetchone()) == (None, NOW)
    counts, stats = poll([_raw("1", REJECT_TITLE)])
    assert counts.text_restored == 1 and stats.passed == 1
    assert _verdicts(db) == [(REJECT_TITLE, 1, "reject"), (REJECT_TITLE, 2, "pass")] and _kept(db) == {REJECT_TITLE: True}


def test_a_rejected_job_stays_without_text_and_its_board_keeps_its_etag(ctx):
    db, disc, poll, _ = ctx
    add_profile(db)
    poll([_raw("1", REJECT_TITLE)])
    counts, stats = poll([_raw("1", REJECT_TITLE)])
    assert counts.text_restored == 0 and counts.touched == [] and _kept(db) == {REJECT_TITLE: False}
    disc.execute("BEGIN IMMEDIATE")
    assert pipeline.request_full_fetch(disc, NOW) == 0
    disc.execute("COMMIT")
    assert tuple(db.execute("SELECT etag, next_poll_at_ms FROM employer_poll_state").fetchone()) == ('W/"e1"', NOW + 10**9)


def test_a_later_reject_drops_the_text_and_supersedes_pending_analysis(ctx):
    db, disc, poll, _ = ctx
    add_profile(db)  # migration 0003 seeds the default model config, so a pass queues an analysis
    _, stats = poll([_raw("1", PASS_TITLE)])
    assert stats.queued == 1
    add_profile(db, {**PROFILE, "title": {**PROFILE["title"], "include": ["systems administrator"]}})  # v2 rejects it
    job = db.execute("SELECT id FROM jobs").fetchone()[0]
    disc.execute("BEGIN IMMEDIATE")
    stats = pipeline.filter_jobs(disc, [job], NOW)
    disc.execute("COMMIT")
    assert stats.texts_dropped == 1 and _kept(db) == {PASS_TITLE: False}
    assert [r[0] for r in db.execute("SELECT status FROM posting_analyses")] == ["superseded"]


def test_evaluate_anyway_on_a_job_without_text_fetches_its_board_in_full(paths):
    async def check(client, runtime, _app):
        db = open_connection(runtime.paths.db, "migration")
        emp = db.execute("INSERT INTO employers (source_id, slug, display_name) VALUES "
                         "((SELECT id FROM sources WHERE kind='greenhouse'), 'acme', 'Acme') RETURNING id").fetchone()[0]
        db.execute("INSERT INTO employer_poll_state (employer_id, next_poll_at_ms, etag) VALUES (?, ?, 'W/\"e1\"')", (emp, NOW + 10**12))
        add_profile(db)
        disc = open_connection(runtime.paths.db, "discovery")
        fetch = disc.execute("INSERT INTO source_fetches (employer_id, started_at_ms, finished_at_ms, outcome) "
                             "VALUES (?, ?, ?, 'OK') RETURNING id", (emp, NOW, NOW)).fetchone()[0]
        disc.execute("BEGIN IMMEDIATE")
        counts = changes.apply_listing(disc, Employer(emp, "greenhouse", "acme", "Acme"), [_raw("1", REJECT_TITLE)], fetch, NOW)
        pipeline.filter_jobs(disc, counts.touched, NOW)
        disc.execute("COMMIT")
        job = db.execute("SELECT id FROM jobs").fetchone()[0]
        r = (await client.post(f"/v1/jobs/{job}/evaluate-anyway", headers=AUTH, json={"note": "t"})).json()
        assert r["queued"] is False and r["waiting_for_text"] is True
        etag, next_at = db.execute("SELECT etag, next_poll_at_ms FROM employer_poll_state").fetchone()
        assert etag is None and next_at < NOW + 10**12
        detail = (await client.get(f"/v1/jobs/{job}", headers=AUTH)).json()
        assert detail["description_text"] is None and detail["description_html"] is None and detail["override"] is True
        # The next poll stores the text (the override makes it wanted) and keeps it despite the reject.
        fetch = disc.execute("INSERT INTO source_fetches (employer_id, started_at_ms, finished_at_ms, outcome) "
                             "VALUES (?, ?, ?, 'OK') RETURNING id", (emp, NOW, NOW)).fetchone()[0]
        disc.execute("BEGIN IMMEDIATE")
        counts = changes.apply_listing(disc, Employer(emp, "greenhouse", "acme", "Acme"), [_raw("1", REJECT_TITLE)], fetch, NOW + 1)
        pipeline.filter_jobs(disc, counts.touched, NOW + 1)
        disc.execute("COMMIT")
        assert counts.text_restored == 1 and _kept(db) == {REJECT_TITLE: True}
        detail = (await client.get(f"/v1/jobs/{job}", headers=AUTH)).json()
        assert detail["description_text"].startswith("Monitor the network")
        disc.close()
        db.close()
    _run(paths, check)


def test_pre_0010_rows_keep_their_plain_text_and_are_read_unchanged(db):
    from conftest import seed_basics
    from jobforge_agent import views
    ids = seed_basics(db)
    db.execute("UPDATE jobs SET employer_id = ? WHERE id = ?", (ids["employer"], ids["job"]))
    assert pipeline.job_view(db, ids["job"])[3] is True  # plain text (even empty) counts as kept
    assert views.job_detail(db, ids["job"])["description_text"] == ""


def test_evaluation_sets_skip_jobs_whose_text_is_not_kept():
    from jobforge_agent.evaluation import store
    assert "posting_texts" in store._CANDIDATES and "posting_texts" in store._LATEST_DONE
