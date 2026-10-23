"""Golden set (ARCHITECTURE 6.7, P1.9): append-only labels, the report's numbers, the interpretation
report against a model, and the read-only CLI."""
import asyncio
import json

import httpx
import pytest

from conftest import NOW
from jobforge_agent import main as cli, views
from jobforge_agent.db.connect import open_connection
from jobforge_agent.evaluation import golden, store
from jobforge_agent.inference.client import InferenceClient, load_config
from test_analysis import MODEL, FakeServer, _run_with_fake
from test_evaluation import E1, e3, env, evals, profile_basic  # noqa: F401  (fixture reused)
from test_ipc import AUTH
from test_views import evaluated


def test_labels_are_append_only_and_remember_what_was_shown(env):
    evaluated(env)
    job = env.ids["job"]
    shown = store.job_recommendation(env.ev, job)["recommendation"]
    first = env.user_tx(golden.label, job, {"recommendation": "APPLY", "obvious": True}, NOW)
    assert (first["recommendation"], first["shown_recommendation_id"], first["raw_posting_id"]) == ("APPLY", shown["id"], env.ids["raw"])
    env.user_tx(golden.label, job, {"recommendation": "STRETCH", "eligibility": "ELIGIBLE"}, NOW + 1)
    assert golden.current_label(env.ev, job)["recommendation"] == "STRETCH"
    assert views.job_detail(env.ev, job)["golden_label"]["eligibility"] == "ELIGIBLE"  # what the window shows
    env.user_tx(golden.withdraw, job, NOW + 2)
    assert golden.current_label(env.ev, job) is None
    assert env.ev.execute("SELECT count(*) FROM golden_labels WHERE job_id = ?", (job,)).fetchone()[0] == 3
    with pytest.raises(golden.LabelError, match="APPLY, STRETCH"):
        env.user_tx(golden.label, job, {"recommendation": "MAYBE"}, NOW)
    with pytest.raises(golden.LabelError, match="not from this job"):
        env.user_tx(golden.label, job, {"recommendation": "SKIP", "misread_requirement_ids": [99999]}, NOW)
    req = env.ev.execute("SELECT r.id FROM requirements r JOIN posting_analyses pa ON pa.id = r.posting_analysis_id "
                         "WHERE pa.job_id = ? LIMIT 1", (job,)).fetchone()[0]
    assert env.user_tx(golden.label, job, {"recommendation": "SKIP", "misread_requirement_ids": [req]}, NOW)["misread_requirement_ids"] == [req]
    with pytest.raises(LookupError):
        env.user_tx(golden.label, 99999, {"recommendation": "SKIP"}, NOW)


def test_report_counts_false_skip_false_apply_review_and_confusion_per_version(env):
    evaluated(env)
    job = env.ids["job"]
    shown = store.job_recommendation(env.ev, job)["recommendation"]
    # The same evaluation under another rule version said SKIP: a second version tuple in the report.
    cols = [c for c in shown if c not in ("id", "verdict_rule_version", "recommendation")]
    vals = [json.dumps(shown[c]) if isinstance(shown[c], (list, dict)) else shown[c] for c in cols]
    env.db.execute(f"INSERT INTO recommendations ({', '.join(cols)}, verdict_rule_version, recommendation) "
                   f"VALUES ({', '.join('?' * len(cols))}, 'vtest', 'SKIP')", vals)
    env.user_tx(golden.label, job, {"recommendation": "APPLY"}, NOW)
    # A provisional label from someone else, never reviewed: not counted.
    env.db.execute("INSERT INTO golden_labels (job_id, raw_posting_id, recommendation, labeled_by, created_at_ms) "
                   "SELECT id, current_raw_posting_id, 'SKIP', 'spike-reference', ? FROM jobs WHERE id != ? LIMIT 1", (NOW, job))
    r = golden.report(env.ev)
    assert (r["labels"], r["provisional_unreviewed"]) == (1, env.ev.execute("SELECT count(*) FROM golden_labels "
                                                                             "WHERE labeled_by != 'user'").fetchone()[0])
    by_rules = {v["rules"]: v for v in r["versions"]}
    assert by_rules["vtest"]["false_skip"] == [job] and by_rules["vtest"]["confusion"]["APPLY"]["SKIP"] == 1
    cur = by_rules[shown["verdict_rule_version"]]
    assert cur["confusion"]["APPLY"][shown["recommendation"]] == 1 and cur["labeled_with_answer"] == 1
    assert cur["review_share"] == (1.0 if shown["recommendation"] == "REVIEW" else 0.0)
    text = golden.format_report(r)
    assert "false SKIP (labeled APPLY/STRETCH, system SKIP): 1 jobs" in text and "confusion" in text


