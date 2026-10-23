"""jobforge-agent command line.

Exit codes are a contract with the host's watchdog (ARCHITECTURE 3.2):
  0 ok, 1 error, 3 already running, 4 database newer than this agent, 5 refused while elevated.

Usage:
  jobforge-agent                 run the agent (default)
  jobforge-agent --shutdown [R]  ask a running agent to stop (reason R), wait for its lock, kill as last resort
  jobforge-agent --status        print the running agent's /v1/health
  jobforge-agent --backup        write a backup now (agent must not be running)
  jobforge-agent --restore FILE  replace the database with FILE (agent must not be running)
  jobforge-agent --import-watchlist FILE  add Greenhouse/Lever/Ashby employers from a watchlist YAML (agent running)
  jobforge-agent --import-search-profile FILE  save a new search profile version from YAML (scanner config.yaml accepted; agent running)
  jobforge-agent golden [--json]  golden-set report: your labels against JobForge's answers (read only; agent may run)
  jobforge-agent golden --suite interpretation [--config ID]  run E1 with a real model on the layer-A snippets
  jobforge-agent --version
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from jobforge_agent import __version__, ipc, logs, winutil
from jobforge_agent.db import backup
from jobforge_agent.db.migrate import DatabaseNewer
from jobforge_agent.instance_lock import InstanceLock
from jobforge_agent.paths import Paths, resolve_paths
from jobforge_agent.sessionend import SessionEndWatcher

EXIT_OK, EXIT_ERROR, EXIT_ALREADY_RUNNING, EXIT_DB_NEWER, EXIT_ELEVATED = 0, 1, 3, 4, 5
# Modes that would create or rewrite files in the user's data root. Run elevated, those files
# would be owned by Administrators (SPEC D16), so they are refused.
_WRITING_MODES = {"run", "backup", "restore"}


def _note_refusal(paths: Paths, mode: str) -> None:
    """Record an elevated refusal without creating new files: stderr, plus the log only if it already exists."""
    line = json.dumps({"event": "refused_elevated", "mode": mode, "version": __version__,
                       "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    print(line, file=sys.stderr)
    log_file = paths.logs / "agent.jsonl"
    if log_file.exists():
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _request(paths: Paths, method: str, path: str, timeout: float = 3, body: dict | None = None) -> dict:
    info = ipc.read_ipc_file(paths.ipc)
    if not info:
        raise ConnectionError("no ipc.json (agent not running?)")
    data = json.dumps(body).encode("utf-8") if body is not None else (b"" if method == "POST" else None)
    headers = {"Authorization": f"Bearer {info['token']}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}{path}", method=method, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def cmd_run(paths: Paths) -> int:
    from jobforge_agent.agent import AgentRuntime  # heavy imports only when running

    paths.ensure()
    sid = winutil.restrict_dir_to_user(paths.run)
    entries = winutil.dir_acl_entries(paths.run)
    if len(entries) != 1:
        # The token file must never sit in a folder anyone else can read (ARCHITECTURE 5).
        print(f"refusing to start: {paths.run} ACL is not user-only after restricting to {sid}: {entries}", file=sys.stderr)
        return EXIT_ERROR
    lock = InstanceLock(paths.lock)
    if not lock.try_acquire():
        print("another JobForge agent is already running for this user", file=sys.stderr)
        return EXIT_ALREADY_RUNNING
    logs.configure(paths.logs)
    log = logs.get("main")
    runtime = AgentRuntime(paths)
    recorded = threading.Event()  # set once the run row holds its final stop reason
    watcher = SessionEndWatcher(runtime.request_stop_threadsafe, recorded)
    if not watcher.start():
        # Not fatal: without it a sign-out ends the agent abruptly and startup recovery repairs the state.
        log.error("session_end_watcher_failed", error=watcher.error)
    try:
        try:
            runtime.start()
        except DatabaseNewer as exc:
            log.error("db_newer_than_agent", db_version=exc.db_version, agent_schema=exc.agent_version)
            return EXIT_DB_NEWER
        try:
            asyncio.run(runtime.serve())
        except KeyboardInterrupt:
            runtime.stop_reason = "signal"
        return EXIT_OK
    except Exception as exc:
        log.error("agent_failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        runtime.finish()
        recorded.set()
        watcher.close()
        logs.close()
        lock.release()


def cmd_shutdown(paths: Paths, reason: str) -> int:
    info = ipc.read_ipc_file(paths.ipc)
    try:
        _request(paths, "POST", f"/v1/shutdown?reason={reason}")
    except (OSError, urllib.error.URLError, ConnectionError) as exc:
        print(f"shutdown request not delivered: {exc}", file=sys.stderr)
    lock = InstanceLock(paths.lock)
    deadline = time.time() + 30
    while time.time() < deadline:
        if lock.try_acquire():
            lock.release()
            return EXIT_OK
        time.sleep(0.25)
    if info and info.get("pid"):
        subprocess.run(["taskkill", "/PID", str(info["pid"]), "/F"], capture_output=True, creationflags=winutil.CREATE_NO_WINDOW)
        print(f"agent pid {info['pid']} did not stop within 30 s; killed", file=sys.stderr)
    return EXIT_OK


def cmd_status(paths: Paths) -> int:
    try:
        print(json.dumps(_request(paths, "GET", "/v1/health"), indent=2))
        return EXIT_OK
    except (OSError, urllib.error.URLError, ConnectionError) as exc:
        print(f"agent not reachable: {exc}", file=sys.stderr)
        return EXIT_ERROR


def cmd_import_watchlist(paths: Paths, source: str) -> int:
    """Read a watchlist YAML (scanner format: employers: [{name, ats, slug}]) and send it to the
    running agent. The agent is the only process that writes the database (SPEC D4)."""
    import yaml

    with open(source, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rows = data.get("employers") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        print("watchlist must be a list or have an 'employers:' list", file=sys.stderr)
        return EXIT_ERROR
    entries = [{"source": r.get("ats") or r.get("source"), "slug": r.get("slug"), "name": r.get("name")}
               for r in rows if isinstance(r, dict)]
    try:
        print(json.dumps(_request(paths, "POST", "/v1/admin/employers/import", timeout=30, body={"employers": entries}), indent=2))
        return EXIT_OK
    except (OSError, urllib.error.URLError, ConnectionError) as exc:
        print(f"agent not reachable (is JobForge running?): {exc}", file=sys.stderr)
        return EXIT_ERROR


def cmd_import_search_profile(paths: Paths, source: str) -> int:
    """Send a profile YAML to the running agent as a new version. A job-scanner config.yaml is
    converted (jax_metro -> home_metro, FL/GA made explicit); otherwise the YAML is the rules."""
    import yaml

    from jobforge_agent.filtering.profile import from_scanner_config

    with open(source, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    rules = from_scanner_config(raw) if "jax_metro" in (raw.get("location") or {}) else raw
    try:
        print(json.dumps(_request(paths, "POST", "/v1/search-profile", timeout=30, body={"rules": rules}), indent=2))
        return EXIT_OK
    except urllib.error.HTTPError as exc:
        print(f"rejected: {exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return EXIT_ERROR
    except (OSError, urllib.error.URLError, ConnectionError) as exc:
        print(f"agent not reachable (is JobForge running?): {exc}", file=sys.stderr)
        return EXIT_ERROR


def _with_agent_stopped(paths: Paths, action) -> int:
    lock = InstanceLock(paths.lock)
    if not lock.try_acquire():
        print("the agent is running; stop it first (jobforge-agent --shutdown)", file=sys.stderr)
        return EXIT_ALREADY_RUNNING
    try:
        return action()
    finally:
        lock.release()


def cmd_backup(paths: Paths) -> int:
    def go() -> int:
        dest = backup.backup_to(paths.db, paths.backups / f"manual-{time.strftime('%Y%m%d-%H%M%S')}.db")
        print(dest)
        return EXIT_OK
    return _with_agent_stopped(paths, go)


def cmd_restore(paths: Paths, source: str) -> int:
    from pathlib import Path

    from jobforge_agent.db.migrate import target_version

    def go() -> int:
        try:
            kept = backup.restore(paths.db, Path(source), paths.backups, target_version())
        except backup.SourceNewer as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_DB_NEWER
        except backup.BackupError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_ERROR
        if kept is not None:
            print(f"kept the previous database as {kept}")
        print(f"restored {source}")
        return EXIT_OK
    return _with_agent_stopped(paths, go)


def cmd_golden(paths: Paths, args: list[str]) -> int:
    """Read-only reports (ARCHITECTURE 6.7, 6.6 C). Opens the database read-only, so it runs beside
    the agent and never changes anything; the interpretation suite also calls the model."""
    from jobforge_agent.db.connect import open_connection
    from jobforge_agent.evaluation import golden
    from jobforge_agent.inference.client import InferenceClient, load_config

    if not paths.db.exists():
        print(f"no database at {paths.db}", file=sys.stderr)
        return EXIT_ERROR
    conn = open_connection(paths.db, "readonly", readonly=True)
    try:
        if conn.execute("PRAGMA user_version").fetchone()[0] < 8:
            print("the database predates golden labels (schema 8); start the agent once to migrate it", file=sys.stderr)
            return EXIT_ERROR
        if "--suite" not in args:
            r = golden.report(conn)
            print(json.dumps(r, indent=2) if "--json" in args else golden.format_report(r))
            return EXIT_OK
        if args[args.index("--suite") + 1:args.index("--suite") + 2] != ["interpretation"]:
            print("unknown suite (only: interpretation)", file=sys.stderr)
            return EXIT_ERROR
        if "--config" in args:
            config_id = int(args[args.index("--config") + 1])
        else:
            row = conn.execute("SELECT value FROM settings WHERE key = 'default_model_config_id'").fetchone()
            config_id = int(row[0]) if row else 0
        cfg = load_config(conn, config_id)
    finally:
        conn.close()
    if cfg is None:
        print(f"no model config {config_id}", file=sys.stderr)
        return EXIT_ERROR

    async def run():
        client = InferenceClient()
        try:
            return await golden.run_interpretation(client, cfg, golden.interpretation_cases())
        finally:
            await client.aclose()

    per_area, meta = asyncio.run(run())
    print(golden.format_interpretation(per_area, meta))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = "run"
    if args:
        mode = {"--shutdown": "shutdown", "--status": "status", "--backup": "backup",
                "--restore": "restore", "--version": "version", "--import-watchlist": "import",
                "--import-search-profile": "import_profile", "golden": "golden"}.get(args[0], "unknown")
    if mode == "unknown":
        print(__doc__, file=sys.stderr)
        return EXIT_ERROR
    if mode == "version":
        print(__version__)
        return EXIT_OK
    paths = resolve_paths()
    if mode in _WRITING_MODES and winutil.is_elevated():
        _note_refusal(paths, mode)
        return EXIT_ELEVATED
    if mode == "run":
        return cmd_run(paths)
    if mode == "shutdown":
        return cmd_shutdown(paths, args[1] if len(args) > 1 else "requested")
    if mode == "status":
        return cmd_status(paths)
    if mode == "golden":
        return cmd_golden(paths, args[1:])
    if mode == "backup":
        return cmd_backup(paths)
    if mode == "import":
        if len(args) < 2:
            print("--import-watchlist needs a file", file=sys.stderr)
            return EXIT_ERROR
        return cmd_import_watchlist(paths, args[1])
    if mode == "import_profile":
        if len(args) < 2:
            print("--import-search-profile needs a file", file=sys.stderr)
            return EXIT_ERROR
        return cmd_import_search_profile(paths, args[1])
    if len(args) < 2:
        print("--restore needs a file", file=sys.stderr)
        return EXIT_ERROR
    return cmd_restore(paths, args[1])


if __name__ == "__main__":
    sys.exit(main())
