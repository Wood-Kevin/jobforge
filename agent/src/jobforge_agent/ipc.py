"""Local IPC: HTTP/JSON on an ephemeral 127.0.0.1 port, per-start bearer token (SPEC D11).

Rules from spike P1.0d, each pinned by a test:
- FastAPI types are imported at module level. With postponed annotations FastAPI resolves
  them from module globals; imported inside a function, `Request` became a query parameter
  and /v1/shutdown answered 422.
- Handlers that touch the database are `async def`, so they run on the event-loop thread
  that owns the SQLite connections. Plain `def` handlers run in a worker thread and fail.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from jobforge_agent import PROTOCOL_VERSION, applications as applications_mod, views
from jobforge_agent.evaluation import golden
from jobforge_agent.filtering.profile import ProfileError
from jobforge_agent.inference import configs as model_configs
from jobforge_agent.inference.client import InferenceError
from jobforge_agent.profile import items, resumes, suggest

if TYPE_CHECKING:
    from jobforge_agent.agent import AgentRuntime


@dataclass(frozen=True)
class IpcEndpoint:
    port: int
    token: str


def new_token() -> str:
    return secrets.token_hex(32)


def bind_loopback() -> socket.socket:
    """Bind 127.0.0.1 on a port the OS picks. Never 0.0.0.0."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    return sock


def write_ipc_file(path: Path, endpoint: IpcEndpoint, pid: int) -> None:
    """Atomic replace, so the host never reads a half-written file."""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({
        "port": endpoint.port, "token": endpoint.token, "pid": pid, "protocol_version": PROTOCOL_VERSION,
    }), encoding="utf-8")
    os.replace(tmp, path)


