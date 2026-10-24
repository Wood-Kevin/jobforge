"""Evaluation E3-E6 against a real database and a fake model server (ARCHITECTURE 3.4, 4.3, 6.4; SPEC D2, D10).

The fake server (test_analysis.FakeServer) scripts the E1 answer, then the E3 answer. Profile
items are created through the profile code on the user-action connection, as the UI does.
"""
import asyncio
import json
import time

import httpx
import pytest

from conftest import NOW, seed_basics
from jobforge_agent.analysis.worker import AnalysisWorker
from jobforge_agent.db.connect import open_connection
from jobforge_agent.db import recovery
from jobforge_agent.evaluation import match, store
from jobforge_agent.evaluation.versions import VERDICT_RULE_VERSION
from jobforge_agent.evaluation.worker import EvaluationWorker
from jobforge_agent.inference.client import InferenceClient
from jobforge_agent.profile import items as profile_items
from test_analysis import MODEL, Clock, FakeServer, _run_with_fake, item, logged
from test_filter_pipeline import PROFILE, add_job
from test_ipc import AUTH

POSTING = ("Requirements:\n- Must be a US citizen.\n- 3+ years of Linux administration experience.\n"
           "- Experience with network monitoring tools.\n- Familiarity with ticketing systems.\n- CCNA certification is a plus.\n\n"
           "Benefits:\n- We pay for AWS certifications.")
E1 = {"requirements": [item("Must be a US citizen", importance="hard", eligibility_type="work_authorization"),
                       item("3+ years of Linux administration experience", min_years=3),
                       item("Experience with network monitoring tools"),
                       item("Familiarity with ticketing systems"),
                       item("CCNA certification is a plus", importance="preferred"),
                       item("We pay for AWS certifications", subject="benefit")]}
SEARCH = {**PROFILE, "opportunity": {"work_modes": {"remote": "favorable"}, "role_families": {"favorable": ["noc"]}}}


def e3(*matches, claims=()):
    return {"matches": [{"requirement": r, "match_type": t, "cited_items": list(c), "bridge": b, "transfer_limits": None,
                         "gap_detail": g} for r, t, c, b, g in matches],
            "inferred_claims": [{"statement": s, "kind": "skill", "rationale": "likely", "requirements": ["R3"]} for s in claims]}


class Env:
    def __init__(self, db, paths, ids):
        self.db, self.paths, self.ids = db, paths, ids
        self.ev = open_connection(paths.db, "evaluation")
        self.user = open_connection(paths.db, "user")
        self.clients = []
        self.clock = Clock()

    def user_tx(self, fn, *args):
        self.user.execute("BEGIN IMMEDIATE")
        try:
            out = fn(self.user, *args)
            self.user.execute("COMMIT")
        except Exception:
            self.user.execute("ROLLBACK")
            raise
        return out

    def workers(self, server):
        server.watch += [self.ev, self.user]
        client = InferenceClient(httpx.MockTransport(server))
        self.clients.append(client)
        a = AnalysisWorker(self.ev, client, now_ms=self.clock)
        e = EvaluationWorker(self.ev, client, a.gate, now_ms=self.clock)
        return a, e

    def item(self, **fields):
        return self.user_tx(profile_items.create_item, {"source": "user", **fields})

    def verify(self, item_id):
        return self.user_tx(profile_items.verify_item, item_id, None, NOW)

    def analyze(self, server):
        a, e = self.workers(server)
        assert asyncio.run(a.run_once()) == "done"
        return a, e


@pytest.fixture
def env(db, paths):
    ids = seed_basics(db)
    db.execute("UPDATE model_configs SET model_id = ?, tested_at_ms = ?, enabled = 1, "
               "params = '{\"max_tokens\": 4000, \"temperature\": 0, \"seed\": 7}' WHERE id = ?", (MODEL, NOW, ids["model"]))
    db.execute("UPDATE settings SET value = ? WHERE key = 'default_model_config_id'", (str(ids["model"]),))
    db.execute("DELETE FROM experience_items")  # seed_basics adds one; each test builds its own profile
    ids["profile"] = db.execute("INSERT INTO search_profiles (version, rules, created_at_ms) VALUES (1, ?, ?) RETURNING id",
                                (json.dumps(SEARCH), NOW)).fetchone()[0]
    job, raw = add_job(db, ids, 7, "NOC Engineer", desc=POSTING)
    ids.update(job=job, raw=raw)
    db.execute("INSERT INTO filter_results (job_id, raw_posting_id, search_profile_id, ruleset_version, verdict, reasons, created_at_ms) "
               "VALUES (?, ?, ?, (SELECT ?), 'pass', '{}', ?)", (job, raw, ids["profile"], _ruleset(), NOW))
    db.execute("INSERT INTO posting_analyses (raw_posting_id, job_id, model_config_id, extraction_prompt_version, segmenter_version, "
               "interpreter_version, created_at_ms) VALUES (?, ?, ?, 'e1-v1', 'seg-v1', 'interp-v1', ?)", (raw, job, ids["model"], NOW))
    e = Env(db, paths, ids)
    yield e
    for c in e.clients:
        asyncio.run(c.aclose())
    e.ev.close()
    e.user.close()


