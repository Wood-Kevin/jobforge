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
