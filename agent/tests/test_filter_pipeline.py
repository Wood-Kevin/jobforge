"""Filter stage, analysis queueing, re-filter and "evaluate anyway" (ARCHITECTURE 3, 4.2; SPEC D9, D10).

Uses a small synthetic profile (no personal data); the scanner regression suite with the real
profile is test_filter_rules.py.
"""
import asyncio
import json

import pytest

from conftest import NOW, seed_basics
from jobforge_agent.db.connect import open_connection
from jobforge_agent.filtering import pipeline, rules
from jobforge_agent.filtering.profile import ProfileError, compile_profile, from_scanner_config
from test_discovery_scheduler import env, run  # noqa: F401  (fixture reused for the poll test)
from test_ipc import AUTH, _run

PROFILE = {
    "title": {"include": ["noc", "systems administrator"], "exclude_seniority": ["senior"], "seniority_exceptions": [],
              "exclude_non_it": ["sales"]},
    "location": {"home_metro": ["jacksonville"], "home_state_codes": ["FL"], "home_state_names": ["florida"],
                 "us_markers": ["us", "usa", "united states"], "us_state_markers": ["florida", "texas"],
                 "home_markers": ["florida"], "non_us_location_markers": ["canada"], "description_restricted_markers": [],
                 "home_excluded_markers": [], "residency_markers": []},
    "salary": {"floor_annual": 50000},
    "freshness": {"max_age_days": None},
}


def add_profile(conn, rules_=PROFILE) -> int:
    version = conn.execute("SELECT coalesce(max(version), 0) + 1 FROM search_profiles").fetchone()[0]
    return conn.execute("INSERT INTO search_profiles (version, rules, created_at_ms) VALUES (?, ?, ?) RETURNING id",
                        (version, json.dumps(rules_), NOW)).fetchone()[0]


def add_job(db, ids, n, title, location="Remote - US", desc="Monitor the network.", closed=False) -> tuple[int, int]:
    sid = f"greenhouse:acme:{n}"
    raw = db.execute(
        "INSERT INTO raw_postings (employer_id, source_job_id, fetched_at_ms, payload, description_text, payload_sha256, "
        "material_sha256, adapter_version) VALUES (?, ?, ?, '{}', ?, ?, ?, 'gh-1') RETURNING id",
        (ids["employer"], sid, NOW, desc, f"p{n}", f"m{n}")).fetchone()[0]
    job = db.execute(
        "INSERT INTO jobs (source_job_id, employer_id, current_raw_posting_id, last_payload_sha256, title, company, location_text, "
        "first_seen_at_ms, last_seen_at_ms, closed_at_ms) VALUES (?, ?, ?, ?, ?, 'Acme', ?, ?, ?, ?) RETURNING id",
        (sid, ids["employer"], raw, f"p{n}", title, location, NOW, NOW, NOW if closed else None)).fetchone()[0]
    return job, raw


def new_version(db, job, n, desc="Changed text."):
    sid = db.execute("SELECT source_job_id, employer_id FROM jobs WHERE id = ?", (job,)).fetchone()
    raw = db.execute(
        "INSERT INTO raw_postings (employer_id, source_job_id, fetched_at_ms, payload, description_text, payload_sha256, "
        "material_sha256, adapter_version) VALUES (?, ?, ?, '{}', ?, ?, ?, 'gh-1') RETURNING id",
        (sid[1], sid[0], NOW, desc, f"p{n}b", f"m{n}b")).fetchone()[0]
    db.execute("UPDATE jobs SET current_raw_posting_id = ? WHERE id = ?", (raw, job))
    return raw


@pytest.fixture
def ctx(db, paths):
    ids = seed_basics(db)
    disc = open_connection(paths.db, "discovery")
    yield db, disc, ids
    disc.close()


def filt(disc, job_ids):
    disc.execute("BEGIN IMMEDIATE")
    stats = pipeline.filter_jobs(disc, job_ids, NOW)
    disc.execute("COMMIT")
    return stats


def results(db, job):
    return [dict(r) for r in db.execute("SELECT * FROM filter_results WHERE job_id = ? ORDER BY id", (job,))]


def analyses(db, job):
    return [tuple(r) for r in db.execute("SELECT raw_posting_id, status FROM posting_analyses WHERE job_id = ? ORDER BY id", (job,))]


