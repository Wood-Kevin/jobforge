"""Analysis worker, E1 schema and model-config routes (ARCHITECTURE 3.4, 6.4, 6.8; SPEC D10).

A fake OpenAI-compatible server (httpx.MockTransport) stands in for LM Studio, so outcomes are
scripted: valid output, invalid output, finish_reason=length, HTTP 400 context overflow, a
stopped server, timeouts.
"""
import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
import pytest

from conftest import NOW, seed_basics
from jobforge_agent import ipc
from jobforge_agent.agent import AgentRuntime
from jobforge_agent.analysis import extract, store
from jobforge_agent.analysis.versions import EXTRACTION_PROMPT_VERSION, INTERPRETER_VERSION, SEGMENTER_VERSION
from jobforge_agent.analysis.worker import AnalysisWorker
from jobforge_agent.db.connect import open_connection
from jobforge_agent.filtering import pipeline
from jobforge_agent.inference.client import InferenceClient
from jobforge_agent.profile import items as profile_items
from test_filter_pipeline import add_job
from test_ipc import AUTH, BASE, PORT, TOKEN

MODEL = "google/gemma-4-12b-qat"
POSTING = ("Requirements:\n- Must be a US citizen.\n- No prior cloud experience required.\n- Zanzibar quokka wrangling.\n\n"
           "Benefits:\n- We pay for AWS certifications.")


def item(quote, **kw):
    return {"text": kw.pop("text", quote), "jd_quote": quote, "importance": kw.pop("importance", "required"),
            "polarity": kw.pop("polarity", "affirmative"), "subject": kw.pop("subject", "candidate"),
            "eligibility_type": kw.pop("eligibility_type", "none"), "min_years": kw.pop("min_years", None)}


GOOD = {"requirements": [item("Must be a US citizen", importance="hard", eligibility_type="work_authorization"),
                         item("No prior cloud experience required"),  # the model missed the negation
                         item("Zanzibar quokka wrangling")]}


class FakeServer:
    """Scripted /v1/chat/completions answers; /v1/models lists MODEL unless `down`."""

    def __init__(self, *answers, watch=()):
        self.answers = list(answers)
        self.down = False
        self.requests: list[httpx.Request] = []
        self.watch = list(watch)  # connections that must have no open transaction during a call
        self.in_tx_during_call: list[bool] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": MODEL}, {"id": "other"}]})
        self.in_tx_during_call.append(any(c.in_transaction for c in self.watch))
        kind, payload = self.answers.pop(0)
        if kind == "ok":
            content = payload if isinstance(payload, str) else json.dumps(payload)
            return httpx.Response(200, json={"model": MODEL, "usage": {"prompt_tokens": 900, "completion_tokens": 200},
                                             "choices": [{"finish_reason": "stop", "message": {"content": content}}]})
        if kind == "length":
            return httpx.Response(200, json={"model": MODEL, "choices": [{"finish_reason": "length", "message": {"content": "{\"req"}}]})
        if kind == "status":
            return httpx.Response(payload[0], text=payload[1])
        if kind == "connect":
            raise httpx.ConnectError("refused", request=request)
        if kind == "timeout":
            raise httpx.ReadTimeout("slow", request=request)
        raise AssertionError(kind)

    @property
    def chat_calls(self) -> int:
        return sum(1 for r in self.requests if r.url.path.endswith("/chat/completions"))


class Clock:
    def __init__(self):
        self.t = NOW

    def __call__(self) -> int:
        return self.t


@pytest.fixture
def env(db, paths):
    ids = seed_basics(db)
    db.execute("UPDATE model_configs SET model_id = ?, tested_at_ms = ?, enabled = 1, "
               "params = '{\"max_tokens\": 4000, \"temperature\": 0, \"seed\": 7}' WHERE id = ?", (MODEL, NOW, ids["model"]))
    job, raw = add_job(db, ids, 7, "NOC Engineer", desc=POSTING)
    ids.update(job7=job, raw7=raw)
    ev = open_connection(paths.db, "evaluation")
    clients = []

    def make(server, clock=None, **kw):
        server.watch.append(ev)
        client = InferenceClient(httpx.MockTransport(server))
        clients.append(client)
        return AnalysisWorker(ev, client, now_ms=clock or Clock(), **kw)

    yield db, ids, make
    for c in clients:
        asyncio.run(c.aclose())
    ev.close()


def logged(caplog, capsys) -> str:
    """structlog prints to stdout when run alone and goes through stdlib logging in the full suite."""
    out = capsys.readouterr()
    return caplog.text + out.out + out.err


