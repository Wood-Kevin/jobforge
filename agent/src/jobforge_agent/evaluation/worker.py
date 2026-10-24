"""The evaluation step of the model worker: E3 (model) -> E4 -> E5/E6 (ARCHITECTURE 3.4, 6.4).

It runs in the same loop as the analysis worker and shares its health gate, so there is one
model call at a time and one place that knows whether the server is up. Each loop iteration:
1. sync() — code only, so it runs while inference is down: snapshot, queue, recompute
   ("recommendations still recompute", 6.8);
2. if the gate says up, claim the oldest pending evaluation and process it. Evaluations go
   before analyses so a job that is half way through the pipeline reaches a recommendation first.

Attempt accounting matches analysis (worker.py there): unavailable -> back to pending, no attempt;
timeout / HTTP error -> one attempt, failed after 3; length -> failed budget_exhausted;
context overflow -> failed; schema-invalid twice -> failed invalid_output; a bug in E4-E6 or
persist -> failed internal_error, the loop goes on.
Requirements matched by code (eligibility) never reach the model; with nothing for the model
to match (no fit requirements or an empty profile) no call is made at all.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Awaitable, Callable

from jobforge_agent import logs
from jobforge_agent.analysis.worker import HealthGate
from jobforge_agent.evaluation import facts, match, store, validate
from jobforge_agent.evaluation.assess import load_rules
from jobforge_agent.evaluation.versions import VERDICT_RULE_VERSION
from jobforge_agent.inference.client import InferenceClient, InferenceError, ModelConfig, load_config


def _now_ms() -> int:
    return int(time.time() * 1000)


class EvaluationWorker:
    def __init__(self, conn: sqlite3.Connection, client: InferenceClient, gate: HealthGate,
                 on_events: Callable[[], None] = lambda: None, now_ms: Callable[[], int] = _now_ms,
                 before_call: Callable[[], Awaitable[None]] | None = None):
        assert getattr(conn, "component", None) == "evaluation", "the worker writes through the evaluation connection"
        self.conn = conn
        self.client = client
        self.gate = gate
        self.on_events = on_events
        self.now_ms = now_ms
        self.before_call = before_call  # test hook: runs just before each model call
        self.log = logs.get("evaluation")
        self.calls = 0
        self._waiting_logged: int | None = None

    def status(self) -> dict:
        ready, waiting = store.pending_config_ids(self.conn)
        return {"counts": store.counts(self.conn), "pending_waiting_for_disabled_config": waiting, "configs_with_work": ready,
                "inference": self.gate.snapshot()}

    def sync(self) -> store.SyncResult:
        r = store.sync(self.conn, self.now_ms())
        if r.snapshot_created:
            self.log.info("profile_snapshot_created", snapshot_id=r.snapshot_id)
        if r.queued:
            self.log.info("evaluations_queued", count=r.queued, superseded=r.superseded, snapshot_id=r.snapshot_id)
        if r.recomputed:
            self.log.info("recommendations_recomputed", count=r.recomputed, rule_version=VERDICT_RULE_VERSION)
        if r.queued or r.recomputed:
            self.on_events()
        return r

    async def run_once(self) -> str | None:
        try:
            self.sync()
        except Exception as exc:  # visible in the error log; claiming below and posting analysis go on
            self.log.error("evaluation_sync_failed", error=f"{type(exc).__name__}: {exc}")
        ready, waiting = store.pending_config_ids(self.conn)
        if waiting != self._waiting_logged and waiting:
            self.log.warning("evaluation_waiting", pending=waiting, reason="model_config_not_enabled")
        self._waiting_logged = waiting
        # A stored answer to the exact same request needs no model, so no health gate either.
        c = store.claim_reusable(self.conn, self.now_ms(), lease_ms=300_000)
        if c is not None:
            cfg = load_config(self.conn, c.model_config_id)
            return await self.process(c, cfg)
        for config_id in ready:
            cfg = load_config(self.conn, config_id)
            if cfg is None or not await self.gate.available(cfg):
                continue
            c = store.claim(self.conn, cfg.id, self.now_ms(), lease_ms=int(cfg.timeout_s * 2 * 1000) + 60_000)
            if c is None:
                continue
            return await self.process(c, cfg)
        return None

    async def process(self, c: store.Claim, cfg: ModelConfig) -> str | None:
        t0 = time.perf_counter()
        attempts: list[dict] = []
        try:
            prep = store.prepare(self.conn, c.analysis_id, c.snapshot_id, c.title, cfg)
            reqs, snap, by_code, by_model = prep.reqs, prep.snap, prep.by_code, prep.by_model
            rules = load_rules(VERDICT_RULE_VERSION)
            facts_list = snap.facts()
            fact_matches = [validate.fact_match(r, facts.check(r, facts_list, snap.completeness, reqs), rules) for r in by_code]
            experience = snap.experience()
            model_matches: dict[int, object] = {}
            skipped: dict[int, str] = {}
            claims: list = []
            raw: dict = {"attempts": attempts}
            if by_model and not experience:
                skipped = {r.id: "no_profile_items_model_not_called" for r in by_model}
            elif by_model:
                msgs, labels, fmt = prep.msgs, prep.labels, prep.fmt
                out = None
                stored = store.stored_answer(self.conn, prep.sha, cfg.id) if prep.sha else None
                if stored is not None:
                    try:
                        out = match.parse(stored["content"])
                        raw.update(reused_from=stored["from_evaluation_id"],
                                   reused={k: stored[k] for k in ("content", "model", "usage")})
                    except match.InvalidOutput as exc:
                        # Only if match.parse got stricter than when the answer was stored. Say so, then
                        # ask the model as usual, but through the health gate: a reuse claim skipped it,
                        # and a down server answered "deferred", which retried at once in a tight loop.
                        out = None
                        self.log.error("stored_answer_unreadable", evaluation_id=c.evaluation_id,
                                       from_evaluation_id=stored["from_evaluation_id"], error=str(exc)[:300])
                        if not await self.gate.available(cfg):
                            store.release(self.conn, c, spend_attempt=False,
                                          error="deferred: stored answer unreadable and the model is not available",
                                          now=self.now_ms())
                            return None  # nothing ran: the worker loop waits instead of claiming it again at once
                for attempt in (1, 2) if out is None else ():  # one retry on schema-invalid output
                    if self.before_call is not None:
                        await self.before_call()
                    self.calls += 1
                    result = await self.client.chat(cfg, msgs, fmt)
                    attempts.append({"content": result.content, "finish_reason": result.finish_reason,
                                     "usage": result.usage, "seconds": result.seconds, "model": result.model_reported})
                    if result.finish_reason == "length":
                        return self._fail(c, "budget_exhausted", f"finish_reason=length at max_tokens={cfg.max_tokens}", attempts)
                    try:
                        out = match.parse(result.content)
                        break
                    except match.InvalidOutput as exc:
                        attempts[-1]["error"] = str(exc)
                if out is None:
                    return self._fail(c, "invalid_output", attempts[-1]["error"], attempts)
                unknown_labels, duplicates = [], 0
                for m in out.matches:
                    rid = labels.get(m.requirement)
                    if rid is None:
                        unknown_labels.append(m.requirement)
                    elif rid in model_matches:
                        duplicates += 1  # first answer per requirement stands
                    else:
                        model_matches[rid] = m
                for r in by_model:
                    model_matches.setdefault(r.id, None)  # omitted by the model -> unknown (flag model_omitted)
                claims = out.inferred_claims
                raw.update(labels=labels, unknown_requirement_labels=unknown_labels, duplicate_matches=duplicates)
            model_reported = attempts[-1]["model"] if attempts else (raw.get("reused") or {}).get("model")
            counts = store.persist_done(self.conn, c, reqs, fact_matches, model_matches, skipped, claims, raw,
                                        model_reported, self.now_ms(), prep.sha)
        except asyncio.CancelledError:
            store.release(self.conn, c, spend_attempt=False, error="agent stopping", now=self.now_ms())
            raise
        except InferenceError as exc:
            if exc.kind == "unavailable":
                self.gate.mark_down(cfg, exc.detail)
                status = store.release(self.conn, c, spend_attempt=False, error=f"deferred: {exc.detail}", now=self.now_ms())
                self.log.info("evaluation_deferred", evaluation_id=c.evaluation_id, job_id=c.job_id, status=status)
                return "deferred"
            if exc.kind == "context_overflow":
                return self._fail(c, "context_overflow", exc.detail, attempts or None)
            status = store.release(self.conn, c, spend_attempt=True, error=f"{exc.kind}: {exc.detail}"[:1000], now=self.now_ms())
            self.log.warning("evaluation_attempt_failed", evaluation_id=c.evaluation_id, job_id=c.job_id, error=exc.kind,
                             status=status)
            if status == "failed":
                self.on_events()
            return f"attempt_failed:{status}"
        except Exception as exc:  # E4-E6/persist bug: this evaluation fails visibly, the loop goes on
            self.log.error("evaluation_internal_error", evaluation_id=c.evaluation_id, error=f"{type(exc).__name__}: {exc}")
            return self._fail(c, "internal_error", f"{type(exc).__name__}: {exc}", attempts or None)
        self.log.info("evaluation_done", evaluation_id=c.evaluation_id, job_id=c.job_id,
                      seconds=round(time.perf_counter() - t0, 1), model_calls=len(attempts),
                      reused_from=raw.get("reused_from"), **counts)
        self.on_events()
        return "done"

    def _fail(self, c: store.Claim, kind: str, detail: str, attempts: list[dict] | None) -> str:
        store.persist_failed(self.conn, c, kind, detail, {"attempts": attempts} if attempts else None, self.now_ms())
        self.log.warning("evaluation_failed", evaluation_id=c.evaluation_id, job_id=c.job_id, error=kind)
        self.on_events()
        return f"failed:{kind}"
