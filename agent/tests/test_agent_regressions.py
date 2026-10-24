"""Regressions from the 2026-10-24 audit (agent runtime, IPC, backups, inference). Each test fails on
the code before the fix."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time

import httpx
import pytest

from jobforge_agent import agent as agent_mod, main as main_mod
from jobforge_agent.db import backup
from jobforge_agent.db.connect import open_connection
from jobforge_agent.evaluation import match, store
from jobforge_agent.inference.client import InferenceClient
from test_evaluation import _done_once, env, evals  # noqa: F401 (env is a fixture)
from test_ipc import AUTH, _read_events, _run, _run_live


# ----- backups -----

def test_pruning_keeps_the_newest_pre_migrate_backup_past_version_nine(db, paths):
    """Name order put v10 before v5-v9, so pruning deleted the backup just made."""
    for v in range(5, 10):
        backup.backup_to(paths.db, paths.backups / f"pre-migrate-v{v}-20261001-000000.db")
    newest = backup.pre_migrate_backup(paths.db, paths.backups, 10)
    names = sorted(p.name for p in paths.backups.glob("pre-migrate-v*.db"))
    assert newest.exists()
    assert "pre-migrate-v5-20261001-000000.db" not in names and len(names) == backup.PRE_MIGRATE_KEEP


def test_a_failed_restore_leaves_the_live_database_in_place(db, paths, monkeypatch):
    """The live file was deleted before the source was read."""
    db.execute("UPDATE settings SET value = '1' WHERE key = 'discovery_paused'")
    saved = backup.backup_to(paths.db, paths.backups / "manual.db")
    db.close()
    real = backup.integrity
    monkeypatch.setattr(backup, "integrity", lambda p: "ok" if p == saved else ("broken" if "restore-tmp" in str(p) else real(p)))
    with pytest.raises(backup.BackupError, match="restored copy"):
        backup.restore(paths.db, saved, paths.backups)
    assert paths.db.exists() and real(paths.db) == "ok"
    assert not list(paths.db.parent.glob("*.restore-tmp"))


def test_restoring_the_live_file_onto_itself_does_not_lose_it(db, paths):
    db.close()
    backup.restore(paths.db, paths.db, paths.backups)
    conn = open_connection(paths.db, "readonly", readonly=True)
    assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] >= 1
    conn.close()


def test_read_only_uri_handles_a_hash_in_the_data_folder(tmp_path):
    """f"file:{path}?mode=ro" cut the path at '#' and opened the wrong file."""
    folder = tmp_path / "Kev#n %20 data"
    folder.mkdir()
    db_file = folder / "jobforge.db"
    c = sqlite3.connect(db_file)
    c.execute("CREATE TABLE marker (x)")
    c.commit()
    c.close()
    from jobforge_agent.db.connect import readonly_uri
    conn = sqlite3.connect(readonly_uri(db_file), uri=True)
    assert conn.execute("SELECT name FROM sqlite_master").fetchall() == [("marker",)]
    conn.close()
    assert backup.integrity(db_file) == "ok" and backup.schema_version(db_file) == 0


# ----- change_events pruning -----

def test_pruning_never_trips_the_trigger_inside_the_same_second(db, paths):
    """A millisecond cutoff picked rows the trigger's whole-second cutoff calls young: the DELETE aborted."""
    week = 7 * 86_400_000
    life = open_connection(paths.db, "lifecycle")
    now_s = int(time.time())
    trigger_cutoff = now_s * 1000 - week  # the trigger's: strftime('%s','now') * 1000 - 7 days
    for t in (trigger_cutoff - 5000, trigger_cutoff + 1):  # old, and young by 1 ms in the trigger's eyes
        db.execute("INSERT INTO change_events (type, created_at_ms) VALUES ('discovery_paused', ?)", (t,))
    # The invariant: a millisecond cutoff later in the same second picks the young row and the whole DELETE aborts.
    with pytest.raises(sqlite3.IntegrityError, match="younger than 7 days"):
        life.execute("DELETE FROM change_events WHERE created_at_ms < ?", (trigger_cutoff + 500,))
    assert agent_mod.prune_change_events(life, now_s * 1000 + 500) == 1
    life.close()


# ----- IPC -----

def test_a_non_ascii_authorization_header_is_401_not_500(paths):
    async def check(client, *_):
        r = await client.get("/v1/health", headers={"Authorization": "Bearer é".encode("latin-1")})
        assert r.status_code == 401
    _run(paths, check)


def test_malformed_json_is_400(paths):
    async def check(client, *_):
        r = await client.post("/v1/search-profile", headers={**AUTH, "Content-Type": "application/json"}, content=b"{nope")
        assert r.status_code == 400 and "not valid JSON" in r.json()["error"]
    _run(paths, check)


@pytest.mark.parametrize("body", [[1, 2], {"note": 5}, {"note": "x" * 2001}])
def test_evaluate_anyway_validates_its_body(paths, body):
    async def check(client, *_):
        r = await client.post("/v1/jobs/1/evaluate-anyway", headers=AUTH, json=body)
        assert r.status_code == 422
    _run(paths, check)


