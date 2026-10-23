"""P1.1 acceptance against real agent processes (ARCHITECTURE 9, P1.1 row)."""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from jobforge_agent import ipc, winutil
from jobforge_agent.db import migrate
from jobforge_agent.instance_lock import InstanceLock
from jobforge_agent.paths import Paths

from launch import run_agent, start_agent, user_temp_dir

pytestmark = pytest.mark.integration


def _wait(cond, timeout: float, what: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = cond()
        if value:
            return value
        time.sleep(0.2)
    raise TimeoutError(what)


def _health(paths: Paths, host: str | None = None, token: str | None = "auto") -> tuple[int, dict | None]:
    info = ipc.read_ipc_file(paths.ipc)
    headers = {}
    if token == "auto":
        headers["Authorization"] = f"Bearer {info['token']}"
    if host:
        headers["Host"] = host
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/v1/health", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def _running_health(paths):
    try:
        return _health(paths)[1] if ipc.read_ipc_file(paths.ipc) else None
    except OSError:
        return None


def _runs(paths: Paths) -> list[tuple]:
    conn = sqlite3.connect(f"file:{paths.db}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT id, pid, elevated, stopped_at_ms IS NOT NULL, stop_reason FROM agent_runs ORDER BY id").fetchall()
    finally:
        conn.close()


@pytest.fixture
def agent_paths():
    # Not tmp_path: pytest's temp tree is owner-only, and from an elevated test process the owner is
    # Administrators, so the unelevated agent under test could not create its data there (P1.8).
    p = Paths(user_temp_dir("jf_data_") / "jf")
    yield p
    # Never leave an agent running after a test.
    info = ipc.read_ipc_file(p.ipc)
    if info:
        try:
            os.kill(info["pid"], signal.SIGTERM)
        except OSError:
            pass


def test_agent_lifecycle_end_to_end(agent_paths):
    p = agent_paths
    start_agent(p.root)
    health = _wait(lambda: _running_health(p), 30, "agent did not publish a working ipc.json")
    pid = health["pid"]
    assert health["elevated"] is False and health["schema"] == migrate.target_version()

    # IPC guard over a real socket.
    assert _health(p, token=None)[0] == 401
    assert _health(p, host="evil.example")[0] == 400

    # Listens on loopback only.
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"Get-NetTCPConnection -OwningProcess {pid} -State Listen | ForEach-Object {{ $_.LocalAddress }}"],
                         capture_output=True, text=True, creationflags=winutil.CREATE_NO_WINDOW).stdout.split()
    assert out and set(out) == {"127.0.0.1"}, out

    # run\ (holding the token) is user-only.
    entries = winutil.dir_acl_entries(p.run)
    assert len(entries) == 1 and entries[0].endswith("(OI)(CI)(F)"), entries

    # One agent per user.
    assert run_agent(p.root) == 3

    # Crash: kill it; Windows releases the lock at once; the next agent recovers the run.
    os.kill(pid, signal.SIGTERM)
    lock = InstanceLock(p.lock)
    t0 = time.time()
    _wait(lock.try_acquire, 5, "lock not released after kill")
    assert time.time() - t0 < 1.0
    lock.release()
    p.ipc.unlink(missing_ok=True)  # stale file from the killed agent
    start_agent(p.root)
    health2 = _wait(lambda: _running_health(p), 30, "agent did not restart")
    assert health2["pid"] != pid
    runs = _runs(p)
    assert runs[0][1] == pid and runs[0][3] and runs[0][4] == "crash_recovered"

    # Graceful stop through the CLI the installer hooks use.
    assert run_agent(p.root, "--shutdown", "test_stop") == 0
    _wait(lambda: not p.ipc.exists(), 10, "ipc.json not removed on clean stop")
    runs = _runs(p)
    assert runs[-1][1] == health2["pid"] and runs[-1][3] and runs[-1][4] == "test_stop"
    assert all(r[2] == 0 for r in runs)  # every run recorded elevated = 0

    log = (p.logs / "agent.jsonl").read_text(encoding="utf-8")
    assert '"event": "agent_start"' in log and '"event": "agent_stop"' in log