def test_no_profile_means_no_verdicts_yet(ctx):
    db, disc, ids = ctx
    job, _ = add_job(db, ids, 2, "NOC Analyst")
    assert filt(disc, [job]) is None
    assert results(db, job) == [] and analyses(db, job) == []


def test_pass_writes_verdict_and_queues_one_analysis_with_event(ctx):
    db, disc, ids = ctx
    add_profile(db)
    job, raw = add_job(db, ids, 2, "NOC Analyst")
    stats = filt(disc, [job])
    [r] = results(db, job)
    assert (r["verdict"], r["raw_posting_id"], r["ruleset_version"]) == ("pass", raw, rules.RULESET_VERSION)
    assert json.loads(r["reasons"])["primary"] is None
    assert analyses(db, job) == [(raw, "pending")] and stats.queued == 1
    default_model = db.execute("SELECT CAST(value AS INTEGER) FROM settings WHERE key = 'default_model_config_id'").fetchone()[0]
    assert db.execute("SELECT model_config_id FROM posting_analyses WHERE job_id = ?", (job,)).fetchone()[0] == default_model
    assert db.execute("SELECT count(*) FROM change_events WHERE type = 'job_queued_for_analysis' AND job_id = ?", (job,)).fetchone()[0] == 1


def test_reject_keeps_every_reason_and_queues_nothing(ctx):
    db, disc, ids = ctx
    add_profile(db)
    job, _ = add_job(db, ids, 2, "Senior NOC Analyst", location="Remote - Canada", desc="Pay: $15 - $18 per hour")
    filt(disc, [job])
    [r] = results(db, job)
    reasons = json.loads(r["reasons"])
    assert r["verdict"] == "reject" and reasons["primary"].startswith("TITLE_SENIORITY")
    assert [f["rule"] for f in reasons["failed"]] == ["TITLE_SENIORITY", "REMOTE_EXCLUDES_HOME", "SALARY_BELOW_FLOOR"]
    assert analyses(db, job) == []


def test_refiltering_same_version_is_idempotent(ctx):
    db, disc, ids = ctx
    add_profile(db)
    job, _ = add_job(db, ids, 2, "NOC Analyst")
    filt(disc, [job])
    stats = filt(disc, [job])
    assert (stats.filtered, stats.already_filtered, stats.queued) == (0, 1, 0)
    assert len(results(db, job)) == 1 and len(analyses(db, job)) == 1


def test_changed_posting_supersedes_the_pending_analysis(ctx):
    db, disc, ids = ctx
    add_profile(db)
    job, raw1 = add_job(db, ids, 2, "NOC Analyst")
    filt(disc, [job])
    raw2 = new_version(db, job, 2)
    filt(disc, [job])
    assert analyses(db, job) == [(raw1, "superseded"), (raw2, "pending")]
    assert [r["raw_posting_id"] for r in results(db, job)] == [raw1, raw2]  # old verdict kept


def test_analysis_already_done_for_this_version_is_not_requeued(ctx):
    db, disc, ids = ctx
    add_profile(db)
    job, raw = add_job(db, ids, 2, "NOC Analyst")
    filt(disc, [job])
    db.execute("UPDATE posting_analyses SET status = 'done' WHERE job_id = ?", (job,))
    add_profile(db, {**PROFILE, "salary": {"floor_annual": 40000}})  # new profile version: new verdict, same posting
    stats = filt(disc, [job])
    assert stats.filtered == 1 and stats.queued == 0
    assert analyses(db, job) == [(raw, "done")]


def test_closed_job_is_filtered_for_the_record_but_not_queued(ctx):
    db, disc, ids = ctx
    add_profile(db)
    job, _ = add_job(db, ids, 2, "NOC Analyst", closed=True)
    filt(disc, [job])
    assert results(db, job)[0]["verdict"] == "pass" and analyses(db, job) == []


def test_override_queues_a_rejected_job(ctx):
    db, disc, ids = ctx
    add_profile(db)
    job, raw = add_job(db, ids, 2, "Sales Engineer, NOC")
    db.execute("INSERT INTO filter_overrides (job_id, created_at_ms) VALUES (?, ?)", (job, NOW))
    stats = filt(disc, [job])
    assert results(db, job)[0]["verdict"] == "reject"  # the verdict stays honest
    assert analyses(db, job) == [(raw, "pending")] and stats.overrides_queued == 1


