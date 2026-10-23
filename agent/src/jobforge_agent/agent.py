"""Agent runtime: startup sequence, background tasks, IPC server, graceful stop.

Startup order matters and is the contract the tests check:
lock -> logging -> migrate (backup first, refuse newer) -> recovery -> run row -> IPC.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from dataclasses import dataclass

import uvicorn

from jobforge_agent import PROTOCOL_VERSION, __version__, applications, ipc, logs, views, winutil
from jobforge_agent.analysis import store as analysis_store
from jobforge_agent.analysis.worker import AnalysisWorker
from jobforge_agent.db import backup, migrate, recovery
from jobforge_agent.db.connect import AgentConnection, open_connection
from jobforge_agent.discovery import admin as discovery_admin
from jobforge_agent.evaluation import store as evaluation_store
from jobforge_agent.evaluation.worker import EvaluationWorker
from jobforge_agent.discovery.http import HttpClient
from jobforge_agent.discovery.service import DiscoveryService
from jobforge_agent.filtering import pipeline as filter_pipeline
from jobforge_agent.filtering import rules as filter_rules
from jobforge_agent.filtering.profile import compile_profile
from jobforge_agent.inference import configs as model_configs
from jobforge_agent.inference.client import InferenceClient, load_config
from jobforge_agent.paths import Paths
from jobforge_agent.profile import resumes as profile_resumes

HEARTBEAT_S = 10
REFILTER_CHUNK = 300
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
        self.discovery_conn: AgentConnection | None = None
        self.discovery: DiscoveryService | None = None
        self.reader: AgentConnection | None = None
        self.evaluation_conn: AgentConnection | None = None
        self.inference: InferenceClient | None = None
        self.inference_transport = None  # tests inject a fake inference server here
        self.analysis: AnalysisWorker | None = None
        self.evaluation: EvaluationWorker | None = None
        self.elevated = winutil.is_elevated()
        self.last_maintenance_error: str | None = None
        # Woken after each commit that adds change_events, so the event stream pushes at once
        # instead of waiting for its fallback poll. Created on the loop in serve().
        self._events_signal: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._refilter_task: asyncio.Task | None = None
        self._refilter_again = False

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
        self.discovery_conn = open_connection(self.paths.db, "discovery")
        self.evaluation_conn = open_connection(self.paths.db, "evaluation")
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
        for c in (self.reader, self.user, self.discovery_conn, self.evaluation_conn, self.lifecycle):
            if c is not None:
                c.close()
        self.lifecycle = self.user = self.reader = self.discovery_conn = self.evaluation_conn = None

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

    def discovery_status(self) -> dict:
        assert self.reader is not None
        return discovery_admin.status(self.reader, now_ms())

    def import_employers(self, entries: list[dict]) -> dict:
        assert self.user is not None
        result = discovery_admin.import_employers(self.user, entries, now_ms(), random.Random())
        self.log.info("employers_imported", added=result["added"], existing=result["existing"],
                      skipped_unsupported=len(result["skipped_unsupported"]))
        return result

    def poll_now(self, employer_id: int) -> bool:
        """User action: make one employer due now (still subject to lease, backoff and budget)."""
        assert self.user is not None
        return self.user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ? WHERE employer_id = ?",
                                 (now_ms(), employer_id)).rowcount == 1

    # ----- search profile and filtering -----

    def search_profile(self) -> dict | None:
        assert self.reader is not None
        row = self.reader.execute("SELECT id, version, rules, created_at_ms FROM search_profiles ORDER BY version DESC LIMIT 1").fetchone()
        if row is None:
            return None
        return {"id": row[0], "version": row[1], "rules": json.loads(row[2]), "created_at_ms": row[3]}

    def save_search_profile(self, rules: dict) -> dict:
        """User action: a new, immutable profile version (validated first), then a re-filter."""
        assert self.user is not None
        compile_profile(rules)  # raises ProfileError naming the bad field; nothing is saved
        self.user.execute("BEGIN IMMEDIATE")
        try:
            version = self.user.execute("SELECT coalesce(max(version), 0) + 1 FROM search_profiles").fetchone()[0]
            self.user.execute("INSERT INTO search_profiles (version, rules, created_at_ms) VALUES (?, ?, ?)",
                              (version, json.dumps(rules), now_ms()))
            self.user.execute("INSERT INTO change_events (type, ref, created_at_ms) VALUES ('search_profile_saved', ?, ?)",
                              (json.dumps({"version": version}), now_ms()))
            self.user.execute("COMMIT")
        except Exception:
            self.user.execute("ROLLBACK")
            raise
        self.log.info("search_profile_saved", version=version)
        self.notify_events()
        self.start_refilter()
        return {"version": version, "refilter": "started"}

    def start_refilter(self) -> str:
        """Re-filter every job's current posting with the active profile. One run at a time; requests
        before a run reads its job list share that run, a request after gets exactly one more."""
        if self._refilter_task is not None and not self._refilter_task.done():
            self._refilter_again = True
            return "queued"
        self._refilter_task = asyncio.get_running_loop().create_task(self._refilter())
        self._refilter_task.add_done_callback(self._refilter_finished)
        return "started"

    def _refilter_finished(self, task: asyncio.Task) -> None:
        # A failed re-filter must be seen: error log plus an event the dashboard shows.
        if task.cancelled() or task.exception() is None:
            return
        exc = task.exception()
        self.log.error("refilter_failed", error=f"{type(exc).__name__}: {exc}")
        if self.discovery_conn is not None:
            self.discovery_conn.execute("INSERT INTO change_events (type, ref, created_at_ms) VALUES ('refilter_failed', ?, ?)",
                                        (json.dumps({"error": f"{type(exc).__name__}: {exc}"[:500]}), now_ms()))
            self.notify_events()

    async def _refilter(self) -> None:
        while True:
            self._refilter_again = False
            conn = self.discovery_conn
            assert conn is not None
            totals = filter_pipeline.FilterStats()
            job_ids = filter_pipeline.all_job_ids(conn)
            started = now_ms()
            profile = filter_pipeline.active_profile(conn)
            if profile is None:
                self.log.warning("refilter_skipped", reason="no search profile")
                return
            model_id = filter_pipeline.default_model_config_id(conn)
            for i in range(0, len(job_ids), REFILTER_CHUNK):
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for job_id in job_ids[i:i + REFILTER_CHUNK]:
                        filter_pipeline.filter_job(conn, job_id, profile, now_ms(), totals, model_id, emit_event=False)
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                await asyncio.sleep(0)  # let polls and IPC run between chunks
            summary = {"profile_version": profile.version, "ruleset": filter_rules.RULESET_VERSION, "jobs": len(job_ids),
                       "new_results": totals.filtered, "already_filtered": totals.already_filtered, "passed": totals.passed,
                       "rejected": totals.rejected, "queued": totals.queued, "seconds": round((now_ms() - started) / 1000, 1)}
            conn.execute("INSERT INTO change_events (type, ref, created_at_ms) VALUES ('refilter_done', ?, ?)",
                         (json.dumps(summary), now_ms()))
            self.log.info("refilter_done", **summary)
            self.notify_events()
            if not self._refilter_again:
                return

    def evaluate_anyway(self, job_id: int, note: str | None) -> dict | None:
        """User action: override a rejection and queue analysis of the job's current posting."""
        assert self.user is not None
        got = filter_pipeline.job_view(self.user, job_id)
        if got is None:
            return None
        _, raw_id, closed = got
        model_id = filter_pipeline.default_model_config_id(self.user)
        self.user.execute("BEGIN IMMEDIATE")
        try:
            self.user.execute("INSERT INTO filter_overrides (job_id, note, created_at_ms) VALUES (?, ?, ?)", (job_id, note, now_ms()))
            queued = (not closed and model_id is not None
                      and filter_pipeline.queue_analysis(self.user, job_id, raw_id, model_id, now_ms()))
            self.user.execute("INSERT INTO change_events (type, job_id, created_at_ms) VALUES ('evaluate_anyway', ?, ?)", (job_id, now_ms()))
            self.user.execute("COMMIT")
        except Exception:
            self.user.execute("ROLLBACK")
            raise
        self.notify_events()
        return {"job_id": job_id, "queued": bool(queued), "closed": closed}

    def filter_summary(self) -> dict:
        """Verdicts and rejection categories for the active profile and ruleset (evidence and dashboard)."""
        assert self.reader is not None
        profile = self.search_profile()
        if profile is None:
            return {"profile_version": None}
        rows = self.reader.execute(
            "SELECT fr.verdict, fr.reasons FROM filter_results fr JOIN jobs j ON j.current_raw_posting_id = fr.raw_posting_id "
            "WHERE fr.search_profile_id = ? AND fr.ruleset_version = ? AND j.closed_at_ms IS NULL",
            (profile["id"], filter_rules.RULESET_VERSION)).fetchall()
        verdicts, categories, clearance = {"pass": 0, "reject": 0}, {}, 0
        for verdict, reasons in rows:
            verdicts[verdict] += 1
            r = json.loads(reasons)
            if r.get("clearance_mention"):
                clearance += 1
            if r.get("primary"):
                cat = filter_rules.category(r["primary"])
                categories[cat] = categories.get(cat, 0) + 1
        pending = self.reader.execute("SELECT count(*) FROM posting_analyses WHERE status = 'pending'").fetchone()[0]
        return {"profile_version": profile["version"], "ruleset": filter_rules.RULESET_VERSION, "verdicts": verdicts,
                "rejected_by_primary_category": categories, "clearance_mentions": clearance, "analyses_pending": pending}

    # ----- profile (user actions only) -----

    def profile_read(self, fn, *args):
        """Profile reads use the user-action connection; reads take no write lock."""
        assert self.user is not None
        return fn(self.user, *args)

    def profile_action(self, what: str, fn, *args, ref: dict | None = None):
        """Run one profile change in a user-action transaction with its change event.
        Logs and events carry ids and the action name only, never profile content."""
        assert self.user is not None
        self.user.execute("BEGIN IMMEDIATE")
        try:
            result = fn(self.user, *args)
            event_ref = {"what": what, **(ref or {})}
            if isinstance(result, int) and not isinstance(result, bool):
                event_ref["id"] = result
            elif isinstance(result, dict) and isinstance(result.get("id"), int):
                event_ref["id"] = result["id"]
            self.user.execute("INSERT INTO change_events (type, ref, created_at_ms) VALUES ('profile_changed', ?, ?)",
                              (json.dumps(event_ref), now_ms()))
            self.user.execute("COMMIT")
        except Exception:
            self.user.execute("ROLLBACK")
            raise
        self.log.info("profile_changed", **event_ref)
        self.notify_events()
        return result

    def upload_resume(self, filename: str, label: str | None, data: bytes) -> dict:
        result = self.profile_action("resume_uploaded", profile_resumes.store, filename, label, data, now_ms())
        if result["extraction_error"]:
            # Stored, but no text: visible in the response and the log, never silently empty.
            self.log.warning("resume_text_extraction_failed", resume_id=result["id"], mime=result["mime"],
                             error=result["extraction_error"])
        return result

    # ----- model configs and posting analysis -----

    def inference_client(self) -> InferenceClient:
        if self.inference is None:
            self.inference = InferenceClient(self.inference_transport)
        return self.inference

    def model_configs(self) -> list[dict]:
        assert self.reader is not None
        return model_configs.listing(self.reader)

    async def test_model_config(self, config_id: int) -> dict | None:
        """User action: load-and-snippet test (ARCHITECTURE 6.8). The model call runs with no
        transaction open; the result is written afterwards in its own short transaction."""
        assert self.user is not None
        cfg = load_config(self.user, config_id)
        if cfg is None:
            return None
        report = await model_configs.run_test(self.inference_client(), cfg)
        self.user.execute("BEGIN IMMEDIATE")
        try:
            if report["ok"]:
                model_configs.record_tested(self.user, config_id, now_ms())
            self.user.execute("INSERT INTO change_events (type, ref, created_at_ms) VALUES ('model_config_tested', ?, ?)",
                              (json.dumps({"model_config_id": config_id, "ok": report["ok"]}), now_ms()))
            self.user.execute("COMMIT")
        except Exception:
            self.user.execute("ROLLBACK")
            raise
        self.log.info("model_config_tested", model_config_id=config_id, ok=report["ok"], seconds=report.get("seconds"),
                      error=None if report["ok"] else (report.get("error") or "")[:200])
        self.notify_events()
        return report

    def set_model_config_enabled(self, config_id: int, enabled: bool) -> None:
        """User action. Raises LookupError (unknown) or ConfigError (enable before a passing test)."""
        assert self.user is not None
        self.user.execute("BEGIN IMMEDIATE")
        try:
            model_configs.set_enabled(self.user, config_id, enabled)
            self.user.execute("INSERT INTO change_events (type, ref, created_at_ms) VALUES (?, ?, ?)",
                              ("model_config_enabled" if enabled else "model_config_disabled",
                               json.dumps({"model_config_id": config_id}), now_ms()))
            self.user.execute("COMMIT")
        except Exception:
            self.user.execute("ROLLBACK")
            raise
        self.log.info("model_config_enabled" if enabled else "model_config_disabled", model_config_id=config_id)
        self.notify_events()  # also wakes the analysis worker

    def analysis_status(self) -> dict:
        if self.analysis is not None:
            return self.analysis.status()
        assert self.reader is not None
        ready, waiting = analysis_store.pending_config_ids(self.reader)
        return {"counts": analysis_store.counts(self.reader), "pending_waiting_for_disabled_config": waiting,
                "configs_with_work": ready, "inference": {}}

    def analysis_view(self, analysis_id: int) -> dict | None:
        assert self.reader is not None
        return analysis_store.analysis_view(self.reader, analysis_id)

    def job_analysis(self, job_id: int) -> dict | None:
        assert self.reader is not None
        aid = analysis_store.latest_for_job(self.reader, job_id)
        return None if aid is None else analysis_store.analysis_view(self.reader, aid)

    # ----- evaluation and recommendations (E3-E6) -----

    def evaluation_status(self) -> dict:
        if self.evaluation is not None:
            return self.evaluation.status()
        assert self.reader is not None
        ready, waiting = evaluation_store.pending_config_ids(self.reader)
        return {"counts": evaluation_store.counts(self.reader), "pending_waiting_for_disabled_config": waiting,
                "configs_with_work": ready, "inference": {}}

    def job_recommendation(self, job_id: int) -> dict | None:
        assert self.reader is not None
        return evaluation_store.job_recommendation(self.reader, job_id)

    def recommendations(self, category: str | None) -> list[dict]:
        assert self.reader is not None
        return evaluation_store.recommendation_list(self.reader, category)

    def review_questions(self) -> list[dict]:
        assert self.reader is not None
        return evaluation_store.review_questions(self.reader)

    def recompute(self) -> dict:
        """Snapshot, queue and recompute now (code only, no model call; the worker would do the
        same on its next iteration)."""
        assert self.evaluation_conn is not None
        if self.evaluation is not None:
            r = self.evaluation.sync()
        else:
            r = evaluation_store.sync(self.evaluation_conn, now_ms())
            self.notify_events()
        return {"snapshot_id": r.snapshot_id, "snapshot_created": r.snapshot_created, "queued": r.queued,
                "superseded": r.superseded, "recomputed": r.recomputed}

    # ----- window read models and applications (P1.8) -----

    def jobs(self, view: str, q: str | None, limit: int, offset: int) -> dict:
        assert self.reader is not None
        return views.list_jobs(self.reader, view, q, limit, offset)

    def job_detail(self, job_id: int) -> dict | None:
        assert self.reader is not None
        return views.job_detail(self.reader, job_id)

    def applications(self) -> list[dict]:
        assert self.reader is not None
        return applications.listing(self.reader)

    def set_application(self, job_id: int, fields: dict) -> dict:
        """User action: create or change the job's application; a status change appends an
        application_events row in the same transaction. Raises ApplicationError / LookupError."""
        assert self.user is not None
        self.user.execute("BEGIN IMMEDIATE")
        try:
            result = applications.set_for_job(self.user, job_id, fields, now_ms())
            self.user.execute("INSERT INTO change_events (type, job_id, ref, created_at_ms) VALUES ('application_changed', ?, ?, ?)",
                              (job_id, json.dumps({"application_id": result["id"], "status": result["status"]}), now_ms()))
            self.user.execute("COMMIT")
        except Exception:
            self.user.execute("ROLLBACK")
            raise
        self.log.info("application_changed", job_id=job_id, application_id=result["id"], status=result["status"])
        self.notify_events()
        return result

    def notify_events(self) -> None:
        """Call after a commit that added change_events rows; never before (no uncommitted events)."""
        if self._events_signal is not None:
            self._events_signal.set()
        if self.analysis is not None:
            self.analysis.wake()  # newly queued analyses or an enabled config: no 10 s idle wait

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
        http = HttpClient()
        self.discovery = DiscoveryService(self.discovery_conn, http, on_events=self.notify_events)
        self.analysis = AnalysisWorker(self.evaluation_conn, self.inference_client(), on_events=self.notify_events)
        self.evaluation = EvaluationWorker(self.evaluation_conn, self.inference_client(), self.analysis.gate,
                                           on_events=self.notify_events)
        self.analysis.steps_before.append(self.evaluation.run_once)
        # Publish the endpoint only once uvicorn is accepting connections.
        while not server.started and not server_task.done():
            await asyncio.sleep(0.01)
        if server_task.done():
            await server_task  # surfaces the startup error
            raise RuntimeError("IPC server stopped during startup")
        ipc.write_ipc_file(self.paths.ipc, endpoint, os.getpid())
        self.log.info("ipc_ready", port=endpoint.port)

        tasks = [asyncio.create_task(self.heartbeat()), asyncio.create_task(self.maintenance()),
                 asyncio.create_task(self.discovery.run(self.stop_event)), asyncio.create_task(self.analysis.run(self.stop_event))]
        stop_task = asyncio.create_task(self.stop_event.wait())
        done, _ = await asyncio.wait({server_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if server_task in done and not self.stop_event.is_set():
            # uvicorn exited on its own (e.g. Ctrl+C / console close): stop the rest too.
            self.stop_reason = self.stop_reason if self.stop_reason != "unknown" else "signal"
            self.stop_event.set()
        server.should_exit = True
        results = await asyncio.gather(server_task, *tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                self.log.error("task_failed_on_stop", error=f"{type(r).__name__}: {r}")
        stop_task.cancel()
        await http.aclose()
        await self.inference_client().aclose()
        self.paths.ipc.unlink(missing_ok=True)