def test_database_newer_than_agent_exits_4(agent_paths):
    p = agent_paths
    p.ensure()
    conn = sqlite3.connect(p.db)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    assert run_agent(p.root) == 4
    log = (p.logs / "agent.jsonl").read_text(encoding="utf-8")
    assert "db_newer_than_agent" in log
    assert not p.ipc.exists()


def test_backup_and_restore_refused_while_agent_runs_then_work(agent_paths):
    p = agent_paths
    start_agent(p.root)
    _wait(lambda: _running_health(p), 30, "agent did not start")
    assert run_agent(p.root, "--backup") == 3
    assert run_agent(p.root, "--shutdown") == 0
    assert run_agent(p.root, "--backup") == 0
    manual = sorted(p.backups.glob("manual-*.db"))
    assert len(manual) == 1
    assert run_agent(p.root, "--restore", str(manual[0])) == 0


@pytest.mark.skipif(not winutil.is_elevated(), reason="needs an elevated test shell to observe the refusal")
def test_elevated_run_is_refused_without_creating_files(tmp_path):
    root = tmp_path / "jf"
    env = {**os.environ, "JOBFORGE_DATA_DIR": str(root), "JOBFORGE_BUILT_IN_LIST": "0"}
    for args in ([], ["--restore", "x.db"], ["--backup"]):
        r = subprocess.run([sys.executable, "-m", "jobforge_agent", *args], env=env, capture_output=True, text=True,
                           creationflags=winutil.CREATE_NO_WINDOW, timeout=60)
        assert r.returncode == 5, (args, r.stderr)
        assert "refused_elevated" in r.stderr
    assert not root.exists()  # nothing created by an elevated process
    # --status and --shutdown stay usable elevated (installer hooks need them).
    r = subprocess.run([sys.executable, "-m", "jobforge_agent", "--status"], env=env, capture_output=True, text=True,
                       creationflags=winutil.CREATE_NO_WINDOW, timeout=60)
    assert r.returncode == 1 and "not reachable" in r.stderr


def test_sign_out_stops_the_agent_gracefully(agent_paths):
    """Simulate Windows sign-out by sending WM_ENDSESSION to the agent's hidden window."""
    import ctypes
    from ctypes import wintypes

    from jobforge_agent.sessionend import CLASS_NAME, ENDSESSION_LOGOFF, WM_ENDSESSION

    p = agent_paths
    start_agent(p.root)
    health = _wait(lambda: _running_health(p), 30, "agent did not start")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.FindWindowExW.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    def own_window():
        # Only the window of THIS test's agent. FindWindowW(CLASS_NAME) returned the first agent
        # window on the machine: in P1.7 this test signed out a separate drill agent (and would do
        # the same to the installed agent if it were running).
        hwnd = None
        while True:
            hwnd = user32.FindWindowExW(None, hwnd, CLASS_NAME, None)
            if not hwnd:
                return None
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == health["pid"]:
                return hwnd

    hwnd = _wait(own_window, 10, "session window of this agent not found")
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    result = ctypes.c_size_t()
    t0 = time.time()
    user32.SendMessageTimeoutW(hwnd, WM_ENDSESSION, 1, ENDSESSION_LOGOFF, 0, 10_000, ctypes.byref(result))
    returned_after = time.time() - t0
    _wait(lambda: not p.ipc.exists(), 10, "agent did not stop after WM_ENDSESSION")
    runs = _runs(p)
    assert runs[-1][1] == health["pid"] and runs[-1][3] and runs[-1][4] == "session_end_logoff"
    # The handler returned only after the run was recorded, and inside Windows' allowance.
    assert returned_after < 5.0, returned_after