def _ruleset():
    from jobforge_agent.filtering.rules import RULESET_VERSION
    return RULESET_VERSION


def profile_basic(env):
    role = env.item(kind="role", statement="NOC Tier I engineer", start_date="2024-10", end_date="2025-09")
    env.verify(role)
    skill = env.item(kind="skill", statement="Zabbix and SolarWinds network monitoring")
    fact = env.item(kind="eligibility", statement="US citizen",
                    attributes={"type": "work_authorization", "us_citizen": True, "authorized_to_work_us": True})
    env.verify(fact)
    return role, skill, fact


def evals(db):
    return [dict(r) for r in db.execute("SELECT * FROM evaluations ORDER BY id")]


def matches(db, eid):
    out = {}
    for r in db.execute("SELECT rm.*, rq.jd_quote FROM requirement_matches rm JOIN requirements rq ON rq.id = rm.requirement_id "
                        "WHERE rm.evaluation_id = ?", (eid,)):
        d = dict(r)
        d["validation_flags"] = json.loads(d["validation_flags"])
        d["citations"] = [c[0] for c in env_db_citations(db, d["id"])]
        out[d["jd_quote"]] = d
    return out


def env_db_citations(db, mid):
    return db.execute("SELECT item_id FROM match_citations WHERE requirement_match_id = ? ORDER BY item_id", (mid,)).fetchall()


def flags(m):
    return {f["flag"] for f in m["validation_flags"]}


def test_end_to_end_recommendation_with_evidence_and_no_open_transaction(env, caplog, capsys):
    role, skill, fact = profile_basic(env)
    answer = e3(("R1", "direct", [f"P{role}"], None, None),           # Linux 3+ years: the role shows 1 year
                ("R2", "direct", [f"P{skill}"], None, None),
                ("R3", "transferable", [f"P{role}"], "NOC tickets in ServiceNow", None),
                ("R4", "unknown", [], None, None),
                claims=["ServiceNow ticketing"])
    server = FakeServer(("ok", E1), ("ok", answer))
    _, e = env.analyze(server)
    assert asyncio.run(e.run_once()) == "done"
    (ev,) = evals(env.db)
    assert (ev["status"], ev["attempts"], ev["match_prompt_version"], ev["model_id_reported"], ev["requested_by"]) == (
        "done", 1, "m1-v1", MODEL, "pipeline")
    ms = matches(env.db, ev["id"])
    citizen = ms["Must be a US citizen"]
    assert (citizen["matched_by"], citizen["match_type"], citizen["basis"], citizen["citations"]) == ("eligibility_fact", "direct", "verified", [fact])
    linux = ms["3+ years of Linux administration experience"]
    assert (linux["match_type"], linux["gap_materiality"]) == ("gap", "minor")  # 2 years short of 3: within tolerance
    assert "years_short_override" in flags(linux) and json.loads(linux["gap_size"])["years_shown"] == 1.0
    mon = ms["Experience with network monitoring tools"]
    assert (mon["match_type"], mon["basis"], mon["citations"]) == ("direct", "self_reported", [skill])
    tick = ms["Familiarity with ticketing systems"]
    assert (tick["match_type"], tick["bridge"], tick["basis"]) == ("transferable", "NOC tickets in ServiceNow", "verified")
    assert ms["CCNA certification is a plus"]["match_type"] == "unknown"
    assert "We pay for AWS certifications" not in ms  # context rows are not matched
    claim = env.db.execute("SELECT statement, state, proposed_by_evaluation_id, evidence FROM inferred_claims").fetchone()
    assert tuple(claim)[:3] == ("ServiceNow ticketing", "open", ev["id"])
    assert json.loads(claim[3]) == {"requirement_ids": [tick["requirement_id"]]}  # R3 mapped to the requirement id

    rec = dict(env.db.execute("SELECT * FROM recommendations").fetchone())
    assert (rec["evaluation_id"], rec["search_profile_id"], rec["verdict_rule_version"], rec["eligibility"]) == (
        ev["id"], env.ids["profile"], VERDICT_RULE_VERSION, "ELIGIBLE")
    assert rec["fit"] == "COMPETITIVE" and rec["recommendation"] == "APPLY"  # 1 minor gap, 1 transferable, 0 unknown negotiable
    assert json.loads(rec["fit_counts"])["self_reported_covering"] == 1
    assert "self-reported" in json.loads(rec["decisive_reasons"])[0]["detail"]

    # The eligibility row never reached the model; the profile's eligibility facts neither.
    body = json.loads(server.requests[-1].content)
    prompt = body["messages"][1]["content"]
    assert "US citizen" not in prompt and "Linux administration" in prompt and f"P{role} [role; 2024-10 to 2025-09]" in prompt
    assert server.in_tx_during_call == [False, False]
    types = [r[0] for r in env.db.execute("SELECT type FROM change_events ORDER BY id")]
    assert types[-3:] == ["evaluations_queued", "evaluation_done", "recommendation_computed"]
    out = logged(caplog, capsys)
    assert "evaluation_done" in out
    for text in ("NOC Tier I", "Zabbix", "ServiceNow", "Linux administration"):
        assert text not in out  # logs carry ids and counts only (ARCHITECTURE 8)