def queue(db, ids, raw=None, job=None) -> int:
    return db.execute(
        "INSERT INTO posting_analyses (raw_posting_id, job_id, model_config_id, extraction_prompt_version, segmenter_version, "
        "interpreter_version, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (raw or ids["raw7"], job or ids["job7"], ids["model"], EXTRACTION_PROMPT_VERSION, SEGMENTER_VERSION,
         INTERPRETER_VERSION, NOW)).fetchone()[0]


def row(db, aid):
    return dict(db.execute("SELECT * FROM posting_analyses WHERE id = ?", (aid,)).fetchone())


def reqs(db, aid):
    return [dict(r) for r in db.execute("SELECT * FROM requirements WHERE posting_analysis_id = ? ORDER BY id", (aid,))]


def events(db, type_):
    return [json.loads(r[0]) for r in db.execute("SELECT ref FROM change_events WHERE type = ? ORDER BY id", (type_,))]


def test_done_stores_both_readings_and_no_transaction_spans_the_call(env):
    db, ids, make = env
    aid = queue(db, ids)
    server = FakeServer(("ok", GOOD))
    w = make(server)
    assert asyncio.run(w.run_once()) == "done"
    a = row(db, aid)
    assert (a["status"], a["attempts"], a["lease_until_ms"], a["model_id_reported"]) == ("done", 1, None, MODEL)
    assert json.loads(a["sections"])["headings"] == 2
    assert json.loads(a["raw_response"])["attempts"][0]["content"] == json.dumps(GOOD)
    rs = {r["jd_quote"]: r for r in reqs(db, aid)}
    citizen, cloud = rs["Must be a US citizen"], rs["No prior cloud experience required"]
    assert (citizen["final_class"], citizen["interpretation_status"], citizen["eligibility_type"]) == ("hard", "confirmed", "work_authorization")
    # Conflict stored with both readings: the model's polarity and the code's.
    assert (cloud["model_polarity"], cloud["cue_polarity"], cloud["final_class"], cloud["interpretation_status"]) == (
        "affirmative", "negated", "negated", "conflict")
    assert any(f["flag"] == "conflict" for f in json.loads(cloud["flags"]))
    assert events(db, "analysis_done")[0] == {"analysis_id": aid, "requirements": 3, "unextracted_eligibility": 0, "confirmed": 2,
                                              "conflict": 1, "unverifiable": 0, "hard": 1}
    assert server.in_tx_during_call == [False]


def test_unverifiable_quote_is_stored_with_the_model_reading(env):
    db, ids, make = env
    aid = queue(db, ids)
    w = make(FakeServer(("ok", {"requirements": [item("Active TS/SCI clearance", importance="hard", eligibility_type="clearance")]})))
    asyncio.run(w.run_once())
    (r,) = [r for r in reqs(db, aid) if r["origin"] == "model"]
    assert (r["quote_found"], r["interpretation_status"], r["final_class"], r["model_importance"], r["model_eligibility_type"]) == (
        0, "unverifiable", "negotiable", "hard", "clearance")


def test_recall_row_for_an_eligibility_sentence_the_model_missed(env):
    db, ids, make = env
    aid = queue(db, ids)
    w = make(FakeServer(("ok", {"requirements": [item("Zanzibar quokka wrangling")]})))
    asyncio.run(w.run_once())
    recall = [r for r in reqs(db, aid) if r["origin"] == "unextracted_eligibility"]
    assert [(r["eligibility_type"], r["interpretation_status"]) for r in recall] == [("work_authorization", "unverifiable")]


def test_invalid_output_is_retried_once(env):
    db, ids, make = env
    aid = queue(db, ids)
    server = FakeServer(("ok", "not json"), ("ok", GOOD))
    assert asyncio.run(make(server).run_once()) == "done"
    attempts = json.loads(row(db, aid)["raw_response"])["attempts"]
    assert server.chat_calls == 2 and "schema errors" in attempts[0]["error"] and "error" not in attempts[1]


def test_invalid_output_twice_fails_with_the_raw_response_kept(env):
    db, ids, make = env
    aid = queue(db, ids)
    bad = {"requirements": [{**item("x"), "importance": "mandatory"}]}
    assert asyncio.run(make(FakeServer(("ok", "not json"), ("ok", bad))).run_once()) == "failed:invalid_output"
    a = row(db, aid)
    assert a["status"] == "failed" and a["last_error"].startswith("invalid_output")
    assert [x["content"] for x in json.loads(a["raw_response"])["attempts"]] == ["not json", json.dumps(bad)]
    assert reqs(db, aid) == [] and events(db, "analysis_failed") == [{"analysis_id": aid, "error": "invalid_output"}]


