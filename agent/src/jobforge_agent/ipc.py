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
from fastapi.responses import JSONResponse, StreamingResponse

from jobforge_agent import PROTOCOL_VERSION

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