def test_negotiable_eligibility_row_is_matched_by_code_not_the_model(env):
    """"Ability to obtain" is negotiable, so it is a fit requirement, but it is still checked
    against the clearance fact by code; the model never sees it."""
    db, ids = env.db, env.ids
    posting = ("Requirements:\n- Must be able to obtain a Secret clearance.\n- 2+ years of help desk experience.\n"
               "- Experience with Active Directory.\n- Experience with ticketing systems.")
    job, raw = add_job(db, ids, 8, "NOC Engineer", desc=posting)
    db.execute("INSERT INTO filter_results (job_id, raw_posting_id, search_profile_id, ruleset_version, verdict, reasons, created_at_ms) "
               "VALUES (?, ?, ?, ?, 'pass', '{}', ?)", (job, raw, ids["profile"], _ruleset(), NOW))
    db.execute("DELETE FROM posting_analyses")
    db.execute("INSERT INTO posting_analyses (raw_posting_id, job_id, model_config_id, extraction_prompt_version, segmenter_version, "
               "interpreter_version, created_at_ms) VALUES (?, ?, ?, 'e1-v1', 'seg-v1', 'interp-v1', ?)", (raw, job, ids["model"], NOW))
    role = env.item(kind="role", statement="Help desk technician", start_date="2022-01", end_date="2025-01")
    fact = env.item(kind="eligibility", statement="clearance", attributes={"type": "clearance", "level": "top_secret", "status": "inactive"})
    env.verify(fact)
    e1 = {"requirements": [item("Must be able to obtain a Secret clearance", importance="hard", eligibility_type="clearance"),
                           item("2+ years of help desk experience", min_years=2), item("Experience with Active Directory"),
                           item("Experience with ticketing systems")]}
    server = FakeServer(("ok", e1), ("ok", e3(("R1", "direct", [f"P{role}"], None, None))))
    _, e = env.analyze(server)
    assert asyncio.run(e.run_once()) == "done"
    (ev,) = evals(db)
    ms = matches(db, ev["id"])
    clearance = ms["Must be able to obtain a Secret clearance"]
    assert (clearance["matched_by"], clearance["match_type"], clearance["citations"]) == ("eligibility_fact", "direct", [fact])
    prompt = json.loads(server.requests[-1].content)["messages"][1]["content"]
    assert "clearance" not in prompt.lower() and "R1 [negotiable, min 2 years] 2+ years of help desk experience" in prompt


def test_rules_v2_checks_a_type_only_conflict_under_both_readings(env):
    """Lyft-style "must be based in the US", typed work_authorization by the model (P1.6 live)."""
    db, ids = env.db, env.ids
    posting = ("Requirements:\n- Candidates for this role must be based in the US.\n- 1+ years of help desk experience.\n"
               "- Experience with Active Directory.\n- Experience with ticketing systems.")
    job, raw = add_job(db, ids, 9, "NOC Engineer", desc=posting)
    db.execute("INSERT INTO filter_results (job_id, raw_posting_id, search_profile_id, ruleset_version, verdict, reasons, created_at_ms) "
               "VALUES (?, ?, ?, ?, 'pass', '{}', ?)", (job, raw, ids["profile"], _ruleset(), NOW))
    db.execute("DELETE FROM posting_analyses")
    db.execute("INSERT INTO posting_analyses (raw_posting_id, job_id, model_config_id, extraction_prompt_version, segmenter_version, "
               "interpreter_version, created_at_ms) VALUES (?, ?, ?, 'e1-v1', 'seg-v1', 'interp-v1', ?)", (raw, job, ids["model"], NOW))
    role = env.item(kind="role", statement="Help desk technician", start_date="2022-01", end_date="2025-01")
    for attrs in ({"type": "residency", "country": "US", "state": "FL"},
                  {"type": "work_authorization", "us_citizen": True, "authorized_to_work_us": True}):
        env.verify(env.item(kind="eligibility", statement=attrs["type"], attributes=attrs))
    e1 = {"requirements": [item("Candidates for this role must be based in the US", importance="hard", eligibility_type="work_authorization"),
                           item("1+ years of help desk experience", min_years=1), item("Experience with Active Directory"),
                           item("Experience with ticketing systems")]}
    p = f"P{role}"
    server = FakeServer(("ok", e1), ("ok", e3(("R1", "direct", [p], None, None), ("R2", "direct", [p], None, None),
                                              ("R3", "direct", [p], None, None))))
    _, e = env.analyze(server)
    assert asyncio.run(e.run_once()) == "done"
    calls = server.chat_calls
    profile = store.filter_pipeline.active_profile(env.ev)
    # v2's rule is kept by every later version (v3 changes fact checks only), so the active one shows it.
    assert store.recompute(env.ev, profile, NOW, rule_version="v1") == 1 and server.chat_calls == calls
    got = {r[0]: (r[1], r[2]) for r in db.execute("SELECT verdict_rule_version, eligibility, recommendation FROM recommendations")}
    assert got == {"v1": ("UNKNOWN", "REVIEW"), VERDICT_RULE_VERSION: ("ELIGIBLE", "APPLY")}
    reason = json.loads(db.execute("SELECT eligibility_reasons FROM recommendations WHERE verdict_rule_version = ?",
                                   (VERDICT_RULE_VERSION,)).fetchone()[0])[0]
    assert (reason["effect"], reason["model_type"], reason["model_outcome"]) == ("ok", "work_authorization", "met")


