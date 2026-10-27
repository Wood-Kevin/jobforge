"""The analysis worker: pending posting analyses -> E0 -> E1 (model) -> E2 -> requirements (ARCHITECTURE 3.4, 6.4).

One worker, one item at a time, oldest first. A health gate stands in front of every claim:
nothing is claimed while the inference server does not answer `/v1/models`, so a stopped LM
Studio costs no attempts, and work resumes by itself when the server answers again (probe
every 30 s while up; 60 s doubling to 15 min while down). Transitions log `inference_available`
and `inference_unavailable`.

Outcomes per analysis:
- done: requirements stored with both readings (E2).
- failed (final): context_overflow, budget_exhausted (`finish_reason = length`, never parsed),
  invalid_output (schema-invalid twice: one retry), or MAX_ATTEMPTS timeouts/HTTP errors.
  Raw responses are kept on the row; the log line has ids and the error kind only.
- deferred: the server went away mid-call; back to pending, no attempt spent.
A bug in E0/E2 fails that analysis with `internal_error` and an error log; the worker goes on.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from jobforge_agent import logs
from jobforge_agent.analysis import extract, store
from jobforge_agent.analysis.interpret import interpret
from jobforge_agent.analysis.segment import segment
from jobforge_agent.inference import steps
from jobforge_agent.inference.client import InferenceClient, InferenceError, ModelConfig, load_config

IDLE_POLL_S = 10.0
UP_RECHECK_S = 30.0
DOWN_BACKOFF_S = (60.0, 900.0)


def _now_ms() -> int:
    return int(time.time() * 1000)


CAPPED_BACKOFF_S = (900.0, 7200.0)  # a plan cap clears on OpenAI's schedule (unpublished): try again after 15 min, up to 2 h


@dataclass
class GateState:
    up: bool | None = None
    reason: str | None = None
    next_check_ms: int = 0
    backoff_s: float = DOWN_BACKOFF_S[0]
    capped: bool = False
    cap_backoff_s: float = CAPPED_BACKOFF_S[0]  # doubles per cap in a row; reset by a call that works (call_ok)


class HealthGate:
    """Is a config's server answering and does it list the model? Cached between probes.

    Also the only writer of inference/steps.OUT (SPEC D18): a config that is down or capped is in it, so a step
    whose own config is a cloud config runs on the fallback meanwhile. A capped config is not probed until its
    backoff ends (`/v1/models` answers while the plan is capped, so only a real call can tell); then it counts as
    available again, and the next call either works or caps it again with a doubled backoff."""

    def __init__(self, client: InferenceClient, now_ms: Callable[[], int] = _now_ms):
        self.client = client
        self.now_ms = now_ms
        self.states: dict[int, GateState] = {}
        self.log = logs.get("inference")
        steps.OUT.clear()  # one gate per agent: nothing is out until a probe or a call says so

    async def available(self, cfg: ModelConfig) -> bool:
        st = self.states.setdefault(cfg.id, GateState())
        if st.up is not None and self.now_ms() < st.next_check_ms:
            return st.up
        try:
            models = await self.client.models(cfg)
        except InferenceError as exc:
            self.mark_down(cfg, exc.detail)
            return False
        if cfg.model_id not in models:
            self.mark_down(cfg, "model_not_listed")
            return False
        if st.up is not True:
            self.log.info("inference_available", model_config_id=cfg.id, models_listed=len(models))
        if steps.OUT.pop(cfg.id, None) is not None and cfg.provider_kind != "openai_compatible":
            self.log.info("model_cloud_back", model_config_id=cfg.id)
        st.up, st.reason, st.backoff_s, st.capped = True, None, DOWN_BACKOFF_S[0], False
        st.next_check_ms = self.now_ms() + int(UP_RECHECK_S * 1000)
        return True

    def mark_down(self, cfg: ModelConfig, reason: str, capped: bool = False) -> None:
        st = self.states.setdefault(cfg.id, GateState())
        if capped:
            st.backoff_s = st.cap_backoff_s
            st.cap_backoff_s = min(st.cap_backoff_s * 2, CAPPED_BACKOFF_S[1])
            self.log.warning("inference_capped", model_config_id=cfg.id, reason=reason[:200], retry_in_s=st.backoff_s)
        elif st.up is not False or st.reason != reason:
            self.log.warning("inference_unavailable", model_config_id=cfg.id, reason=reason[:200], retry_in_s=st.backoff_s)
        else:
            st.backoff_s = min(st.backoff_s * 2, DOWN_BACKOFF_S[1])
        if cfg.id not in steps.OUT and cfg.provider_kind != "openai_compatible":
            # The steps on this config now run on the fallback (verify.sh: D18 "cap falls back to local").
            self.log.warning("model_fallback", model_config_id=cfg.id, reason="capped" if capped else reason[:200])
        steps.OUT[cfg.id] = "capped" if capped else reason[:200]
        st.up, st.reason, st.capped = False, reason, capped
        st.next_check_ms = self.now_ms() + int(st.backoff_s * 1000)

    def call_ok(self, cfg: ModelConfig) -> None:
        """A model call worked: a later cap starts again from the shortest wait."""
        st = self.states.get(cfg.id)
        if st is not None:
            st.cap_backoff_s = CAPPED_BACKOFF_S[0]

    async def recheck_due(self, conn: sqlite3.Connection) -> None:
        """Probe out configs whose wait is over, whether or not work is queued on them: while a cloud config is out
        its work moves to the fallback, so nothing else would ever probe it again."""
        now = self.now_ms()
        for cid in [c for c in steps.OUT if self.states.get(c) and self.states[c].next_check_ms <= now]:
            cfg = load_config(conn, cid)
            if cfg is None or not cfg.enabled:
                steps.OUT.pop(cid, None)
                continue
            st = self.states[cid]
            if st.capped:
                # No probe can tell a cap has cleared: count it as available and let the next call tell.
                steps.OUT.pop(cid, None)
                st.up, st.next_check_ms = None, 0
                self.log.info("model_cloud_retry", model_config_id=cid, after="capped")
                continue
            await self.available(cfg)

    def next_check_ms(self) -> int | None:
        downs = [s.next_check_ms for s in self.states.values() if s.up is False]
        return min(downs) if downs else None

    def snapshot(self) -> dict:
        return {str(k): {"up": v.up, "reason": v.reason, "next_check_ms": v.next_check_ms, "capped": v.capped}
                for k, v in self.states.items()}


class AnalysisWorker:
    def __init__(self, conn: sqlite3.Connection, client: InferenceClient, on_events: Callable[[], None] = lambda: None,
                 now_ms: Callable[[], int] = _now_ms, before_call: Callable[[], Awaitable[None]] | None = None):
        assert getattr(conn, "component", None) == "evaluation", "the worker writes through the evaluation connection"
        self.conn = conn
        self.client = client
        self.gate = HealthGate(client, now_ms)
        self.on_events = on_events
        self.now_ms = now_ms
        self.log = logs.get("analysis")
        self._wake = asyncio.Event()
        self._waiting_logged: int | None = None
        self.before_call = before_call  # test hook: runs just before each model call
        self.calls = 0
        # Steps tried before posting analysis on every loop iteration (P1.7: the evaluation step,
        # so a job half way through the pipeline finishes first). Same loop, so one model call at a time.
        self.steps_before: list[Callable[[], Awaitable[str | None]]] = []

    def wake(self) -> None:
        self._wake.set()

    def status(self) -> dict:
        ready, waiting = store.pending_config_ids(self.conn)
        return {"counts": store.counts(self.conn), "pending_waiting_for_disabled_config": waiting,
                "configs_with_work": ready, "inference": self.gate.snapshot()}

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                outcome = None
                for step in self.steps_before:
                    outcome = await step()
                    if outcome is not None:
                        break
                if outcome is None:
                    outcome = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # keep the loop alive; the failure is in the error log
                self.log.error("analysis_worker_error", error=f"{type(exc).__name__}: {exc}")
                outcome = None
            if outcome is not None:
                continue
            timeout = IDLE_POLL_S
            nxt = self.gate.next_check_ms()
            if nxt is not None:
                timeout = max(0.5, min(timeout, (nxt - self.now_ms()) / 1000))
            self._wake.clear()
            waiter = asyncio.ensure_future(self._wake.wait())
            stopper = asyncio.ensure_future(stop.wait())
            await asyncio.wait({waiter, stopper}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            waiter.cancel()
            stopper.cancel()

    async def run_once(self) -> str | None:
        """Process one analysis if any can run now; returns its outcome, or None."""
        ready, waiting = store.pending_config_ids(self.conn)
        if waiting != self._waiting_logged and waiting:
            # Visible, not silent: work exists but its model config is not enabled (test it first).
            self.log.warning("analysis_waiting", pending=waiting, reason="model_config_not_enabled")
        self._waiting_logged = waiting
        await self.gate.recheck_due(self.conn)
        for config_id in ready:
            cfg = load_config(self.conn, config_id)
            if cfg is None:
                continue
            if not await self.gate.available(cfg):
                # A cloud config that is out hands its waiting postings to the fallback (SPEC D18), which
                # claims them on a later iteration; a local config that is down simply waits.
                fallback = steps.active(self.conn, "e1") if cfg.id == steps.configured(self.conn, "e1") else None
                if cfg.provider_kind != "openai_compatible" and fallback not in (None, cfg.id):
                    moved = store.move_pending(self.conn, cfg.id, fallback)
                    if moved:
                        self.log.info("analyses_moved_to_fallback", count=moved, from_config=cfg.id, to_config=fallback)
                        return "moved"
                continue
            c = store.claim(self.conn, cfg.id, self.now_ms(), lease_ms=int(cfg.timeout_s * 2 * 1000) + 60_000)
            if c is None:
                continue
            return await self.process(c, cfg)
        return None

    async def process(self, c: store.Claim, cfg: ModelConfig) -> str:
        t0 = time.perf_counter()
        attempts: list[dict] = []
        try:
            seg = segment(c.text)
            stored = store.stored_extraction(self.conn, c.analysis_id)
            if stored is not None:
                return self._reuse(c, seg, *stored, t0)
            msgs = extract.messages(c.title, c.text, cfg.structured_output_mode)
            fmt = extract.response_format(cfg.structured_output_mode)
            items = None
            for attempt in (1, 2):  # one retry on schema-invalid output
                if self.before_call is not None:
                    await self.before_call()
                self.calls += 1
                result = await self.client.chat(cfg, msgs, fmt)
                self.gate.call_ok(cfg)
                attempts.append({"content": result.content, "finish_reason": result.finish_reason,
                                 "usage": result.usage, "seconds": result.seconds, "model": result.model_reported})
                if result.finish_reason == "length":
                    return self._fail(c, "budget_exhausted", f"finish_reason=length at max_tokens={cfg.max_tokens}", attempts)
                try:
                    items = extract.parse(result.content)
                    break
                except extract.InvalidOutput as exc:
                    attempts[-1]["error"] = str(exc)
            if items is None:
                return self._fail(c, "invalid_output", attempts[-1]["error"], attempts)
            reqs = interpret(seg, items)
            counts = store.persist_done(self.conn, c, seg, reqs, {"attempts": attempts}, attempts[-1]["model"], self.now_ms())
        except asyncio.CancelledError:
            store.release(self.conn, c, spend_attempt=False, error="agent stopping", now=self.now_ms())
            raise
        except InferenceError as exc:
            if exc.kind in ("unavailable", "capped"):
                self.gate.mark_down(cfg, exc.detail, capped=exc.kind == "capped")
                status = store.release(self.conn, c, spend_attempt=False, error=f"deferred: {exc.detail}", now=self.now_ms())
                self.log.info("analysis_deferred", analysis_id=c.analysis_id, job_id=c.job_id, status=status)
                return "deferred"
            if exc.kind == "context_overflow":
                return self._fail(c, "context_overflow", exc.detail, attempts or None)
            status = store.release(self.conn, c, spend_attempt=True, error=f"{exc.kind}: {exc.detail}"[:1000], now=self.now_ms())
            self.log.warning("analysis_attempt_failed", analysis_id=c.analysis_id, job_id=c.job_id, error=exc.kind, status=status)
            if status == "failed":
                self.on_events()
            return f"attempt_failed:{status}"
        except Exception as exc:  # E0/E2/persist bug: this analysis fails visibly, the worker goes on
            self.log.error("analysis_internal_error", analysis_id=c.analysis_id, error=f"{type(exc).__name__}: {exc}")
            return self._fail(c, "internal_error", f"{type(exc).__name__}: {exc}", attempts or None)
        self.log.info("analysis_done", analysis_id=c.analysis_id, job_id=c.job_id, seconds=round(time.perf_counter() - t0, 1),
                      model_calls=len(attempts), **counts)
        self.on_events()
        return "done"

    def _reuse(self, c: store.Claim, seg, source_id: int, raw: dict, model_reported: str | None, t0: float) -> str:
        """E2 again on the posting's stored E1 answer (store.stored_extraction): no model call. A stored answer
        that no longer parses is a failure here, not a silent new model call."""
        try:
            items = extract.parse(raw["attempts"][-1]["content"])
        except extract.InvalidOutput as exc:
            return self._fail(c, "invalid_output", f"stored E1 answer of analysis {source_id}: {exc}", None)
        counts = store.persist_done(self.conn, c, seg, interpret(seg, items), {**raw, "reused_from_analysis": source_id},
                                    model_reported, self.now_ms())
        self.log.info("analysis_done", analysis_id=c.analysis_id, job_id=c.job_id, seconds=round(time.perf_counter() - t0, 1),
                      model_calls=0, reused_from_analysis=source_id, **counts)
        self.on_events()
        return "done"

    def _fail(self, c: store.Claim, kind: str, detail: str, attempts: list[dict] | None) -> str:
        store.persist_failed(self.conn, c, kind, detail, {"attempts": attempts} if attempts else None, self.now_ms())
        self.log.warning("analysis_failed", analysis_id=c.analysis_id, job_id=c.job_id, error=kind)
        self.on_events()
        return f"failed:{kind}"