def test_golden_routes_and_job_detail(paths):
    async def check(client, runtime):
        owner = open_connection(paths.db, "migration")
        from conftest import seed_basics
        job = seed_basics(owner)["job"]
        owner.close()
        r = await client.post(f"/v1/jobs/{job}/golden-label", headers=AUTH, json={"recommendation": "SKIP", "note": "on-site only"})
        assert r.status_code == 200 and r.json()["shown_recommendation_id"] is None  # no recommendation yet: recorded as such
        assert golden.current_label(runtime.reader, job)["note"] == "on-site only"
        assert (await client.post(f"/v1/jobs/{job}/golden-label", headers=AUTH, json={"recommendation": "x"})).status_code == 422
        assert (await client.post("/v1/jobs/999/golden-label", headers=AUTH, json={"recommendation": "SKIP"})).status_code == 404
        rep = (await client.get("/v1/golden/report", headers=AUTH)).json()
        assert rep["labels"] == 1 and rep["labels_without_system_answer"] == [job]
        assert (await client.post(f"/v1/jobs/{job}/golden-label/withdraw", headers=AUTH)).json() == {"withdrawn": True}
        assert (await client.post(f"/v1/jobs/{job}/golden-label/withdraw", headers=AUTH)).status_code == 404
        assert golden.current_label(runtime.reader, job) is None
        assert "golden_labeled" in [x[0] for x in runtime.reader.execute("SELECT type FROM change_events")]

    _run_with_fake(paths, FakeServer(), check)


def test_cli_report_is_read_only(paths, monkeypatch, capsys):
    from jobforge_agent.db import migrate
    conn = open_connection(paths.db, "migration")
    migrate.migrate(conn, paths.db, paths.backups)
    conn.close()
    monkeypatch.setattr(cli, "resolve_paths", lambda: paths)
    before = paths.db.stat().st_mtime_ns
    assert cli.main(["golden"]) == 0
    assert "Golden set: 0 labels (target 20-30)" in capsys.readouterr().out
    assert cli.main(["golden", "--json"]) == 0 and json.loads(capsys.readouterr().out)["labels"] == 0
    assert cli.main(["golden", "--suite", "nope"]) == 1
    assert paths.db.stat().st_mtime_ns == before


def _fixture_answer(case):
    """The case's own E1 reading as a model answer (the layer-A defaults filled in)."""
    return {"requirements": [{"text": m.get("text", m["quote"]), "jd_quote": m["quote"], "importance": m.get("importance", "required"),
                              "polarity": m.get("polarity", "affirmative"), "subject": m.get("subject", "candidate"),
                              "eligibility_type": m.get("eligibility_type", "none"), "min_years": m.get("min_years")}
                             for m in case.get("model", [])]}


def test_interpretation_report_reproduces_layer_a_when_the_model_answers_like_the_fixture(db):
    cases = [c for c in golden.interpretation_cases() if c.get("expect") or "recall" in c]
    server = FakeServer(*[("ok", _fixture_answer(c)) for c in cases])
    db.execute("UPDATE model_configs SET model_id = ?", (MODEL,))
    cfg = load_config(db, db.execute("SELECT min(id) FROM model_configs").fetchone()[0])

    async def go():
        client = InferenceClient(httpx.MockTransport(server))
        try:
            return await golden.run_interpretation(client, cfg, golden.interpretation_cases())
        finally:
            await client.aclose()

    per_area, meta = asyncio.run(go())
    assert meta["failed"] == 0 and meta["cases"] == len(cases)
    for area, a in per_area.items():
        assert a["extracted"] == a["expected"] == a["as_expected"], area  # the fixture readings give exactly the expected rows
        assert a["recall_ok"] == a["recall_cases"], area
        assert a["raw_agreed"] + a["conflict_caught"] <= a["extracted"]
    assert sum(a["conflict_caught"] for a in per_area.values()) > 0  # the fixture includes real spike misreadings
    assert "E2 caught" in golden.format_interpretation(per_area, meta)


def test_interpretation_report_stops_when_the_model_is_unreachable(db):
    server = FakeServer()
    server.down = True
    cfg = load_config(db, db.execute("SELECT min(id) FROM model_configs").fetchone()[0])

    async def go():
        client = InferenceClient(httpx.MockTransport(server))
        try:
            return await golden.run_interpretation(client, cfg, golden.interpretation_cases())
        finally:
            await client.aclose()

    per_area, meta = asyncio.run(go())
    assert meta["failed"] == 1 and "not reachable" in meta["stopped"] and server.chat_calls == 1
    assert golden.format_interpretation(per_area, meta).startswith("stopped: the model at")