def test_citation_of_an_inferred_claim_is_rejected_by_the_fk(env):
    role, _, _ = profile_basic(env)
    claim_id = env.db.execute("INSERT INTO inferred_claims (id, statement, kind, created_at_ms) VALUES (777, 'Kubernetes', 'skill', ?) "
                              "RETURNING id", (NOW,)).fetchone()[0]
    answer = e3(("R1", "direct", [f"P{role}"], None, None), ("R2", "direct", [f"P{claim_id}"], None, None),
                ("R3", "transferable", [f"P{claim_id}", f"P{role}"], "tickets", None), ("R4", "unknown", [], None, None))
    _, e = env.analyze(FakeServer(("ok", E1), ("ok", answer)))
    assert asyncio.run(e.run_once()) == "done"
    (ev,) = evals(env.db)
    ms = matches(env.db, ev["id"])
    mon = ms["Experience with network monitoring tools"]
    assert (mon["match_type"], mon["citations"]) == ("unknown", []) and {"citation_rejected", "downgraded"} <= flags(mon)
    tick = ms["Familiarity with ticketing systems"]
    assert (tick["match_type"], tick["citations"]) == ("transferable", [role]) and "citation_rejected" in flags(tick)
    # The guarantee is the constraint itself, not the validation code.
    mid = mon["id"]
    with pytest.raises(Exception, match="FOREIGN KEY"):
        env.ev.execute("INSERT INTO match_citations (requirement_match_id, snapshot_id, item_id) VALUES (?, ?, ?)",
                       (mid, ev["profile_snapshot_id"], claim_id))


def test_profile_edit_queues_a_new_evaluation_and_supersedes_the_pending_one(env):
    role, skill, _ = profile_basic(env)
    a, e = env.analyze(FakeServer(("ok", E1)))
    e.sync()
    (first,) = evals(env.db)
    assert first["status"] == "pending"
    e.sync()
    assert len(evals(env.db)) == 1  # unchanged profile: same snapshot, nothing queued
    env.verify(skill)  # a verify changes the snapshot
    e.sync()
    rows = evals(env.db)
    assert [r["status"] for r in rows] == ["superseded", "pending"]
    assert rows[0]["profile_snapshot_id"] != rows[1]["profile_snapshot_id"]
    n_snapshots = env.db.execute("SELECT count(*) FROM profile_snapshots").fetchone()[0]
    assert n_snapshots == 2
    # Reverting the edit returns to the first snapshot: its superseded evaluation is queued again
    # (found in review: the unique key would otherwise leave the stale snapshot-2 evaluation to run).
    env.user_tx(profile_items.unverify_item, skill)
    e.sync()
    rows = evals(env.db)
    assert [(r["id"], r["status"]) for r in rows] == [(first["id"], "pending"), (rows[1]["id"], "superseded")]
    assert len(rows) == 2


def test_reverted_profile_shows_the_recommendation_for_the_current_snapshot(env):
    """Independent P1.7 review: snapshot A done, edit -> B done, revert -> A. The job must show A's
    evaluation (current), not B's (newest id); a B evaluation still pending at the revert must not run."""
    role, skill, _ = profile_basic(env)
    answers = [("ok", e3(("R1", "direct", [f"P{role}"], None, None)))] * 2
    server = FakeServer(("ok", E1), *answers)
    _, e = env.analyze(server)
    assert asyncio.run(e.run_once()) == "done"                     # A
    env.verify(skill)
    assert asyncio.run(e.run_once()) == "done"                     # B
    a, b = evals(env.db)
    env.user_tx(profile_items.unverify_item, skill)                # back to A
    e.sync()
    shown = store.job_recommendation(env.ev, env.ids["job"])
    assert shown["evaluation"]["id"] == a["id"] and shown["recommendation"] is not None
    env.verify(skill)                                              # B again: done already, shown again
    e.sync()
    assert store.job_recommendation(env.ev, env.ids["job"])["evaluation"]["id"] == b["id"]
    env.item(kind="skill", statement="Bash")                       # C, queued pending
    e.sync()
    env.db.execute("DELETE FROM experience_items WHERE statement = 'Bash'")  # revert C -> B
    e.sync()
    assert [x["status"] for x in evals(env.db)] == ["done", "done", "superseded"]