def test_no_default_model_counts_skips_instead_of_failing(ctx):
    db, disc, ids = ctx
    add_profile(db)
    db.execute("DELETE FROM settings WHERE key = 'default_model_config_id'")
    job, _ = add_job(db, ids, 2, "NOC Analyst")
    stats = filt(disc, [job])
    assert stats.skipped_no_model == 1 and analyses(db, job) == []


def test_new_profile_version_adds_rows_and_leaves_old_ones(ctx):
    db, disc, ids = ctx
    p1 = add_profile(db)
    job, _ = add_job(db, ids, 2, "Systems Administrator", location="Austin, TX")
    filt(disc, [job])
    before = results(db, job)
    p2 = add_profile(db, {**PROFILE, "location": {**PROFILE["location"], "us_state_markers": []}})
    filt(disc, [job])
    after = results(db, job)
    assert after[0] == before[0] and [r["search_profile_id"] for r in after] == [p1, p2]


# ----- profile validation and import -----

@pytest.mark.parametrize("bad, field", [
    (None, "rules must be an object"),
    ({"title": {"include": []}}, "title.include"),
    ({"title": ["noc"]}, "title must be an object"),
    ({"title": {"include": ["noc("]}}, "bad regex in title.include"),
    ({"title": {"include": ["noc"]}, "location": {"residency_markers": ["must reside in (.*)"]}}, "(?P<place>"),
    ({"title": {"include": ["noc"]}, "salary": {"floor_annual": "lots"}}, "salary.floor_annual"),
    ({"title": {"include": ["noc"]}, "freshness": {"max_age_days": 0}}, "freshness.max_age_days"),
])
def test_bad_profiles_name_the_field(bad, field):
    with pytest.raises(ProfileError, match=field.replace("(", r"\(").replace("?", r"\?")):
        compile_profile(bad)


def test_scanner_config_conversion_makes_home_explicit():
    raw = {"title": {"include": ["noc"]}, "location": {"jax_metro": ["jacksonville"], "florida_markers": ["florida"],
                                                       "florida_excluded_markers": ["excluding florida"]},
           "salary": {"floor_annual": 60000}}
    r = from_scanner_config(raw)
    assert r["location"]["home_metro"] == ["jacksonville"] and r["location"]["home_state_codes"] == ["FL", "GA"]
    assert r["location"]["home_excluded_markers"] == ["excluding florida"]
    compile_profile(r)


# ----- discovery poll filters in the same transaction -----

def test_poll_filters_new_postings_once_a_profile_exists(env):  # noqa: F811
    add_profile(env.db, {**PROFILE, "title": {**PROFILE["title"], "include": [".+"]}})
    env.add("cloudflare")
    run(env.poll_due())
    jobs = env.db.execute("SELECT count(*) FROM jobs").fetchone()[0]
    filtered = env.db.execute("SELECT count(DISTINCT job_id) FROM filter_results").fetchone()[0]
    assert jobs == 3 and filtered == 3


def test_poll_without_profile_stores_postings_unfiltered(env):  # noqa: F811
    env.add("cloudflare")
    run(env.poll_due())
    assert env.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 3
    assert env.db.execute("SELECT count(*) FROM filter_results").fetchone()[0] == 0


# ----- IPC: profile save, re-filter, evaluate anyway -----

def _seed_jobs(runtime):
    db = open_connection(runtime.paths.db, "migration")
    ids = seed_basics(db)
    db.execute("UPDATE jobs SET closed_at_ms = ? WHERE id = ?", (NOW, ids["job"]))  # a closed job that would pass
    keep, _ = add_job(db, ids, 2, "NOC Analyst")
    reject, raw = add_job(db, ids, 3, "Senior NOC Analyst")
    db.close()
    return keep, reject, raw, ids["job"]


async def _wait_refilter(runtime):
    while runtime._refilter_task is not None and not runtime._refilter_task.done():
        await asyncio.sleep(0.01)
    if runtime._refilter_task is not None:
        runtime._refilter_task.result()  # re-raise a failure here instead of losing it


