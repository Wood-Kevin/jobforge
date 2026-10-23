"""Window read models, applications and the no-outbound-request rule (P1.8; ARCHITECTURE 5, 8; SPEC section 2)."""
import asyncio
import json
import re
from pathlib import Path

import pytest

from conftest import NOW
from jobforge_agent import applications, views
from jobforge_agent.db.connect import open_connection
from jobforge_agent.profile import items as profile_items
from test_analysis import FakeServer, _run_with_fake
from test_evaluation import E1, SEARCH, e3, env, evals, profile_basic  # noqa: F401  (fixture reused)
from test_filter_pipeline import add_job
from test_ipc import AUTH

ROOT = Path(__file__).resolve().parents[2]


def evaluated(env):
    role, skill, fact = profile_basic(env)
    _, e = env.analyze(FakeServer(("ok", E1), ("ok", e3(("R1", "direct", [f"P{role}"], None, None)))))
    assert asyncio.run(e.run_once()) == "done"
    return role, skill, fact


def test_job_lists_by_view(env):
    evaluated(env)
    rejected_job, _ = add_job(env.db, env.ids, 30, "Senior Sales Director", location="Paris")
    env.db.execute("INSERT INTO filter_results (job_id, raw_posting_id, search_profile_id, ruleset_version, verdict, reasons, "
                   "created_at_ms) VALUES (?, (SELECT current_raw_posting_id FROM jobs WHERE id = ?), ?, (SELECT ?), 'reject', ?, ?)",
                   (rejected_job, rejected_job, env.ids["profile"], _ruleset(), json.dumps({"primary": "TITLE_NO_MATCH"}), NOW))
    rec = views.list_jobs(env.ev, "recommended", None, 50, 0)
    assert [j["id"] for j in rec["jobs"]] == [env.ids["job"]] and rec["jobs"][0]["recommendation"]["current"] is True
    rej = views.list_jobs(env.ev, "rejected", None, 50, 0)
    assert [(j["id"], j["filter_primary"]) for j in rej["jobs"]] == [(rejected_job, "TITLE_NO_MATCH")]
    assert views.list_jobs(env.ev, "all", "sales", 50, 0)["total"] == 1
    with pytest.raises(ValueError):
        views.list_jobs(env.ev, "everything", None, 50, 0)


def _ruleset():
    from jobforge_agent.filtering.rules import RULESET_VERSION
    return RULESET_VERSION


def test_job_detail_shows_the_statements_the_evaluation_saw(env):
    role, _, _ = evaluated(env)
    env.user_tx(profile_items.update_item, role, {"statement": "Edited after the evaluation"})
    d = views.job_detail(env.ev, env.ids["job"])
    assert d["description_text"] and d["filter"]["verdict"] == "pass" and d["analysis"]["status"] == "done"
    assert d["evaluation"]["cited_items"][str(role)]["statement"] == "NOC Tier I engineer"  # the snapshot, not today's text
    assert views.job_detail(env.ev, 99999) is None


def test_application_status_changes_append_events(env):
    job = env.ids["job"]
    a = env.user_tx(applications.set_for_job, job, {"status": "saved"}, NOW)
    a = env.user_tx(applications.set_for_job, job, {"notes": "recruiter is Sam"}, NOW + 1)  # notes only: no event
    a = env.user_tx(applications.set_for_job, job, {"status": "applied", "note": "via company site"}, NOW + 2)
    assert [(e["from_status"], e["to_status"], e["note"]) for e in a["events"]] == [(None, "saved", None), ("saved", "applied", "via company site")]
    assert (a["status"], a["notes"]) == ("applied", "recruiter is Sam")
    with pytest.raises(applications.ApplicationError):
        env.user_tx(applications.set_for_job, job, {"status": "hired"}, NOW)
    with pytest.raises(LookupError):
        env.user_tx(applications.set_for_job, 99999, {"status": "saved"}, NOW)
    with pytest.raises(Exception, match="append-only"):
        env.db.execute("UPDATE application_events SET to_status = 'offer'")
    with pytest.raises(Exception, match="UNIQUE"):
        env.db.execute("INSERT INTO applications (job_id, status, created_at_ms) VALUES (?, 'saved', ?)", (job, NOW))