def test_listings_label_a_recommendation_from_an_older_snapshot(env):
    """Codex adversarial review: after a profile edit the list kept showing the old snapshot's
    recommendation with nothing saying so."""
    _, skill, _ = profile_basic(env)
    _, e = env.analyze(FakeServer(("ok", E1), ("ok", e3())))
    assert asyncio.run(e.run_once()) == "done"
    (row,) = store.recommendation_list(env.ev, None)
    assert (row["current"], row["newer_evaluation_pending"]) == (True, False)
    env.verify(skill)
    e.sync()
    (row,) = store.recommendation_list(env.ev, None)
    assert (row["current"], row["newer_evaluation_pending"]) == (False, True)
    job = store.job_recommendation(env.ev, env.ids["job"])
    assert (job["current"], job["newer_evaluation_pending"]) == (False, True)
    for g in store.review_questions(env.ev):
        assert all(j["current"] is False for j in g["jobs"])


def test_jobs_the_active_profile_rejects_are_not_recommended(env):
    profile_basic(env)
    _, e = env.analyze(FakeServer(("ok", E1), ("ok", e3())))
    assert asyncio.run(e.run_once()) == "done"
    assert [r["job_id"] for r in store.recommendation_list(env.ev, None)] == [env.ids["job"]]
    env.db.execute("UPDATE filter_results SET verdict = 'reject'")
    assert store.recommendation_list(env.ev, None) == [] and store.job_recommendation(env.ev, env.ids["job"]) is None


def test_eligibility_fact_or_out_of_range_id_does_not_count_as_a_citation(env):
    role, _, fact = profile_basic(env)
    answer = e3(("R1", "direct", [f"P{fact}"], None, None), ("R2", "direct", ["P" + "9" * 20, f"P{role}"], None, None))
    _, e = env.analyze(FakeServer(("ok", E1), ("ok", answer)))
    assert asyncio.run(e.run_once()) == "done"
    ms = matches(env.db, evals(env.db)[0]["id"])
    linux = ms["3+ years of Linux administration experience"]
    assert (linux["match_type"], linux["citations"]) == ("unknown", []) and "cites_eligibility_fact" in flags(linux)
    mon = ms["Experience with network monitoring tools"]
    assert mon["citations"] == [role] and "citation_rejected" in flags(mon)


def test_pending_evaluation_of_a_replaced_posting_or_closed_job_is_superseded(env):
    from test_filter_pipeline import new_version
    profile_basic(env)
    _, e = env.analyze(FakeServer(("ok", E1)))
    e.sync()
    new_version(env.db, env.ids["job"], 7)  # the posting changed; its new analysis is not done yet
    r = e.sync()
    assert r.superseded == 1 and [x["status"] for x in evals(env.db)] == ["superseded"]


# ----- reuse of a stored E3 answer for the exact same request -----

def _done_once(env, answer=None):
    role, skill, fact = profile_basic(env)
    server = FakeServer(("ok", E1), ("ok", answer or e3(("R1", "direct", [f"P{role}"], None, None),
                                                         ("R2", "direct", [f"P{skill}"], None, None))))
    _, e = env.analyze(server)
    assert asyncio.run(e.run_once()) == "done"
    return server, e, role, skill


def test_verify_reuses_the_stored_answer_and_updates_the_basis(env, caplog, capsys):
    server, e, role, skill = _done_once(env)
    calls = server.chat_calls
    env.verify(skill)  # new snapshot; the E3 prompt does not include status, so the request is identical
    assert asyncio.run(e.run_once()) == "done"
    assert server.chat_calls == calls
    first, second = evals(env.db)
    assert (second["status"], second["request_sha256"]) == ("done", first["request_sha256"])
    assert json.loads(second["raw_response"])["reused_from"] == first["id"]
    mon = matches(env.db, second["id"])["Experience with network monitoring tools"]
    assert (mon["match_type"], mon["basis"]) == ("direct", "verified")  # E4 recomputed the basis from the new snapshot
    assert "reused_from" in logged(caplog, capsys)


def test_match_version_bump_reruns_e4_e6_without_the_model(env, monkeypatch):
    server, e, _, _ = _done_once(env)
    calls = server.chat_calls
    monkeypatch.setattr(store, "MATCH_PROMPT_VERSION", "m1-vtest")  # e.g. an E4 change; same prompt text
    assert e.sync().queued == 1
    assert asyncio.run(e.run_once()) == "done" and server.chat_calls == calls
    assert [r["match_prompt_version"] for r in evals(env.db)] == ["m1-v1", "m1-vtest"]


