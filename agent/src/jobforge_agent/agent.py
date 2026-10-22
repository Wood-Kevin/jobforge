"""Agent runtime: startup sequence, background tasks, IPC server, graceful stop.

Startup order matters and is the contract the tests check:
lock -> logging -> migrate (backup first, refuse newer) -> recovery -> run row -> IPC.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

import uvicorn

from jobforge_agent import PROTOCOL_VERSION, __version__, ipc, logs, winutil
from jobforge_agent.db import backup, migrate, recovery
from jobforge_agent.db.connect import AgentConnection, open_connection
from jobforge_agent.paths import Paths

HEARTBEAT_S = 10
MAINTENANCE_S = 3600


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class StartupResult:
    migrated_from: int
    migrated_to: int
    pre_migrate_backup: str | None
    recovery: recovery.RecoveryReport


class AgentRuntime:
    """Owns the database connections and the run row. Lives on one event-loop thread."""

    def __init__(self, paths: Paths):
        self.paths = paths
        self.log = logs.get("agent")
        self.started_ms = now_ms()
        self.stop_event: asyncio.Event | None = None
        self.stop_reason = "unknown"
        self.run_id: int | None = None
        self.lifecycle: AgentConnection | None = None
        self.user: AgentConnection | None = None
        self.reader: AgentConnection | None = None
        self.elevated = winutil.is_elevated()
        self.last_maintenance_error: str | None = None
        # Woken after each commit that adds change_events, so the event stream pushes at once
        # instead of waiting for its fallback poll. Created on the loop in serve().
        self._events_signal: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ----- startup / shutdown (synchronous, before and after the loop work) -----

    def start(self) -> StartupResult:
        mig = open_connection(self.paths.db, "migration")
        try:
            result = migrate.migrate(mig, self.paths.db, self.paths.backups)
        finally:
            mig.close()
        if result.backup:
            self.log.info("pre_migrate_backup", path=str(result.backup), integrity="ok")
        if result.to_version != result.from_version:
            self.log.info("migrated", from_version=result.from_version, to_version=result.to_version)
        self.lifecycle = open_connection(self.paths.db, "lifecycle")
        self.user = open_connection(self.paths.db, "user")
        self.reader = open_connection(self.paths.db, "readonly", readonly=True)
        report = recovery.recover(self.lifecycle, now_ms())
        self.run_id = self.lifecycle.execute(
            "INSERT INTO agent_runs (pid, version, elevated, started_at_ms, heartbeat_at_ms) VALUES (?, ?, ?, ?, ?) RETURNING id",
            (os.getpid(), __version__, int(self.elevated), self.started_ms, self.started_ms)).fetchone()[0]
        self.log.info("agent_start", run_id=self.run_id, version=__version__, schema=result.to_version,
                      elevated=self.elevated, recovered_runs=report.crashed_runs,
                      interrupted_fetches=len(report.interrupted_fetches),
                      reset_analyses=report.reset_analyses, reset_evaluations=report.reset_evaluations)
        return StartupResult(result.from_version, result.to_version, str(result.backup) if result.backup else None, report)

    def finish(self) -> None:
        if self.lifecycle is not None and self.run_id is not None:
            t = now_ms()
            self.lifecycle.execute("UPDATE agent_runs SET stopped_at_ms = ?, heartbeat_at_ms = ?, stop_reason = ? WHERE id = ?",
                                   (t, t, self.stop_reason, self.run_id))
            self.log.info("agent_stop", run_id=self.run_id, reason=self.stop_reason)
        # Read-only first, a writer last: only a read-write connection can checkpoint and remove
        # the WAL on close. Closing the read-only one last left committed rows in jobforge.db-wal
        # (found in the P1.2 lifecycle run), which a plain file copy would miss.
        for c in (self.reader, self.user, self.lifecycle):
            if c is not None:
                c.close()
        self.lifecycle = self.user = self.reader = None

    # ----- used by IPC handlers (event-loop thread) -----

    def health(self) -> dict:
        assert self.reader is not None
        paused = self.reader.execute("SELECT value FROM settings WHERE key = 'discovery_paused'").fetchone()
        return {
            "version": __version__,
            "protocol_version": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "run_id": self.run_id,
            "schema": self.reader.execute("PRAGMA user_version").fetchone()[0],
            "db": "ok",
            "elevated": self.elevated,
            "discovery_paused": paused is not None and paused[0] == "1",
            "uptime_s": round((now_ms() - self.started_ms) / 1000),
            "last_maintenance_error": self.last_maintenance_error,
            "data_dir": str(self.paths.root),
        }

    def set_discovery_paused(self, paused: bool) -> dict:
        """User action (tray or window). Persists across restarts; recorded as a change event."""
        assert self.user is not None
        self.user.execute("BEGIN IMMEDIATE")
        try:
            self.user.execute("UPDATE settings SET value = ? WHERE key = 'discovery_paused'", ("1" if paused else "0",))
            self.user.execute("INSERT INTO change_events (type, created_at_ms) VALUES (?, ?)",
                              ("discovery_paused" if paused else "discovery_resumed", now_ms()))
            self.user.execute("COMMIT")
        except Exception:
            self.user.execute("ROLLBACK")
            raise
        self.log.info("discovery_paused" if paused else "discovery_resumed")
        self.notify_events()
        return self.health()

    def notify_events(self) -> None:
        """Call after a commit that added change_events rows; never before (no uncommitted events)."""
        if self._events_signal is not None:
            self._events_signal.set()

    def events_after(self, last_id: int, limit: int = 200) -> list[dict]:
        assert self.reader is not None
        rows = self.reader.execute(
            "SELECT id, type, job_id, employer_id, ref, created_at_ms FROM change_events WHERE id > ? ORDER BY id LIMIT ?",
            (last_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def latest_event_id(self) -> int:
        assert self.reader is not None
        return self.reader.execute("SELECT coalesce(max(id), 0) FROM change_events").fetchone()[0]

    async def wait_for_events(self, timeout: float) -> None:
        """Return when notified or after `timeout` (fallback poll), whichever comes first."""
        if self._events_signal is None:
            self._events_signal = asyncio.Event()
        signal = self._events_signal
        try:
            await asyncio.wait_for(signal.wait(), timeout=timeout)
        except TimeoutError:
            return
        # Swap in a fresh event so the next waiter blocks until the next notification.
        if self._events_signal is signal:
            self._events_signal = asyncio.Event()

    def request_stop(self, reason: str) -> None:
        self.stop_reason = reason
        if self.stop_event is not None:
            self.stop_event.set()
        self.notify_events()  # wake event streams so they end and do not hold up shutdown

    def request_stop_threadsafe(self, reason: str) -> None:
        """For callers on other threads (the session-end window)."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self.request_stop, reason)

    def is_stopping(self) -> bool:
        return self.stop_event is not None and self.stop_event.is_set()

    # ----- background tasks -----

    async def heartbeat(self) -> None:
        assert self.stop_event is not None and self.lifecycle is not None
        while not self.stop_event.is_set():
            self.lifecycle.execute("UPDATE agent_runs SET heartbeat_at_ms = ? WHERE id = ?", (now_ms(), self.run_id))
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=HEARTBEAT_S)
            except TimeoutError:
                pass

    async def maintenance(self) -> None:
        """Daily backup and notification-log pruning, checked hourly."""
        assert self.stop_event is not None and self.lifecycle is not None
        while not self.stop_event.is_set():
            try:
                made = backup.daily_backup(self.paths.db, self.paths.backups)
                if made:
                    self.log.info("daily_backup", path=str(made), integrity="ok")
                pruned = self.lifecycle.execute(
                    "DELETE FROM change_events WHERE created_at_ms < ?", (now_ms() - 7 * 86_400_000,)).rowcount
                if pruned:
                    self.log.info("change_events_pruned", rows=pruned)
            except Exception as exc:  # the agent keeps running; the failure shows in the log and in /v1/health
                self.last_maintenance_error = f"{type(exc).__name__}: {exc}"
                self.log.error("maintenance_failed", error=self.last_maintenance_error)
            else:
                self.last_maintenance_error = None
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=MAINTENANCE_S)
            except TimeoutError:
                pass

    async def serve(self) -> None:
        """Run the IPC server and background tasks until a stop is requested."""
        self.stop_event = asyncio.Event()
        self._events_signal = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        sock = ipc.bind_loopback()
        endpoint = ipc.IpcEndpoint(port=sock.getsockname()[1], token=ipc.new_token())
        app = ipc.create_app(self, endpoint)
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off",
                                               timeout_graceful_shutdown=3))
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        # Publish the endpoint only once uvicorn is accepting connections.
        while not server.started and not server_task.done():
            await asyncio.sleep(0.01)
        if server_task.done():
            await server_task  # surfaces the startup error
            raise RuntimeError("IPC server stopped during startup")
        ipc.write_ipc_file(self.paths.ipc, endpoint, os.getpid())
        self.log.info("ipc_ready", port=endpoint.port)

        tasks = [asyncio.create_task(self.heartbeat()), asyncio.create_task(self.maintenance())]
        stop_task = asyncio.create_task(self.stop_event.wait())
        done, _ = await asyncio.wait({server_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if server_task in done and not self.stop_event.is_set():
            # uvicorn exited on its own (e.g. Ctrl+C / console close): stop the rest too.
            self.stop_reason = self.stop_reason if self.stop_reason != "unknown" else "signal"
            self.stop_event.set()
        server.should_exit = True
        await asyncio.gather(server_task, *tasks, return_exceptions=True)
        stop_task.cancel()
        self.paths.ipc.unlink(missing_ok=True)