def read_ipc_file(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def create_app(runtime: "AgentRuntime", endpoint: IpcEndpoint) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    expected_host = f"127.0.0.1:{endpoint.port}"
    expected_auth = f"Bearer {endpoint.token}"

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Host first: blocks DNS-rebinding pages even if they somehow had a token.
        if request.headers.get("host") != expected_host:
            return JSONResponse({"error": "bad host"}, status_code=400)
        if not secrets.compare_digest(request.headers.get("authorization", ""), expected_auth):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/v1/health")
    async def health():
        return runtime.health()

    @app.post("/v1/shutdown")
    async def shutdown(request: Request):
        runtime.request_stop(request.query_params.get("reason", "requested"))
        return {"stopping": True}

    @app.post("/v1/discovery/pause")
    async def pause_discovery():
        return runtime.set_discovery_paused(True)

    @app.post("/v1/discovery/resume")
    async def resume_discovery():
        return runtime.set_discovery_paused(False)

    @app.get("/v1/discovery/status")
    async def discovery_status():
        return runtime.discovery_status()

    @app.post("/v1/admin/employers/import")
    async def import_employers(request: Request):
        body = await request.json()
        entries = body.get("employers") if isinstance(body, dict) else None
        if not isinstance(entries, list):
            return JSONResponse({"error": "expected an object with an 'employers' list"}, status_code=422)
        return runtime.import_employers(entries)

    @app.post("/v1/admin/employers/{employer_id}/poll-now")
    async def poll_now(employer_id: int):
        if not runtime.poll_now(employer_id):
            return JSONResponse({"error": "unknown employer"}, status_code=404)
        return {"due": True}

    @app.get("/v1/search-profile")
    async def get_search_profile():
        profile = runtime.search_profile()
        if profile is None:
            return JSONResponse({"error": "no search profile yet"}, status_code=404)
        return profile

    @app.post("/v1/search-profile")
    async def save_search_profile(request: Request):
        body = await request.json()
        rules = body.get("rules") if isinstance(body, dict) else None
        try:
            return runtime.save_search_profile(rules)
        except ProfileError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/v1/admin/refilter")
    async def refilter():
        return {"refilter": runtime.start_refilter()}

    @app.get("/v1/filter/summary")
    async def filter_summary():
        return runtime.filter_summary()

    @app.post("/v1/jobs/{job_id}/evaluate-anyway")
    async def evaluate_anyway(job_id: int, request: Request):
        body = await request.json() if request.headers.get("content-length", "0") not in ("", "0") else {}
        result = runtime.evaluate_anyway(job_id, (body or {}).get("note"))
        if result is None:
            return JSONResponse({"error": "unknown job"}, status_code=404)
        return result

    # ----- profile: resumes, experience items, verify, completeness, inferred claims -----

    def _profile_call(fn):
        try:
            return fn()
        except (items.ProfileInputError, resumes.ResumeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except items.NotFound as exc:
            return JSONResponse({"error": f"unknown {exc}"}, status_code=404)
        except items.Conflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    async def _body(request: Request):
        if request.headers.get("content-length", "0") in ("", "0"):
            return {}
        body = await request.json()
        return body if isinstance(body, dict) else None

    def _bad_body():
        return JSONResponse({"error": "request body must be a JSON object"}, status_code=422)

    def _now() -> int:
        return int(time.time() * 1000)

    @app.post("/v1/resumes")
    async def upload_resume(request: Request, filename: str, label: str | None = None):
        # Raw bytes in the body (no multipart, no base64): what is stored is exactly what was sent.
        size = int(request.headers.get("content-length") or 0)
        if size > resumes.MAX_BYTES:
            return JSONResponse({"error": f"file is {size} bytes; the limit is {resumes.MAX_BYTES}"}, status_code=413)
        data = await request.body()
        return _profile_call(lambda: runtime.upload_resume(filename, label, data))

    @app.get("/v1/resumes")
    async def list_resumes():
        return runtime.profile_read(resumes.listing)

    @app.get("/v1/resumes/{resume_id}")
    async def get_resume(resume_id: int):
        r = runtime.profile_read(resumes.get, resume_id)
        return r if r else JSONResponse({"error": "unknown resume"}, status_code=404)

    @app.get("/v1/resumes/{resume_id}/original")
    async def get_resume_original(resume_id: int):
        r = runtime.profile_read(lambda c, i: resumes.get(c, i, original=True), resume_id)
        if not r:
            return JSONResponse({"error": "unknown resume"}, status_code=404)
        return Response(r["original_bytes"], media_type=r["mime"], headers={"X-Sha256": r["sha256"]})

    @app.post("/v1/resumes/{resume_id}/suggest-items")
    async def suggest_items(resume_id: int):
        # Slow by nature (a model reads the whole resume): the host allows this path a longer timeout.
        try:
            r = await runtime.suggest_from_resume(resume_id)
        except suggest.SuggestError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except InferenceError as exc:
            return JSONResponse({"error": f"the AI model did not answer ({exc.kind}: {exc.detail})"}, status_code=503)
        return r if r is not None else JSONResponse({"error": "unknown resume"}, status_code=404)

    @app.delete("/v1/resumes/{resume_id}")
    async def delete_resume(resume_id: int):
        def go():
            if not runtime.profile_action("resume_deleted", resumes.delete, resume_id, ref={"id": resume_id}):
                raise items.NotFound(f"resume {resume_id}")
            return {"deleted": resume_id}
        return _profile_call(go)

    @app.get("/v1/experience")
    async def list_experience():
        return runtime.profile_read(items.list_items)

    @app.post("/v1/experience")
    async def create_experience(request: Request):
        body = await _body(request)
        if body is None:
            return _bad_body()
        return _profile_call(lambda: {"id": runtime.profile_action("item_created", items.create_item, body)})

    @app.patch("/v1/experience/{item_id}")
    async def update_experience(item_id: int, request: Request):
        body = await _body(request)
        if body is None:
            return _bad_body()
        return _profile_call(lambda: runtime.profile_action("item_updated", items.update_item, item_id, body, ref={"id": item_id}))

    @app.delete("/v1/experience/{item_id}")
    async def delete_experience(item_id: int):
        def go():
            runtime.profile_action("item_deleted", items.delete_item, item_id, ref={"id": item_id})
            return {"deleted": item_id}
        return _profile_call(go)

    @app.post("/v1/experience/{item_id}/verify")
    async def verify_experience(item_id: int, request: Request):
        body = await _body(request)
        if body is None:
            return _bad_body()

        def go():
            runtime.profile_action("item_verified", items.verify_item, item_id, body.get("note"), _now(), ref={"id": item_id})
            return runtime.profile_read(items.get_item, item_id)
        return _profile_call(go)

    @app.post("/v1/experience/{item_id}/unverify")
    async def unverify_experience(item_id: int):
        def go():
            runtime.profile_action("item_unverified", items.unverify_item, item_id, ref={"id": item_id})
            return runtime.profile_read(items.get_item, item_id)
        return _profile_call(go)

    @app.get("/v1/profile/completeness")
    async def get_completeness():
        return runtime.profile_read(items.get_completeness)

    @app.put("/v1/profile/completeness")
    async def set_completeness(request: Request):
        body = await _body(request)
        if body is None:
            return _bad_body()
        return _profile_call(lambda: runtime.profile_action("completeness_set", items.set_completeness, body, _now()))

    @app.get("/v1/inferred-claims")
    async def list_claims(state: str | None = None):
        return _profile_call(lambda: runtime.profile_read(items.list_claims, state))

    @app.post("/v1/inferred-claims/{claim_id}/accept")
    async def accept_claim(claim_id: int, request: Request):
        body = await _body(request)
        if body is None:
            return _bad_body()
        return _profile_call(lambda: {"item_id": runtime.profile_action("claim_accepted", items.accept_claim, claim_id, body,
                                                                        ref={"claim_id": claim_id})})

    @app.post("/v1/inferred-claims/{claim_id}/reject")
    async def reject_claim(claim_id: int):
        def go():
            runtime.profile_action("claim_rejected", items.reject_claim, claim_id, ref={"claim_id": claim_id})
            return {"rejected": claim_id}
        return _profile_call(go)

    # ----- model configs and posting analysis (E0-E2) -----

    def _config_toggle(config_id: int, enabled: bool):
        try:
            runtime.set_model_config_enabled(config_id, enabled)
        except LookupError:
            return JSONResponse({"error": "unknown model config"}, status_code=404)
        except model_configs.ConfigError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return {"model_config_id": config_id, "enabled": enabled}

    @app.get("/v1/model-configs")
    async def list_model_configs():
        return runtime.model_configs()

    @app.post("/v1/model-configs/{config_id}/test")
    async def test_model_config(config_id: int):
        report = await runtime.test_model_config(config_id)
        return report if report is not None else JSONResponse({"error": "unknown model config"}, status_code=404)

    @app.post("/v1/model-configs/{config_id}/enable")
    async def enable_model_config(config_id: int):
        return _config_toggle(config_id, True)

    @app.post("/v1/model-configs/{config_id}/disable")
    async def disable_model_config(config_id: int):
        return _config_toggle(config_id, False)

    @app.get("/v1/analysis/status")
    async def analysis_status():
        return runtime.analysis_status()

    @app.get("/v1/analyses/{analysis_id}")
    async def get_analysis(analysis_id: int):
        a = runtime.analysis_view(analysis_id)
        return a if a is not None else JSONResponse({"error": "unknown analysis"}, status_code=404)

    @app.get("/v1/jobs/{job_id}/analysis")
    async def get_job_analysis(job_id: int):
        a = runtime.job_analysis(job_id)
        return a if a is not None else JSONResponse({"error": "no finished analysis for this job"}, status_code=404)

    # ----- first-run setup (P1.8b): plain inputs, never JSON or patterns from the user -----

    async def _text_body(request: Request) -> str | None:
        body = await _body(request)
        return body.get("text") if isinstance(body, dict) and isinstance(body.get("text"), str) else None

    @app.get("/v1/setup/status")
    async def setup_status():
        return runtime.setup_status()

    @app.get("/v1/search-profile/form")
    async def search_profile_form():
        return runtime.search_profile_form()

    @app.post("/v1/search-profile/form")
    async def save_search_profile_form(request: Request):
        body = await _body(request)
        if body is None:
            return _bad_body()
        try:
            return runtime.save_search_profile_form(body)
        except ProfileError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/v1/search-profile/import-scanner")
    async def import_scanner_config(request: Request):
        text = await _text_body(request)
        if text is None:
            return JSONResponse({"error": "send the file's text as {\"text\": ...}"}, status_code=422)
        try:
            return runtime.import_scanner_config(text)
        except (ValueError, ProfileError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/v1/employers/add")
    async def add_employers(request: Request):
        text = await _text_body(request)
        if text is None:
            return JSONResponse({"error": "send the links or names as {\"text\": ...}"}, status_code=422)
        return await runtime.add_employers_from_text(text)

    @app.post("/v1/employers/built-in")
    async def set_built_in_watch(request: Request):
        body = await _body(request)
        if not isinstance(body, dict) or not isinstance(body.get("watch"), bool):
            return JSONResponse({"error": "expected {\"watch\": true|false}"}, status_code=422)
        return runtime.set_built_in_watch(body["watch"])

    @app.post("/v1/employers/import-watchlist")
    async def import_watchlist(request: Request):
        text = await _text_body(request)
        if text is None:
            return JSONResponse({"error": "send the file's text as {\"text\": ...}"}, status_code=422)
        try:
            return runtime.import_watchlist_text(text)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.post("/v1/employers/{employer_id}/enable")
    async def enable_employer(employer_id: int):
        return {"enabled": True} if runtime.set_employer_enabled(employer_id, True) else JSONResponse({"error": "unknown employer"}, status_code=404)

    @app.post("/v1/employers/{employer_id}/disable")
    async def disable_employer(employer_id: int):
        return {"enabled": False} if runtime.set_employer_enabled(employer_id, False) else JSONResponse({"error": "unknown employer"}, status_code=404)

    # ----- window read models and applications (P1.8) -----

    @app.get("/v1/jobs")
    async def list_jobs(view: str = "recommended", q: str | None = None, limit: int = 200, offset: int = 0):
        if view not in views.VIEWS:
            return JSONResponse({"error": f"view must be one of {', '.join(views.VIEWS)}"}, status_code=422)
        return runtime.jobs(view, q, limit, offset)

    @app.get("/v1/jobs/{job_id}")
    async def get_job(job_id: int):
        d = runtime.job_detail(job_id)
        return d if d is not None else JSONResponse({"error": "unknown job"}, status_code=404)

    @app.get("/v1/applications")
    async def list_applications():
        return runtime.applications()

    @app.put("/v1/jobs/{job_id}/application")
    async def set_application(job_id: int, request: Request):
        body = await _body(request)
        if body is None:
            return _bad_body()
        try:
            return runtime.set_application(job_id, body)
        except applications_mod.ApplicationError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except LookupError:
            return JSONResponse({"error": "unknown job"}, status_code=404)

    @app.post("/v1/jobs/{job_id}/golden-label")
    async def golden_label(job_id: int, request: Request):
        body = await _body(request)
        if body is None:
            return _bad_body()
        try:
            return runtime.golden_label(job_id, body)
        except golden.LabelError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)
        except LookupError:
            return JSONResponse({"error": "unknown job"}, status_code=404)

    @app.post("/v1/jobs/{job_id}/golden-label/withdraw")
    async def golden_withdraw(job_id: int):
        try:
            runtime.golden_label(job_id, None)
        except LookupError:
            return JSONResponse({"error": "no label on this job"}, status_code=404)
        return {"withdrawn": True}

    @app.get("/v1/golden/report")
    async def golden_report():
        assert runtime.reader is not None
        return golden.report(runtime.reader)

    @app.get("/v1/profile/schema")
    async def profile_schema():
        # The typed fields of every eligibility fact, so the window builds forms from the agent's
        # own validation table instead of a copy that could drift (P1.5).
        return {"kinds": list(items.KINDS),
                "eligibility": {t: {f: {"type": spec[0][0], "required": spec[1]} for f, spec in fields.items()}
                                for t, fields in items.ELIGIBILITY.items()},
                "multiple_allowed": sorted(items.MULTIPLE_ALLOWED),
                "application_statuses": list(applications_mod.STATUSES)}

    @app.get("/v1/evaluation/status")
    async def evaluation_status():
        return runtime.evaluation_status()

    @app.get("/v1/jobs/{job_id}/recommendation")
    async def get_job_recommendation(job_id: int):
        r = runtime.job_recommendation(job_id)
        return r if r is not None else JSONResponse({"error": "no finished evaluation for this job"}, status_code=404)

    @app.get("/v1/recommendations")
    async def list_recommendations(category: str | None = None):
        if category is not None and category not in ("APPLY", "STRETCH", "REVIEW", "SKIP"):
            return JSONResponse({"error": "category must be APPLY, STRETCH, REVIEW or SKIP"}, status_code=422)
        return runtime.recommendations(category)

    @app.get("/v1/review-questions")
    async def review_questions():
        return runtime.review_questions()

    @app.post("/v1/recompute")
    async def recompute():
        return runtime.recompute()

    @app.get("/v1/events")
    async def events(request: Request):
        """Server-Sent Events over change_events (ARCHITECTURE 3.3, 5).

        Resumes after Last-Event-ID (or ?after=N); a new subscriber without either gets only
        future events. Rows are read after commit, so nothing rolled back is ever sent.
        """
        raw = request.headers.get("last-event-id") or request.query_params.get("after")
        last_id = int(raw) if raw and raw.isdigit() else runtime.latest_event_id()
        return StreamingResponse(_event_stream(runtime, request, last_id), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


EVENT_FALLBACK_POLL_S = 2.0
KEEPALIVE_S = 15.0


async def _event_stream(runtime: "AgentRuntime", request: Request, last_id: int):
    yield ": connected\n\n"
    last_write = time.monotonic()
    while not runtime.is_stopping() and not await request.is_disconnected():
        rows = runtime.events_after(last_id)
        for row in rows:
            last_id = row["id"]
            yield f"id: {row['id']}\nevent: change\ndata: {json.dumps(row)}\n\n"
            last_write = time.monotonic()
        if not rows:
            if time.monotonic() - last_write >= KEEPALIVE_S:
                yield ": keepalive\n\n"
                last_write = time.monotonic()
            await runtime.wait_for_events(EVENT_FALLBACK_POLL_S)