def test_changed_item_text_calls_the_model(env):
    server, e, role, skill = _done_once(env)
    server.answers.append(("ok", e3()))
    calls = server.chat_calls
    env.user_tx(profile_items.update_item, skill, {"statement": "Zabbix, SolarWinds and PRTG network monitoring"})
    assert asyncio.run(e.run_once()) == "done" and server.chat_calls == calls + 1
    first, second = evals(env.db)
    assert second["request_sha256"] != first["request_sha256"] and "reused_from" not in json.loads(second["raw_response"])


def test_reuse_runs_while_inference_is_down(env):
    server, e, _, skill = _done_once(env)
    server.down = True
    env.clock.t += 31_000
    env.verify(skill)
    assert asyncio.run(e.run_once()) == "done"
    assert [r["status"] for r in evals(env.db)] == ["done", "done"]


def test_a_different_model_never_reuses(env):
    from jobforge_agent.inference.client import load_config
    profile_basic(env)
    _, e = env.analyze(FakeServer(("ok", E1)))
    e.sync()
    (ev,) = evals(env.db)
    cfg = load_config(env.ev, ev["model_config_id"])
    a = store.prepare(env.ev, ev["posting_analysis_id"], ev["profile_snapshot_id"], "NOC Engineer", cfg)
    other = type(cfg)(**{**cfg.__dict__, "model_id": "openai/gpt-oss-20b"})
    b = store.prepare(env.ev, ev["posting_analysis_id"], ev["profile_snapshot_id"], "NOC Engineer", other)
    assert a.sha == ev["request_sha256"] and a.sha != b.sha


def test_rule_change_recomputes_with_zero_inference_calls(env, monkeypatch):
    profile_basic(env)
    server = FakeServer(("ok", E1), ("ok", e3(("R1", "unknown", [], None, None))))
    _, e = env.analyze(server)
    assert asyncio.run(e.run_once()) == "done"
    calls = server.chat_calls
    # A new rule version (same thresholds; only the label matters here).
    original = store.assess.load_rules
    monkeypatch.setattr(store, "VERDICT_RULE_VERSION", "v1-recompute-test")
    monkeypatch.setattr(store.assess, "load_rules", lambda v: original("v1"))
    r = e.sync()
    assert r.recomputed == 1 and server.chat_calls == calls
    versions = [x[0] for x in env.db.execute("SELECT verdict_rule_version FROM recommendations ORDER BY id")]
    assert versions == [VERDICT_RULE_VERSION, "v1-recompute-test"]
    assert e.sync().recomputed == 0  # idempotent


def test_search_profile_change_recomputes_without_the_model(env):
    profile_basic(env)
    server = FakeServer(("ok", E1), ("ok", e3()))
    _, e = env.analyze(server)
    assert asyncio.run(e.run_once()) == "done"
    calls = server.chat_calls
    pid = env.db.execute("INSERT INTO search_profiles (version, rules, created_at_ms) VALUES (2, ?, ?) RETURNING id",
                         (json.dumps({**SEARCH, "opportunity": None}), NOW)).fetchone()[0]
    # Not passed by the new profile yet (its re-filter has not landed): no recommendation under it.
    assert e.sync().recomputed == 0
    env.db.execute("INSERT INTO filter_results (job_id, raw_posting_id, search_profile_id, ruleset_version, verdict, reasons, "
                   "created_at_ms) VALUES (?, ?, ?, ?, 'pass', '{}', ?)", (env.ids["job"], env.ids["raw"], pid, _ruleset(), NOW))
    assert e.sync().recomputed == 1 and server.chat_calls == calls
    rows = env.db.execute("SELECT search_profile_id, opportunity FROM recommendations ORDER BY id").fetchall()
    assert [r[0] for r in rows] == [env.ids["profile"], pid]


def test_inference_down_keeps_evaluations_pending_then_drains(env, caplog, capsys):
    profile_basic(env)
    server = FakeServer(("ok", E1), ("ok", e3()))
    a, e = env.analyze(server)
    server.down = True
    env.clock.t += 31_000  # past the 30 s up-recheck: the gate probes and finds the server gone
    assert asyncio.run(e.run_once()) is None
    (ev,) = evals(env.db)
    assert (ev["status"], ev["attempts"]) == ("pending", 0)
    assert e.sync().queued == 0  # code-only work still runs while inference is down
    server.down = False
    env.clock.t += 61_000  # first down backoff is 60 s
    assert asyncio.run(e.run_once()) == "done"
    out = logged(caplog, capsys)
    assert out.index("inference_unavailable") < out.rindex("inference_available") < out.index("evaluation_done")


def test_stopped_server_mid_call_defers_without_spending_an_attempt(env):
    profile_basic(env)
    _, e = env.analyze(FakeServer(("ok", E1), ("connect", None)))
    assert asyncio.run(e.run_once()) == "deferred"
    (ev,) = evals(env.db)
    assert (ev["status"], ev["attempts"]) == ("pending", 0)