def test_window_routes(paths):
    async def check(client, runtime):
        owner = open_connection(paths.db, "migration")
        from conftest import seed_basics
        ids = seed_basics(owner)
        owner.execute("INSERT INTO search_profiles (version, rules, created_at_ms) VALUES (1, ?, ?)", (json.dumps(SEARCH), NOW))
        raw = owner.execute(
            "INSERT INTO raw_postings (employer_id, source_job_id, fetched_at_ms, payload, description_raw, description_text, "
            "payload_sha256, material_sha256, adapter_version) VALUES (?, 'greenhouse:acme:7', ?, '{}', ?, 'Hello', 'p7', 'm7', 'gh-1') "
            "RETURNING id", (ids["employer"], NOW, "<p>Hello <script>alert(1)</script></p>")).fetchone()[0]
        job = owner.execute(
            "INSERT INTO jobs (source_job_id, employer_id, current_raw_posting_id, last_payload_sha256, title, company, "
            "first_seen_at_ms, last_seen_at_ms) VALUES ('greenhouse:acme:7', ?, ?, 'p7', 'NOC Engineer', 'Acme', ?, ?) RETURNING id",
            (ids["employer"], raw, NOW, NOW)).fetchone()[0]
        owner.close()
        assert (await client.get("/v1/jobs?view=all", headers=AUTH)).json()["total"] == 2  # + the seeded job
        assert (await client.get("/v1/jobs?view=nope", headers=AUTH)).status_code == 422
        d = (await client.get(f"/v1/jobs/{job}", headers=AUTH)).json()
        assert "<script>" in d["description_html"]  # raw from the agent; the window sanitizes (test in desktop/)
        assert (await client.get("/v1/jobs/999", headers=AUTH)).status_code == 404
        r = await client.put(f"/v1/jobs/{job}/application", headers=AUTH, json={"status": "applied"})
        assert r.status_code == 200 and r.json()["events"][0]["to_status"] == "applied"
        assert (await client.put(f"/v1/jobs/{job}/application", headers=AUTH, json={"status": "x"})).status_code == 422
        assert (await client.put("/v1/jobs/999/application", headers=AUTH, json={"status": "saved"})).status_code == 404
        assert [a["status"] for a in (await client.get("/v1/applications", headers=AUTH)).json()] == ["applied"]
        types = [x[0] for x in runtime.reader.execute("SELECT type FROM change_events ORDER BY id")]
        assert "application_changed" in types
        schema = (await client.get("/v1/profile/schema", headers=AUTH)).json()
        assert set(schema["eligibility"]) == set(profile_items.ELIGIBILITY)
        assert schema["eligibility"]["clearance"]["level"]["required"] is True

    _run_with_fake(paths, FakeServer(), check)


# ----- nothing submits to an employer (P1.8 acceptance) -----

def test_outbound_http_only_in_discovery_and_inference():
    """The agent may make requests only to job boards (discovery/) and the local model server
    (inference/); the host only to the agent on 127.0.0.1; the window never makes a request at all."""
    src = ROOT / "agent" / "src" / "jobforge_agent"
    http = re.compile(r"^\s*(import httpx|from httpx|import requests|from requests|import urllib\.request|from urllib\.request|"
                      r"import aiohttp|from aiohttp|import http\.client|from http\.client)", re.M)
    users = sorted(str(p.relative_to(src)).replace("\\", "/") for p in src.rglob("*.py") if http.search(p.read_text(encoding="utf-8")))
    # main.py is the --status/--shutdown command line: it talks only to this agent on loopback.
    loopback_only = {"main.py"}
    assert users and all(u.startswith(("discovery/", "inference/")) or u in loopback_only for u in users), users
    for u in loopback_only & set(users):
        text = (src / u).read_text(encoding="utf-8")
        assert "http://127.0.0.1:" in text and not re.search(r"https?://(?!127\.0\.0\.1)", text), u
    rust = ROOT / "desktop" / "src-tauri" / "src"
    ureq_users = sorted(p.name for p in rust.glob("*.rs") if "ureq::" in p.read_text(encoding="utf-8"))
    assert ureq_users == ["supervisor.rs"], ureq_users
    assert "http://127.0.0.1:" in (rust / "supervisor.rs").read_text(encoding="utf-8")
    assert not re.search(r"https?://(?!127\.0\.0\.1)", re.sub(r"(?m)^\s*//.*$", "", (rust / "supervisor.rs").read_text(encoding="utf-8")))
    web = ROOT / "desktop" / "src"
    for p in web.rglob("*.ts*"):
        text = re.sub(r"(?m)^\s*//.*$|/\*[\s\S]*?\*/", "", p.read_text(encoding="utf-8"))  # code, not comments
        assert not re.search(r"\bfetch\(|XMLHttpRequest|WebSocket\(|navigator\.sendBeacon|\bEventSource\(", text), p.name


def test_every_route_the_window_calls_exists():
    """SPEC D14 asked for UI types generated from OpenAPI; the routes return plain dicts, so there is
    nothing to generate (corrected in P1.8). This keeps the part that matters: a renamed or removed
    route, or a wrong method, fails here instead of in the window."""
    from fastapi.routing import APIRoute
    from jobforge_agent import ipc
    from jobforge_agent.agent import AgentRuntime
    from jobforge_agent.paths import Paths
    app = ipc.create_app(AgentRuntime(Paths(ROOT / "unused")), ipc.IpcEndpoint(1, "t"))
    served = [(m, re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", r.path) + "$"))
              for r in app.routes if isinstance(r, APIRoute) for m in r.methods]
    api_ts = (ROOT / "desktop" / "src" / "api.ts").read_text(encoding="utf-8")
    calls = re.findall(r'agentCall<[^(]*>\(\s*"(GET|POST|PUT|PATCH|DELETE)",\s*[`"]([^`"]+)[`"]', api_ts)
    calls += [("POST", p) for p in re.findall(r'agentUpload<[^(]*>\(\s*`([^`]+)`', api_ts)]
    assert len(calls) == len(re.findall(r"agent(?:Call|Upload)<", api_ts)), "a call in api.ts was not parsed"
    missing = []
    for method, path in calls:
        concrete = re.sub(r"\$\{[^}]+\}", "1", path).split("?")[0]
        if not any(m == method and rx.match(concrete) for m, rx in served):
            missing.append(f"{method} {path}")
    assert missing == [], missing