def test_saving_a_profile_versions_it_and_refilters_everything(paths):
    async def check(client, runtime, _app):
        keep, reject, _, closed = _seed_jobs(runtime)
        assert (await client.get("/v1/search-profile", headers=AUTH)).status_code == 404
        r = await client.post("/v1/search-profile", headers=AUTH, json={"rules": PROFILE})
        assert r.status_code == 200 and r.json()["version"] == 1
        await _wait_refilter(runtime)
        r = await client.post("/v1/search-profile", headers=AUTH, json={"rules": {**PROFILE, "salary": {"floor_annual": 1}}})
        assert r.json()["version"] == 2
        await _wait_refilter(runtime)
        got = (await client.get("/v1/search-profile", headers=AUTH)).json()
        assert got["version"] == 2 and got["rules"]["salary"]["floor_annual"] == 1
        rows = runtime.reader.execute("SELECT job_id, search_profile_id, verdict FROM filter_results ORDER BY id").fetchall()
        # Every stored posting, closed included, has a verdict under both versions; v1 rows untouched.
        assert len(rows) == 6 and {(r[0], r[2]) for r in rows} == {(keep, "pass"), (reject, "reject"), (closed, "pass")}
        queued = {r[0] for r in runtime.reader.execute("SELECT job_id FROM posting_analyses")}
        assert queued == {keep}  # the closed pass is recorded, never queued
        summary = (await client.get("/v1/filter/summary", headers=AUTH)).json()
        assert summary["verdicts"] == {"pass": 1, "reject": 1} and summary["rejected_by_primary_category"] == {"seniority": 1}
        done = [json.loads(e["ref"]) for e in runtime.events_after(0) if e["type"] == "refilter_done"]
        assert [d["new_results"] for d in done] == [3, 3]
    _run(paths, check)


def test_invalid_profile_is_422_and_saves_nothing(paths):
    async def check(client, runtime, _app):
        r = await client.post("/v1/search-profile", headers=AUTH, json={"rules": {"title": {"include": []}}})
        assert r.status_code == 422 and "title.include" in r.json()["error"]
        assert runtime.reader.execute("SELECT count(*) FROM search_profiles").fetchone()[0] == 0
    _run(paths, check)


def test_refilter_requests_coalesce_and_a_mid_run_request_gets_one_follow_up(paths, monkeypatch):
    from jobforge_agent import agent as agent_mod
    monkeypatch.setattr(agent_mod, "REFILTER_CHUNK", 1)  # yield after every job so a request can land mid-run

    async def check(client, runtime, _app):
        _seed_jobs(runtime)
        await client.post("/v1/search-profile", headers=AUTH, json={"rules": PROFILE})
        await _wait_refilter(runtime)

        def runs():
            return [json.loads(e["ref"]) for e in runtime.events_after(0) if e["type"] == "refilter_done"]

        # Before the task starts: it has not read anything yet, so one run covers all three.
        burst = [runtime.start_refilter() for _ in range(3)]
        await _wait_refilter(runtime)
        assert burst == ["started", "queued", "queued"] and len(runs()) == 2

        # After the run has read its job list: exactly one follow-up run, however many requests.
        assert (await client.post("/v1/admin/refilter", headers=AUTH)).json() == {"refilter": "started"}
        await _wait_refilter(runtime)
        assert len(runs()) == 3
        assert runtime.start_refilter() == "started"
        await asyncio.sleep(0)  # the run reads its job list, filters one job, yields
        assert not runtime._refilter_task.done()
        mid = [runtime.start_refilter() for _ in range(2)]
        await _wait_refilter(runtime)
        assert mid == ["queued", "queued"] and len(runs()) == 5
        assert runs()[-1]["new_results"] == 0 and runs()[-1]["already_filtered"] == 3
    _run(paths, check)


def test_evaluate_anyway_queues_a_rejected_job(paths):
    async def check(client, runtime, _app):
        _, reject, raw, _ = _seed_jobs(runtime)
        await client.post("/v1/search-profile", headers=AUTH, json={"rules": PROFILE})
        await _wait_refilter(runtime)
        r = await client.post(f"/v1/jobs/{reject}/evaluate-anyway", headers=AUTH, json={"note": "lead role is fine"})
        assert r.status_code == 200 and r.json()["queued"] is True
        assert runtime.reader.execute("SELECT note FROM filter_overrides WHERE job_id = ?", (reject,)).fetchone()[0] == "lead role is fine"
        assert [tuple(r) for r in runtime.reader.execute("SELECT raw_posting_id, status FROM posting_analyses WHERE job_id = ?", (reject,)).fetchall()] == [(raw, "pending")]
        again = await client.post(f"/v1/jobs/{reject}/evaluate-anyway", headers=AUTH)
        assert again.json()["queued"] is False  # same posting version already queued
        assert (await client.post("/v1/jobs/999999/evaluate-anyway", headers=AUTH)).status_code == 404
    _run(paths, check)