def test_invalid_output_twice_fails_with_raw_response_kept(env):
    profile_basic(env)
    _, e = env.analyze(FakeServer(("ok", E1), ("ok", "{\"matches\": 1}"), ("ok", "not json")))
    assert asyncio.run(e.run_once()) == "failed:invalid_output"
    (ev,) = evals(env.db)
    assert ev["status"] == "failed" and len(json.loads(ev["raw_response"])["attempts"]) == 2
    assert env.db.execute("SELECT count(*) FROM recommendations").fetchone()[0] == 0


def test_budget_exhausted_is_never_parsed(env):
    profile_basic(env)
    _, e = env.analyze(FakeServer(("ok", E1), ("length", None)))
    assert asyncio.run(e.run_once()) == "failed:budget_exhausted"


def test_empty_profile_makes_no_model_call_and_asks_questions(env):
    server = FakeServer(("ok", E1))
    _, e = env.analyze(server)
    assert asyncio.run(e.run_once()) == "done"
    assert server.chat_calls == 1  # E1 only
    rec = dict(env.db.execute("SELECT * FROM recommendations").fetchone())
    assert (rec["eligibility"], rec["fit"], rec["recommendation"]) == ("UNKNOWN", "INSUFFICIENT", "REVIEW")
    questions = json.loads(rec["decisive_reasons"])[0]["questions"]
    assert any(q["kind"] == "eligibility" and q["type"] == "work_authorization" for q in questions)