def test_finish_reason_length_is_budget_exhausted_and_never_parsed(env):
    db, ids, make = env
    aid = queue(db, ids)
    server = FakeServer(("length", None))
    assert asyncio.run(make(server).run_once()) == "failed:budget_exhausted"
    assert row(db, aid)["last_error"].startswith("budget_exhausted") and server.chat_calls == 1


def test_context_overflow_fails_without_retry(env):
    """The 400 body measured from LM Studio in P1.6 (18,216-token prompt, 16,384 loaded)."""
    db, ids, make = env
    aid = queue(db, ids)
    body = ('{"error": "Engine protocol predict request returned 400: {\\"error\\":{\\"code\\":400,\\"message\\":\\"request '
            '(18216 tokens) exceeds the available context size (16384 tokens), try increasing it\\",\\"type\\":\\"exceed_context_size_error\\"}}"}')
    server = FakeServer(("status", (400, body)))
    assert asyncio.run(make(server).run_once()) == "failed:context_overflow"
    assert row(db, aid)["status"] == "failed" and server.chat_calls == 1


def test_stopped_server_defers_without_spending_attempts_and_resumes(env, caplog, capsys):
    db, ids, make = env
    aid = queue(db, ids)
    clock = Clock()
    server = FakeServer(("connect", None), ("ok", GOOD))
    w = make(server, clock)
    with caplog.at_level(logging.INFO):
        assert asyncio.run(w.run_once()) == "deferred"
        a = row(db, aid)
        assert (a["status"], a["attempts"]) == ("pending", 0)
        # Down: nothing is claimed and no model call is made until the probe is due again.
        server.down = True
        assert asyncio.run(w.run_once()) is None and server.chat_calls == 1
        clock.t += 61_000
        assert asyncio.run(w.run_once()) is None  # probe fails: still down, backoff doubles
        assert w.gate.states[ids["model"]].backoff_s == 120
        server.down = False
        clock.t += 121_000
        assert asyncio.run(w.run_once()) == "done"
    assert row(db, aid)["attempts"] == 1
    text = logged(caplog, capsys)
    assert text.index("inference_unavailable") < text.rindex("inference_available")


def test_timeouts_spend_attempts_then_fail(env):
    db, ids, make = env
    aid = queue(db, ids)
    w = make(FakeServer(("timeout", None), ("timeout", None), ("timeout", None)))
    outcomes = [asyncio.run(w.run_once()) for _ in range(3)]
    assert outcomes == ["attempt_failed:pending", "attempt_failed:pending", "attempt_failed:failed"]
    a = row(db, aid)
    assert (a["status"], a["attempts"]) == ("failed", 3) and a["last_error"].startswith("timeout")
    assert events(db, "analysis_failed") == [{"analysis_id": aid, "error": "attempts_exhausted"}]


def test_newer_posting_version_supersedes_a_released_analysis(env, paths):
    db, ids, make = env
    aid = queue(db, ids)
    disc = open_connection(paths.db, "discovery")

    async def newer_version_arrives():
        raw = db.execute("INSERT INTO raw_postings (employer_id, source_job_id, fetched_at_ms, payload, description_text, payload_sha256, "
                         "material_sha256, adapter_version) VALUES (?, 'greenhouse:acme:7', ?, '{}', 'v2', 'p7b', 'm7b', 'gh-1') RETURNING id",
                         (ids["employer"], NOW)).fetchone()[0]
        assert pipeline.queue_analysis(disc, ids["job7"], raw, ids["model"], NOW)

    w = make(FakeServer(("connect", None)), before_call=newer_version_arrives)
    assert asyncio.run(w.run_once()) == "deferred"
    assert row(db, aid)["status"] == "superseded"
    disc.close()


def test_disabled_config_claims_nothing_and_says_so(env, caplog, capsys):
    db, ids, make = env
    aid = queue(db, ids)
    db.execute("UPDATE model_configs SET enabled = 0")
    server = FakeServer()
    with caplog.at_level(logging.INFO):
        assert asyncio.run(make(server).run_once()) is None
    assert server.requests == [] and row(db, aid)["status"] == "pending"
    assert "analysis_waiting" in logged(caplog, capsys)


