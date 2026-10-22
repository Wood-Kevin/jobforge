"""IPC guard and handler rules (SPEC D11, spike P1.0d defects 4 and 5).

Runs the app in-process over httpx's ASGI transport on the same thread that opened the
SQLite connections, which is how uvicorn runs it in the agent.
"""
import asyncio
import inspect

import httpx

from jobforge_agent import PROTOCOL_VERSION, ipc
from jobforge_agent.agent import AgentRuntime

PORT = 50123
TOKEN = "t" * 64
BASE = f"http://127.0.0.1:{PORT}"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _run(paths, coro_fn):
    runtime = AgentRuntime(paths)
    runtime.start()
    try:
        async def go():
            runtime.stop_event = asyncio.Event()
            app = ipc.create_app(runtime, ipc.IpcEndpoint(PORT, TOKEN))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as client:
                return await coro_fn(client, runtime, app)
        return asyncio.run(go())
    finally:
        runtime.finish()


def _run_live(paths, coro_fn):
    """Like _run, but over a real uvicorn server on a loopback socket (same loop and thread).

    httpx's ASGITransport buffers the whole response body, so it cannot read an endless SSE
    stream; a real socket can.
    """
    import uvicorn

    runtime = AgentRuntime(paths)
    runtime.start()
    try:
        async def go():
            runtime.stop_event = asyncio.Event()
            sock = ipc.bind_loopback()
            port = sock.getsockname()[1]
            app = ipc.create_app(runtime, ipc.IpcEndpoint(port, TOKEN))
            server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="off", timeout_graceful_shutdown=2))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            while not server.started:
                await asyncio.sleep(0.01)
            try:
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
                    return await asyncio.wait_for(coro_fn(client, runtime, app), 15)
            finally:
                runtime.request_stop("test_end")
                server.should_exit = True
                await task
        return asyncio.run(go())
    finally:
        runtime.finish()


def test_missing_or_wrong_token_is_401(paths):
    async def check(client, *_):
        assert (await client.get("/v1/health")).status_code == 401
        assert (await client.get("/v1/health", headers={"Authorization": "Bearer nope"})).status_code == 401
    _run(paths, check)


def test_foreign_host_is_400_even_with_token(paths):
    async def check(client, *_):
        r = await client.get("/v1/health", headers={**AUTH, "Host": "evil.example"})
        assert r.status_code == 400
    _run(paths, check)


