"""Scheduler and poller against a fake job board and a fake clock (ARCHITECTURE 3.3, SPEC D5)."""
from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from pathlib import Path

import httpx
import pytest

from jobforge_agent.db.connect import open_connection
from jobforge_agent.discovery import admin, poller, scheduler
from jobforge_agent.discovery.clock import FakeClock
from jobforge_agent.discovery.http import HostLimiter, HttpClient
from jobforge_agent.discovery.service import DiscoveryService

FIX = Path(__file__).parent / "fixtures" / "ats"
GH_BODY = (FIX / "greenhouse_cloudflare.json").read_bytes()
INTERVAL_MS = 900_000


class Board:
    """Routes requests by path; records every request."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.robots = {"default": (200, "User-agent: *\nAllow: /\n")}
        self.routes: dict[str, list] = {}

    def on(self, slug: str, *responses):
        self.routes[slug] = list(responses)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/robots.txt":
            status, body = self.robots.get(request.url.host, self.robots["default"])
            return httpx.Response(status, text=body)
        slug = request.url.path.rstrip("/").split("/")[-2 if "boards" in request.url.path else -1]
        queue = self.routes.get(slug) or [(200, GH_BODY, {"ETag": 'W/"v1"'})]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if callable(item):
            return await item(request)
        status, body, headers = item
        return httpx.Response(status, content=body, headers=headers)

    def board_requests(self):
        return [r for r in self.requests if r.url.path != "/robots.txt"]


@pytest.fixture
def env(db, paths):
    clock = FakeClock()
    board = Board()
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    user = open_connection(paths.db, "user")
    disc = open_connection(paths.db, "discovery")
    http = HttpClient(transport=httpx.MockTransport(board), limiter=HostLimiter(sleep=fake_sleep, monotonic=clock.monotonic, rng=random.Random(1)))

    def add(*slugs, kind="greenhouse", due_now=True):
        admin.import_employers(user, [{"source": kind, "slug": s, "name": s} for s in slugs], clock.now_ms(), random.Random(2))
        if due_now:
            user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ?", (clock.now_ms(),))
        return [r[0] for r in db.execute(f"SELECT id FROM employers WHERE slug IN ({','.join('?' * len(slugs))}) ORDER BY id", slugs)]

    async def poll_due():
        claims, _ = scheduler.claim_due(disc, clock.now_ms())
        return [await poller.poll(disc, http, c, clock.now_ms, random.Random(3)) for c in claims]

    yield type("Env", (), dict(db=db, paths=paths, clock=clock, board=board, sleeps=sleeps, user=user, disc=disc, http=http,
                               add=staticmethod(add), poll_due=staticmethod(poll_due)))
    for c in (user, disc):
        c.close()


def run(coro):
    return asyncio.run(coro)


def state(db, employer_id):
    return dict(db.execute("SELECT * FROM employer_poll_state WHERE employer_id = ?", (employer_id,)).fetchone())


# ----- pure timing rules -----

def test_interval_clamped_to_terms_floor_and_a_day():
    assert scheduler.effective_interval_s(None, 900, 300) == 900
    assert scheduler.effective_interval_s(60, 900, 300) == 300  # override cannot undercut the terms floor
    assert scheduler.effective_interval_s(10**9, 900, 300) == 24 * 3600


def test_backoff_doubles_and_caps_at_six_hours():
    rng = random.Random(0)
    for failures, base in ((1, 1800), (2, 3600), (3, 7200), (10, 6 * 3600)):
        delay = (scheduler.next_after_failure(0, 900, failures, rng)) / 1000
        assert base * 0.9 <= delay <= base * 1.1


def test_jitter_stays_within_ten_percent():
    rng = random.Random(0)
    delays = [scheduler.next_after_success(0, 900, rng) / 1000 for _ in range(500)]
    assert 810 <= min(delays) and max(delays) <= 990 and max(delays) - min(delays) > 100


# ----- claims -----

def test_claims_are_exclusive_and_a_second_in_flight_row_is_impossible(env):
    env.add("a", "b", "c")
    other = open_connection(env.paths.db, "discovery")
    c1, _ = scheduler.claim_due(env.disc, env.clock.now_ms(), batch=2)
    c2, _ = scheduler.claim_due(other, env.clock.now_ms(), batch=2)
    ids1, ids2 = {c.employer_id for c in c1}, {c.employer_id for c in c2}
    assert ids1.isdisjoint(ids2) and len(ids1 | ids2) == 3
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        env.disc.execute("INSERT INTO source_fetches (employer_id, started_at_ms) VALUES (?, ?)", (c1[0].employer_id, 1))
    other.close()


def test_paused_discovery_claims_nothing(env):
    env.add("a")
    env.user.execute("UPDATE settings SET value = '1' WHERE key = 'discovery_paused'")
    assert scheduler.claim_due(env.disc, env.clock.now_ms()) == ([], [])


# ----- conditional requests -----

def test_200_then_304_with_etag(env):
    [emp] = env.add("cloudflare")
    env.board.on("cloudflare", (200, GH_BODY, {"ETag": 'W/"v1"'}), (304, b"", {"ETag": 'W/"v1"'}))
    [r1] = run(env.poll_due())
    assert (r1.record.outcome, r1.record.postings_seen, r1.record.new_count) == ("OK", 3, 3)
    assert state(env.db, emp)["etag"] == 'W/"v1"'
    env.clock.advance(1000)
    env.user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ?", (env.clock.now_ms(),))
    [r2] = run(env.poll_due())
    last = env.board.board_requests()[-1]
    assert last.headers.get("if-none-match") == 'W/"v1"'
    assert (r2.record.outcome, r2.record.new_count) == ("NOT_MODIFIED", 0)
    assert r2.record.bytes_received < 500
    assert env.db.execute("SELECT count(*) FROM raw_postings").fetchone()[0] == 3
    assert {r[0] for r in env.db.execute("SELECT last_seen_at_ms FROM jobs")} == {env.clock.now_ms()}


# ----- backoff and access restrictions -----

def test_retry_after_pauses_the_whole_host(env):
    a, b = env.add("a", "b")
    env.board.on("a", (429, b"", {"Retry-After": "120"}))
    env.user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ? WHERE employer_id = ?", (env.clock.now_ms() + 10**9, b))
    [r] = run(env.poll_due())
    assert r.record.retry_after_s == 120
    until = env.db.execute("SELECT backoff_until_ms FROM sources WHERE kind = 'greenhouse'").fetchone()[0]
    assert until == env.clock.now_ms() + 120_000
    assert state(env.db, a)["consecutive_failures"] == 0  # host limit, not this employer's fault
    env.user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ?", (env.clock.now_ms(),))
    env.clock.advance(60)
    assert scheduler.claim_due(env.disc, env.clock.now_ms())[0] == []
    before = len(env.board.requests)
    env.clock.advance(61)
    claims, _ = scheduler.claim_due(env.disc, env.clock.now_ms())
    assert len(claims) == 2 and len(env.board.requests) == before


def test_server_errors_back_off_exponentially(env):
    [emp] = env.add("a")
    env.board.on("a", (503, b"", {}))
    gaps = []
    for _ in range(3):
        env.user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ?", (env.clock.now_ms(),))
        [r] = run(env.poll_due())
        gaps.append((r.next_poll_at_ms - env.clock.now_ms()) / INTERVAL_MS)
    assert state(env.db, emp)["consecutive_failures"] == 3
    assert 1.8 <= gaps[0] <= 2.2 and 3.6 <= gaps[1] <= 4.4 and 7.2 <= gaps[2] <= 8.8


def test_three_404s_mark_dead_and_403_marks_blocked(env):
    dead, blocked = env.add("gone", "private")
    env.board.on("gone", (404, b"", {}))
    env.board.on("private", (403, b"", {}))
    for _ in range(3):
        env.user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ?", (env.clock.now_ms(),))
        run(env.poll_due())
    statuses = dict(env.db.execute("SELECT slug, status FROM employers").fetchall())
    assert statuses == {"gone": "dead", "private": "blocked"}
    env.user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ?", (env.clock.now_ms(),))
    assert scheduler.claim_due(env.disc, env.clock.now_ms())[0] == []  # neither is polled again


def test_robots_disallow_skips_without_touching_the_board(env):
    [emp] = env.add("a")
    env.board.robots["default"] = (200, "User-agent: *\nDisallow: /\n")
    [r] = run(env.poll_due())
    assert r.record.outcome == "SKIPPED" and env.board.board_requests() == []
    expires = env.db.execute("SELECT expires_at_ms FROM robots_cache").fetchone()[0]
    assert r.next_poll_at_ms == expires


def test_robots_4xx_means_no_rules_rfc_9309(env):
    env.add("a")
    env.board.robots["default"] = (401, "Unauthorized")  # Ashby answers this way
    [r] = run(env.poll_due())
    assert r.record.outcome == "OK"


def test_hourly_budget_stops_claims(env):
    env.add("a", "b", "c")
    env.user.execute("UPDATE sources SET max_requests_per_hour = 3 WHERE kind = 'greenhouse'")
    run(env.poll_due())  # robots + 3 boards = 4 requests
    env.user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ?", (env.clock.now_ms(),))
    claims, over = scheduler.claim_due(env.disc, env.clock.now_ms())
    gh = env.db.execute("SELECT id FROM sources WHERE kind = 'greenhouse'").fetchone()[0]
    assert claims == [] and over == [gh]
    env.clock.advance(3601)
    assert len(scheduler.claim_due(env.disc, env.clock.now_ms())[0]) > 0


def test_same_host_polls_are_spaced(env):
    env.add("a", "b")
    run(env.poll_due())
    assert any(s >= 2.0 for s in env.sleeps), env.sleeps  # request_spacing_ms = 2000 plus jitter


# ----- recovery, catch-up and clocks -----

def test_downtime_backlog_is_spread_over_one_interval(env):
    slugs = [f"e{i}" for i in range(100)]
    env.add(*slugs, due_now=False)
    env.user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ?", (env.clock.now_ms() - 2 * 3_600_000,))
    moved = scheduler.respread_overdue(env.disc, env.clock.now_ms(), random.Random(4))
    assert moved == 100
    nexts = [r[0] - env.clock.now_ms() for r in env.db.execute("SELECT next_poll_at_ms FROM employer_poll_state")]
    assert min(nexts) >= 0 and max(nexts) <= INTERVAL_MS
    assert max(nexts) - min(nexts) > 0.8 * INTERVAL_MS
    # Due in the first minute after restart: a few, not the backlog.
    assert sum(n <= 60_000 for n in nexts) <= 15


def test_wall_clock_going_backwards_does_not_double_poll(env):
    env.add("a")
    [r] = run(env.poll_due())
    env.clock.jump_wall(-3600)
    assert scheduler.claim_due(env.disc, env.clock.now_ms())[0] == []
    env.clock.jump_wall(3600 + (r.next_poll_at_ms - env.clock.now_ms() - 3_600_000) / 1000 + 1)
    assert len(scheduler.claim_due(env.disc, env.clock.now_ms())[0]) == 1


def test_cancelled_poll_records_interrupted_and_clears_lease(env):
    [emp] = env.add("slow")
    hang = asyncio.Event()

    async def never(_request):
        await hang.wait()

    env.board.on("slow", never)

    async def go():
        [claim], _ = scheduler.claim_due(env.disc, env.clock.now_ms())
        task = asyncio.create_task(poller.poll(env.disc, env.http, claim, env.clock.now_ms, random.Random(0)))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    run(go())
    row = env.db.execute("SELECT outcome, finished_at_ms IS NOT NULL FROM source_fetches").fetchone()
    assert tuple(row) == ("INTERRUPTED", 1)
    assert state(env.db, emp)["lease_until_ms"] is None


def test_service_tick_polls_and_notifies_on_new_postings(env):
    env.add("cloudflare")
    notified = []
    svc = DiscoveryService(env.disc, env.http, clock=env.clock, rng=random.Random(5), on_events=lambda: notified.append(1))

    async def go():
        tasks = await svc.tick()
        await asyncio.gather(*tasks)
    run(go())
    assert svc.polls_done == 1 and notified == [1]
    assert env.db.execute("SELECT count(*) FROM change_events WHERE type = 'posting_new'").fetchone()[0] == 3
