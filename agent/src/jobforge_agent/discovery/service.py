"""The discovery loop that runs inside the agent (ARCHITECTURE 3.3).

Every TICK_S: detect gaps (sleep/resume or a stalled process) and re-spread overdue work,
claim due employers within budget, and poll each in its own task. Polls of one host are
serialized and spaced by the HostLimiter; different hosts run in parallel.
"""
from __future__ import annotations

import asyncio
import random
import sqlite3
from typing import Callable

from jobforge_agent import logs
from jobforge_agent.discovery import poller, scheduler
from jobforge_agent.discovery.clock import Clock
from jobforge_agent.discovery.http import HttpClient

TICK_S = 5.0
GAP_S = 120.0
STOP_GRACE_S = 5.0
BUDGET_LOG_EVERY_MS = 3_600_000


class DiscoveryService:
    def __init__(self, conn: sqlite3.Connection, http: HttpClient, clock: Clock | None = None,
                 rng: random.Random | None = None, on_events: Callable[[], None] = lambda: None):
        self.conn = conn
        self.http = http
        self.clock = clock or Clock()
        self.rng = rng or random.Random()
        self.on_events = on_events
        self.log = logs.get("discovery")
        self.tasks: set[asyncio.Task] = set()
        self._budget_logged: dict[int, int] = {}
        self.polls_done = 0

    async def tick(self) -> list[asyncio.Task]:
        """Claim and start polls; returns the tasks started (tests await them)."""
        now = self.clock.now_ms()
        claims, over_budget = scheduler.claim_due(self.conn, now)
        for source_id in over_budget:
            if now - self._budget_logged.get(source_id, 0) > BUDGET_LOG_EVERY_MS:
                self._budget_logged[source_id] = now
                self.log.warning("budget_deferred", source_id=source_id)
        started = []
        for c in claims:
            t = asyncio.create_task(self._poll(c), name=f"poll-{c.employer_id}")
            self.tasks.add(t)
            t.add_done_callback(self.tasks.discard)
            started.append(t)
        return started

    async def _poll(self, claim: scheduler.Claim) -> None:
        try:
            result = await poller.poll(self.conn, self.http, claim, self.clock.now_ms, self.rng)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # poll() records its own failures; reaching here means _close itself failed (e.g. database
            # busy). The fetch row stays unfinished and blocks this employer until the startup recovery
            # sweep: the lease expiring does not free it (scheduler.claim_due).
            self.log.error("poll_crashed", employer_id=claim.employer_id, error=f"{type(exc).__name__}: {exc}")
            return
        self.polls_done += 1
        rec = result.record
        if rec.error and rec.error.startswith("internal:"):  # a bug, not a board problem: the error log, not just Status
            self.log.error("poll_internal_error", employer_id=claim.employer_id, error=rec.error)
        self.log.info("poll", employer_id=claim.employer_id, host=claim.host, outcome=rec.outcome, http_status=rec.http_status,
                      requests=rec.requests_made, bytes=rec.bytes_received, new=rec.new_count, changed=rec.changed_count,
                      closed=rec.closed_count, error=rec.error)
        if result.events:
            self.on_events()

    async def run(self, stop: asyncio.Event) -> None:
        moved = scheduler.respread_overdue(self.conn, self.clock.now_ms(), self.rng)
        self.log.info("discovery_start", respread=moved)
        last = self.clock.monotonic()
        while not stop.is_set():
            mono = self.clock.monotonic()
            if mono - last > GAP_S:
                moved = scheduler.respread_overdue(self.conn, self.clock.now_ms(), self.rng)
                self.log.info("gap_detected", gap_s=round(mono - last), respread=moved)
            last = mono
            try:
                await self.tick()
            except Exception as exc:  # e.g. database busy: log, keep ticking
                self.log.error("discovery_tick_failed", error=f"{type(exc).__name__}: {exc}")
            try:
                await asyncio.wait_for(stop.wait(), timeout=TICK_S)
            except TimeoutError:
                pass
        await self.drain()

    async def drain(self) -> None:
        """On stop: give in-flight polls a moment, then cancel (they record INTERRUPTED)."""
        if not self.tasks:
            return
        done, pending = await asyncio.wait(set(self.tasks), timeout=STOP_GRACE_S)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