def test_a_resume_upload_needs_content_length(paths):
    """A chunked body skipped the size check and was buffered whole."""
    async def check(client, *_):
        async def chunks():
            yield b"%PDF-1.4 "
            yield b"x" * 10
        r = await client.post("/v1/resumes?filename=a.pdf", headers=AUTH, content=chunks())
        assert r.status_code == 411
    _run(paths, check)


def test_an_unhandled_error_is_logged_and_answers_json_500(paths, monkeypatch, caplog, capsys):
    from test_analysis import logged

    async def check(client, runtime, _app):
        monkeypatch.setattr(runtime, "filter_summary", lambda: 1 / 0)
        transport = httpx.ASGITransport(app=_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url=str(client.base_url)) as c:
            r = await c.get("/v1/filter/summary", headers=AUTH)
        assert r.status_code == 500 and r.json()["error"] == "internal error: ZeroDivisionError"
    _run(paths, check)
    assert "ipc_unhandled_error" in logged(caplog, capsys)


def test_an_event_cursor_past_the_newest_row_resumes_from_now(paths):
    """After a restore (older log) or an emptied log, nothing was sent until ids passed the old cursor."""
    async def check(client, runtime, _app):
        async with client.stream("GET", "/v1/events", headers={**AUTH, "Last-Event-ID": "999999"}) as r:
            async def later():
                await asyncio.sleep(0.2)
                runtime.set_discovery_paused(True)
            asyncio.create_task(later())
            got = await asyncio.wait_for(_read_events(r, 1), 5)
        assert json.loads(got[0])["type"] == "discovery_paused"
    _run_live(paths, check)


@pytest.mark.parametrize("cursor", ["²", "9" * 40])
def test_an_odd_event_cursor_is_ignored_not_a_crash(paths, cursor):
    async def check(client, runtime, _app):
        async with client.stream("GET", f"/v1/events?after={cursor}", headers=AUTH) as r:
            assert r.status_code == 200
            async def later():
                await asyncio.sleep(0.2)
                runtime.set_discovery_paused(True)
            asyncio.create_task(later())
            got = await asyncio.wait_for(_read_events(r, 1), 5)
        assert json.loads(got[0])["type"] == "discovery_paused"
    _run_live(paths, check)


# ----- background tasks -----

def test_a_background_task_that_dies_shows_in_health_and_the_log(paths, caplog, capsys):
    from test_analysis import logged

    runtime = agent_mod.AgentRuntime(paths)
    runtime.start()
    try:
        async def go():
            runtime.stop_event = asyncio.Event()

            async def broken():
                raise RuntimeError("heartbeat write failed")
            t = asyncio.create_task(broken(), name="heartbeat")
            t.add_done_callback(runtime._task_finished)
            await asyncio.gather(t, return_exceptions=True)
            await asyncio.sleep(0)
        asyncio.run(go())
        assert runtime.health()["task_errors"] == {"heartbeat": "RuntimeError: heartbeat write failed"}
    finally:
        runtime.finish()
    assert "background_task_died" in logged(caplog, capsys)


# ----- CLI -----

def test_shutdown_reason_is_url_encoded(paths, monkeypatch):
    seen = []
    monkeypatch.setattr(main_mod, "_request", lambda p, method, path: seen.append(path))
    monkeypatch.setattr(main_mod.time, "time", iter([0, 0, 100, 100, 100]).__next__)
    monkeypatch.setattr(main_mod.InstanceLock, "try_acquire", lambda self: True)
    monkeypatch.setattr(main_mod.InstanceLock, "release", lambda self: None)
    main_mod.cmd_shutdown(paths, "user asked & left")
    assert seen == ["/v1/shutdown?reason=user%20asked%20%26%20left"]


# ----- inference -----

def test_model_calls_ignore_proxy_settings(monkeypatch):
    """With HTTP_PROXY set, prompts with profile and resume text went to the proxy, even for 127.0.0.1."""
    monkeypatch.setenv("HTTP_PROXY", "http://10.255.255.1:8888")
    monkeypatch.setenv("HTTPS_PROXY", "http://10.255.255.1:8888")
    client = InferenceClient()
    try:
        assert client._http.trust_env is False
        assert not client._http._mounts  # no proxy transport mounted from the environment
    finally:
        asyncio.run(client.aclose())


def test_an_unreadable_stored_answer_is_logged_and_waits_for_the_model(env, monkeypatch, caplog, capsys):
    """It was swallowed silently, and with the model down the reuse claim retried in a tight loop."""
    from test_analysis import logged

    server, e, _, skill = _done_once(env)
    server.down = True
    env.clock.t += 31_000
    env.verify(skill)
    calls = server.chat_calls

    def stricter(_content):
        raise match.InvalidOutput("a check added after this answer was stored")
    monkeypatch.setattr(match, "parse", stricter)
    assert asyncio.run(e.run_once()) is None  # nothing ran, so the worker loop waits
    assert server.chat_calls == calls
    assert [r["status"] for r in evals(env.db)] == ["done", "pending"]
    assert "stored_answer_unreadable" in logged(caplog, capsys)