def test_profile_edits_do_not_rerun_analysis(env, paths):
    """D10 / P1.6 acceptance: analysis depends on the posting version and prompt only."""
    db, ids, make = env
    aid = queue(db, ids)
    server = FakeServer(("ok", GOOD))
    w = make(server)
    assert asyncio.run(w.run_once()) == "done"
    user = open_connection(paths.db, "user")
    item_id = profile_items.create_item(user, {"kind": "skill", "statement": "Linux"})
    profile_items.verify_item(user, item_id, None, NOW)
    profile_items.update_item(user, item_id, {"statement": "Linux administration"})
    profile_items.set_completeness(user, {"cert_list_complete": True}, NOW)
    user.close()
    assert asyncio.run(w.run_once()) is None
    assert server.chat_calls == 1
    assert pipeline.queue_analysis(db, ids["job7"], ids["raw7"], ids["model"], NOW) is False
    assert db.execute("SELECT count(*) FROM posting_analyses").fetchone()[0] == 1 and row(db, aid)["status"] == "done"


def test_analysis_code_never_reads_the_profile():
    """Pins D10 at the source: if E0-E2 ever read profile tables, an analysis would silently
    depend on the profile without being keyed by it."""
    root = Path(__file__).parents[1] / "src" / "jobforge_agent"
    profile_tables = re.compile(r"\b(FROM|JOIN|INTO|UPDATE)\s+(experience_items|profile_snapshot\w*|profile_completeness"
                                r"|inferred_claims|resumes)\b", re.I)
    offenders = [str(p.relative_to(root)) for d in ("analysis", "inference") for p in (root / d).glob("*.py")
                 if profile_tables.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_logs_carry_ids_and_counts_not_posting_text(env, caplog, capsys):
    db, ids, make = env
    queue(db, ids)
    with caplog.at_level(logging.INFO):
        asyncio.run(make(FakeServer(("ok", GOOD))).run_once())
    text = logged(caplog, capsys)
    assert "analysis_done" in text
    assert "quokka" not in text.lower() and "citizen" not in text.lower()


# ----- E1 schema -----

def _enum_fields(schema, path=""):
    if isinstance(schema, dict):
        if "enum" in schema:
            yield path, schema["enum"]
        for k, v in schema.items():
            yield from _enum_fields(v, f"{path}.{k}" if k not in ("properties", "items") else path)


def shared_enum_values(schema) -> dict:
    seen: dict[str, str] = {}
    shared = {}
    for path, values in _enum_fields(schema):
        for v in values:
            if v in seen and seen[v] != path:
                shared[v] = (seen[v], path)
            seen.setdefault(v, path)
    return shared


def test_schema_lint_no_enum_value_in_two_fields():
    """Spike P1.0b draft 1: "required" in both importance and polarity made models label ~95% negated."""
    assert shared_enum_values(extract.SCHEMA) == {}
    draft1 = {"properties": {"importance": {"enum": ["hard", "required", "preferred"]}, "polarity": {"enum": ["required", "negated"]}}}
    assert "required" in shared_enum_values(draft1)  # the lint catches the original defect


def test_parse_validates_whatever_the_server_claims():
    assert extract.parse(json.dumps(GOOD))[0].eligibility_type == "work_authorization"
    for bad in ("[]", json.dumps({"requirements": [{**item("x"), "extra": 1}]}),
                json.dumps({"requirements": [{**item("x"), "eligibility_type": "citizenship"}]}),
                json.dumps({"requirements": [{k: v for k, v in item("x").items() if k != "min_years"}]})):
        with pytest.raises(extract.InvalidOutput):
            extract.parse(bad)


def test_request_body_sets_max_tokens_reasoning_and_schema():
    from jobforge_agent.inference.client import ModelConfig, request_body
    cfg = ModelConfig(1, "g", "http://127.0.0.1:1234/v1", MODEL, {"reasoning_effort": "none", "max_tokens": 4000, "seed": 7}, "json_schema", True, NOW)
    body = request_body(cfg, extract.messages("T", "x", "json_schema"), extract.response_format("json_schema"))
    assert (body["max_tokens"], body["reasoning_effort"], body["seed"], body["temperature"]) == (4000, "none", 7, 0)
    assert body["response_format"]["json_schema"]["schema"] is extract.SCHEMA


# ----- IPC: model config test / enable, analysis reads -----

def _run_with_fake(paths, server, coro_fn):
    runtime = AgentRuntime(paths)
    runtime.inference_transport = httpx.MockTransport(server)
    runtime.start()
    server.watch += [runtime.user, runtime.evaluation_conn, runtime.discovery_conn, runtime.lifecycle]
    try:
        async def go():
            runtime.stop_event = asyncio.Event()
            app = ipc.create_app(runtime, ipc.IpcEndpoint(PORT, TOKEN))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                try:
                    return await coro_fn(client, runtime)
                finally:
                    await runtime.inference_client().aclose()
        return asyncio.run(go())
    finally:
        runtime.finish()


SNIPPET_OK = {"requirements": [item("Must be a US citizen", importance="hard", eligibility_type="work_authorization"),
                               item("3+ years of Linux administration experience", min_years=3),
                               item("CCNA certification is a plus", importance="preferred")]}


def test_model_config_test_then_enable_then_analysis_reads(paths):
    server = FakeServer(("ok", SNIPPET_OK), ("ok", GOOD))

    async def check(client, runtime):
        configs = (await client.get("/v1/model-configs", headers=AUTH)).json()
        default = [c for c in configs if c["default"]][0]
        assert default["model_id"] == MODEL and default["enabled"] is False and default["tested_at_ms"] is None
        cid = default["id"]
        assert (await client.post(f"/v1/model-configs/{cid}/enable", headers=AUTH)).status_code == 409
        report = (await client.post(f"/v1/model-configs/{cid}/test", headers=AUTH)).json()
        assert report["ok"] is True and report["listed"] is True and len(report["requirements"]) == 3
        assert [r["final_class"] for r in report["requirements"]] == ["hard", "negotiable", "preferred"]
        assert (await client.post(f"/v1/model-configs/{cid}/enable", headers=AUTH)).json() == {"model_config_id": cid, "enabled": True}
        assert (await client.post("/v1/model-configs/999/enable", headers=AUTH)).status_code == 404

        owner = open_connection(paths.db, "migration")
        ids = seed_basics(owner)
        job, raw = add_job(owner, ids, 7, "NOC Engineer", desc=POSTING)
        aid = owner.execute("INSERT INTO posting_analyses (raw_posting_id, job_id, model_config_id, extraction_prompt_version, "
                            "segmenter_version, interpreter_version, created_at_ms) VALUES (?, ?, ?, 'e1-v1', 'seg-v1', 'interp-v1', ?) "
                            "RETURNING id", (raw, job, cid, NOW)).fetchone()[0]
        owner.close()
        status = (await client.get("/v1/analysis/status", headers=AUTH)).json()
        assert status["counts"] == {"pending": 1} and status["configs_with_work"] == [cid]
        worker = AnalysisWorker(runtime.evaluation_conn, runtime.inference_client())
        assert await worker.run_once() == "done"
        a = (await client.get(f"/v1/analyses/{aid}", headers=AUTH)).json()
        assert a["status"] == "done" and len(a["requirements"]) == 3 and a["sections"]["headings"] == 2
        assert (await client.get(f"/v1/jobs/{job}/analysis", headers=AUTH)).json()["id"] == aid
        assert (await client.get("/v1/analyses/999", headers=AUTH)).status_code == 404
        types = [r[0] for r in runtime.reader.execute("SELECT type FROM change_events ORDER BY id")]
        assert ["model_config_tested", "model_config_enabled", "analysis_done"] == [t for t in types if t.startswith(("model_", "analysis_"))]

    _run_with_fake(paths, server, check)
    assert server.in_tx_during_call == [False, False]


def test_failed_model_test_leaves_the_config_untested(paths):
    bad = {"requirements": [item("Must hold a TS/SCI clearance", importance="hard", eligibility_type="clearance")]}  # not in the snippet

    async def check(client, runtime):
        cid = [c for c in (await client.get("/v1/model-configs", headers=AUTH)).json() if c["default"]][0]["id"]
        report = (await client.post(f"/v1/model-configs/{cid}/test", headers=AUTH)).json()
        assert report["ok"] is False and "quote" in report["error"]
        assert (await client.post(f"/v1/model-configs/{cid}/enable", headers=AUTH)).status_code == 409

    _run_with_fake(paths, FakeServer(("ok", bad)), check)


def test_prompt_changes_require_a_version_bump():
    """Analyses are keyed by EXTRACTION_PROMPT_VERSION (D10). A prompt or schema edit without a
    bump would silently mix two prompts under one version. If this fails: bump the version in
    analysis/versions.py, then update both values here."""
    import hashlib
    from jobforge_agent.analysis.versions import EXTRACTION_PROMPT_VERSION
    digest = hashlib.sha256((extract.SYSTEM + json.dumps(extract.SCHEMA, sort_keys=True)
                             + extract.user_message("T", "X")).encode()).hexdigest()[:16]
    assert (EXTRACTION_PROMPT_VERSION, digest) == ("e1-v1", "4fb42bf9ca058f7e")