def test_health_reports_version_schema_and_elevation(paths):
    async def check(client, runtime, _app):
        r = await client.get("/v1/health", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["protocol_version"] == PROTOCOL_VERSION
        assert body["schema"] == 1 and body["db"] == "ok" and body["run_id"] == runtime.run_id
        assert body["discovery_paused"] is False
        assert isinstance(body["elevated"], bool)
    _run(paths, check)


def test_shutdown_without_reason_is_not_422(paths):
    """Spike defect 5: a locally imported Request made FastAPI demand a query parameter."""
    async def check(client, runtime, _app):
        r = await client.post("/v1/shutdown", headers=AUTH)
        assert r.status_code == 200 and r.json() == {"stopping": True}
        assert runtime.stop_event.is_set() and runtime.stop_reason == "requested"
        await client.post("/v1/shutdown?reason=user_exit", headers=AUTH)
        assert runtime.stop_reason == "user_exit"
    _run(paths, check)


def test_unknown_path_with_token_is_404(paths):
    async def check(client, *_):
        assert (await client.get("/wp-admin", headers=AUTH)).status_code == 404
        assert (await client.get("/.env", headers=AUTH)).status_code == 404
    _run(paths, check)


def test_every_route_handler_is_async(paths):
    """Spike defect 4: plain def handlers run in a worker thread and cannot use the loop-owned SQLite connections."""
    async def check(_client, _runtime, app):
        handlers = [r.endpoint for r in app.routes if getattr(r, "path", "").startswith("/v1/")]
        assert handlers, "no /v1 routes found"
        sync = [h.__name__ for h in handlers if not inspect.iscoroutinefunction(h)]
        assert sync == [], f"sync handlers: {sync}"
    _run(paths, check)


def test_fastapi_types_imported_at_module_level():
    assert "Request" in vars(ipc) and "FastAPI" in vars(ipc)


def test_ipc_file_round_trip(paths):
    ipc.write_ipc_file(paths.ipc, ipc.IpcEndpoint(PORT, TOKEN), 4242)
    data = ipc.read_ipc_file(paths.ipc)
    assert data == {"port": PORT, "token": TOKEN, "pid": 4242, "protocol_version": PROTOCOL_VERSION}
    assert not paths.ipc.with_suffix(".json.tmp").exists()


def test_bind_is_loopback_only():
    sock = ipc.bind_loopback()
    try:
        assert sock.getsockname()[0] == "127.0.0.1" and sock.getsockname()[1] > 0
    finally:
        sock.close()


def test_pause_and_resume_persist_and_emit_events(paths):
    async def check(client, runtime, _app):
        r = await client.post("/v1/discovery/pause", headers=AUTH)
        assert r.status_code == 200 and r.json()["discovery_paused"] is True
        assert runtime.reader.execute("SELECT value FROM settings WHERE key = 'discovery_paused'").fetchone()[0] == "1"
        await client.post("/v1/discovery/resume", headers=AUTH)
        types = [e["type"] for e in runtime.events_after(0)]
        assert types == ["discovery_paused", "discovery_resumed"]
    _run(paths, check)


async def _read_events(response, n: int) -> list[str]:
    """Collect the first n SSE data lines."""
    found = []
    async for line in response.aiter_lines():
        if line.startswith("data: "):
            found.append(line[len("data: "):])
            if len(found) == n:
                break
    return found


def test_event_stream_resumes_after_last_event_id(paths):
    import json as _json

    async def check(client, runtime, _app):
        runtime.set_discovery_paused(True)
        runtime.set_discovery_paused(False)
        first_id = runtime.events_after(0)[0]["id"]
        async with client.stream("GET", "/v1/events", headers={**AUTH, "Last-Event-ID": str(first_id)}) as r:
            assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
            got = await asyncio.wait_for(_read_events(r, 1), 5)
        assert _json.loads(got[0])["type"] == "discovery_resumed"
    _run_live(paths, check)


def test_new_subscriber_gets_only_future_events_pushed_without_waiting_for_poll(paths):
    import json as _json
    import time as _time

    async def check(client, runtime, _app):
        runtime.set_discovery_paused(True)  # before subscribing: must not be replayed
        async with client.stream("GET", "/v1/events", headers=AUTH) as r:
            async def later():
                await asyncio.sleep(0.2)
                runtime.set_discovery_paused(False)
            asyncio.create_task(later())
            t0 = _time.monotonic()
            got = await asyncio.wait_for(_read_events(r, 1), 5)
            elapsed = _time.monotonic() - t0
        assert _json.loads(got[0])["type"] == "discovery_resumed"
        assert elapsed < 1.5, f"event took {elapsed:.2f} s; the commit notification did not wake the stream"
    _run_live(paths, check)


def test_event_stream_ends_when_agent_stops(paths):
    async def check(client, runtime, _app):
        async with client.stream("GET", "/v1/events", headers=AUTH) as r:
            async def stop_soon():
                await asyncio.sleep(0.2)
                runtime.request_stop("test")
            asyncio.create_task(stop_soon())
            lines = [line async for line in r.aiter_lines()]  # returns only if the server ends the stream
        assert lines[0] == ": connected"
    _run_live(paths, check)


def test_clean_stop_leaves_no_wal_file(paths):
    """Committed rows must be in jobforge.db after a clean stop, not stranded in the WAL."""
    runtime = AgentRuntime(paths)
    runtime.start()
    runtime.set_discovery_paused(True)
    runtime.stop_reason = "test"
    runtime.finish()
    assert not paths.db.with_name("jobforge.db-wal").exists()
    import sqlite3
    conn = sqlite3.connect(paths.db)
    assert conn.execute("SELECT stop_reason FROM agent_runs").fetchone()[0] == "test"
    conn.close()
