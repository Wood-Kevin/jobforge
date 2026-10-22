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
        self.reader: AgentConnection | None = None
        self.elevated = winutil.is_elevated()
        self.last_maintenance_error: str | None = None

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
        for c in (self.lifecycle, self.reader):
            if c is not None:
                c.close()
        self.lifecycle = self.reader = None

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

    def request_stop(self, reason: str) -> None:
        self.stop_reason = reason
        if self.stop_event is not None:
            self.stop_event.set()

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
        sock = ipc.bind_loopback()
        endpoint = ipc.IpcEndpoint(port=sock.getsockname()[1], token=ipc.new_token())
        app = ipc.create_app(self, endpoint)
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off"))
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