def test_long_model_call_does_not_block_a_user_action(env):
    """ARCHITECTURE 4.3: a model call held open; a verify commits within 1 s meanwhile."""
    role, skill, _ = profile_basic(env)
    _, e = env.analyze(FakeServer(("ok", E1)))
    released = asyncio.Event()
    seen = {}

    async def slow(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}]})
        seen["in_tx"] = env.ev.in_transaction or env.user.in_transaction
        await asyncio.wait_for(released.wait(), timeout=30)
        return httpx.Response(200, json={"model": MODEL, "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(e3())}}]})

    client = InferenceClient(httpx.MockTransport(slow))
    env.clients.append(client)
    worker = EvaluationWorker(env.ev, client, e.gate, now_ms=env.clock)

    async def go():
        task = asyncio.create_task(worker.run_once())
        while "in_tx" not in seen:
            await asyncio.sleep(0.01)
        await asyncio.sleep(1.2)  # the call has now been open for more than a second
        t0 = time.perf_counter()
        env.verify(skill)
        elapsed = time.perf_counter() - t0
        released.set()
        return elapsed, await task

    elapsed, outcome = asyncio.run(go())
    assert seen["in_tx"] is False and elapsed < 1.0 and outcome == "done"
    assert env.db.execute("SELECT status FROM experience_items WHERE id = ?", (skill,)).fetchone()[0] == "verified"


def test_recovery_supersedes_a_running_evaluation_with_a_newer_pending_one(env, paths):
    profile_basic(env)
    a, e = env.analyze(FakeServer(("ok", E1)))
    e.sync()
    env.db.execute("UPDATE evaluations SET status = 'running'")
    env.item(kind="skill", statement="Bash")
    e.sync()
    assert [r["status"] for r in evals(env.db)] == ["running", "pending"]
    lc = open_connection(paths.db, "lifecycle")
    report = recovery.recover(lc, NOW)
    lc.close()
    assert report.superseded_evaluations == 1
    assert [r["status"] for r in evals(env.db)] == ["superseded", "pending"]


def test_closed_or_rejected_jobs_are_not_evaluated(env):
    profile_basic(env)
    _, e = env.analyze(FakeServer(("ok", E1)))
    env.db.execute("DELETE FROM filter_results")
    assert e.sync().queued == 0
    env.db.execute("INSERT INTO filter_overrides (job_id, created_at_ms) VALUES (?, ?)", (env.ids["job"], NOW))
    assert e.sync().queued == 1
    assert evals(env.db)[0]["requested_by"] == "override"
    env.db.execute("UPDATE evaluations SET status = 'superseded'")
    env.db.execute("UPDATE jobs SET closed_at_ms = ?", (NOW,))
    env.item(kind="skill", statement="Bash")
    assert e.sync().queued == 0


# ----- schema lint and version pin -----

def test_schema_lint_no_enum_value_in_two_fields():
    from test_analysis import shared_enum_values
    assert shared_enum_values(match.SCHEMA) == {}


def test_match_prompt_changes_require_a_version_bump():
    """Evaluations are keyed by MATCH_PROMPT_VERSION (D10). If this fails: bump it in
    evaluation/versions.py (also when E4 validation changes), then update both values here."""
    import hashlib
    from jobforge_agent.evaluation.snapshot import Item
    from jobforge_agent.evaluation.versions import MATCH_PROMPT_VERSION
    from test_verdict import req
    text, _ = match.user_message("T", [req(1, min_years=2)], [Item(5, "role", "NOC", "verified", {}, 1.5, "2024-01", None)])
    digest = hashlib.sha256((match.SYSTEM + json.dumps(match.SCHEMA, sort_keys=True) + text).encode()).hexdigest()[:16]
    assert (MATCH_PROMPT_VERSION, digest) == ("m1-v1", "60aeb05ba172f837")


def test_parse_rejects_bad_ids_and_kinds():
    good = e3(("R1", "direct", ["P1"], None, None))
    assert match.parse(json.dumps(good)).matches[0].cited_items == ["P1"]
    for bad in (e3(("R1", "direct", ["1"], None, None)), e3(("R1", "maybe", [], None, None)),
                {**good, "extra": 1}, {"matches": []}):
        with pytest.raises(match.InvalidOutput):
            match.parse(json.dumps(bad))


# ----- IPC -----

def test_recommendation_routes(paths):
    server = FakeServer(("ok", E1), ("ok", e3(("R1", "unknown", [], None, None))))

    async def check(client, runtime):
        owner = open_connection(paths.db, "migration")
        ids = seed_basics(owner)
        owner.execute("UPDATE model_configs SET model_id = ?, tested_at_ms = ?, enabled = 1 WHERE id = ?", (MODEL, NOW, ids["model"]))
        owner.execute("UPDATE settings SET value = ? WHERE key = 'default_model_config_id'", (str(ids["model"]),))
        pid = owner.execute("INSERT INTO search_profiles (version, rules, created_at_ms) VALUES (1, ?, ?) RETURNING id",
                            (json.dumps(SEARCH), NOW)).fetchone()[0]
        job, raw = add_job(owner, ids, 7, "NOC Engineer", desc=POSTING)
        owner.execute("INSERT INTO filter_results (job_id, raw_posting_id, search_profile_id, ruleset_version, verdict, reasons, "
                      "created_at_ms) VALUES (?, ?, ?, ?, 'pass', '{}', ?)", (job, raw, pid, _ruleset(), NOW))
        owner.execute("INSERT INTO posting_analyses (raw_posting_id, job_id, model_config_id, extraction_prompt_version, "
                      "segmenter_version, interpreter_version, created_at_ms) VALUES (?, ?, ?, 'e1-v1', 'seg-v1', 'interp-v1', ?)",
                      (raw, job, ids["model"], NOW))
        owner.close()
        a = AnalysisWorker(runtime.evaluation_conn, runtime.inference_client())
        e = EvaluationWorker(runtime.evaluation_conn, runtime.inference_client(), a.gate)
        runtime.evaluation = e
        assert await a.run_once() == "done"
        assert (await client.post("/v1/recompute", headers=AUTH)).json()["queued"] == 1
        assert (await client.get("/v1/evaluation/status", headers=AUTH)).json()["counts"] == {"pending": 1}
        assert await e.run_once() == "done"
        r = (await client.get(f"/v1/jobs/{job}/recommendation", headers=AUTH)).json()
        assert r["recommendation"]["recommendation"] == "REVIEW" and r["newer_evaluation_pending"] is False
        assert {row["class"] for row in r["evidence"]} >= {"hard", "negotiable", "preferred", "context"}
        listing = (await client.get("/v1/recommendations?category=REVIEW", headers=AUTH)).json()
        assert [x["job_id"] for x in listing] == [job]
        assert (await client.get("/v1/recommendations?category=MAYBE", headers=AUTH)).status_code == 422
        groups = (await client.get("/v1/review-questions", headers=AUTH)).json()
        assert groups and groups[0]["jobs"][0]["job_id"] == job
        assert (await client.get("/v1/jobs/999/recommendation", headers=AUTH)).status_code == 404

    _run_with_fake(paths, server, check)


def test_startup_queues_current_version_analyses_for_passing_jobs_only(env):
    """interp-v3, 2026-10-24: a new E2 version reached only re-filtered jobs; at startup every open job that
    passes (or has an override) and keeps its text gets an analysis at the current versions, once."""
    from jobforge_agent.analysis.versions import INTERPRETER_VERSION
    from jobforge_agent.filtering import pipeline
    env.db.execute("UPDATE posting_analyses SET interpreter_version = 'interp-old'")
    rejected, _ = add_job(env.db, env.ids, 31, "Sales Director", desc="Sell things.")
    disc = open_connection(env.paths.db, "discovery")  # the agent's startup runs it on this connection (authorizer)
    disc.execute("BEGIN IMMEDIATE")
    assert pipeline.queue_current_analyses(disc, NOW) == 1  # the seeded passing job; the unfiltered one is not queued
    disc.execute("COMMIT")
    assert env.db.execute("SELECT count(*) FROM posting_analyses WHERE job_id = ? AND interpreter_version = ? AND status = 'pending'",
                          (env.ids["job"], INTERPRETER_VERSION)).fetchone()[0] == 1
    assert env.db.execute("SELECT count(*) FROM posting_analyses WHERE job_id = ?", (rejected,)).fetchone()[0] == 0
    disc.execute("BEGIN IMMEDIATE")
    assert pipeline.queue_current_analyses(disc, NOW) == 0  # idempotent
    disc.execute("COMMIT")
    disc.close()

