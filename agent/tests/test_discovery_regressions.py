"""Regressions from the 2026-10-24 audit (discovery). Each test fails on the code before the fix."""
from __future__ import annotations

import asyncio
import json
import random

import httpx
import pytest

from jobforge_agent.discovery import adapters, admin, directory, poller, scheduler
from jobforge_agent.discovery import http as http_mod
from jobforge_agent.discovery.http import HostLimiter, HttpClient
from jobforge_agent.discovery.models import Employer
from test_discovery_scheduler import GH_BODY, env, run, state  # noqa: F401 (env is a fixture)

GH = json.loads(GH_BODY)


def _due(env):
    env.user.execute("UPDATE employer_poll_state SET next_poll_at_ms = ?", (env.clock.now_ms(),))


def _body(jobs):
    return json.dumps({"jobs": jobs, "meta": {"total": len(jobs)}}).encode()


# ----- robots.txt -----

def test_unreachable_robots_retries_in_minutes_not_a_day(env):
    """A network error on robots.txt parked every board of that host for 24 h."""
    [emp] = env.add("a")

    async def unreachable(request):
        raise httpx.ConnectError("dns not ready")
    env.board.robots["default"] = unreachable
    [r] = run(env.poll_due())
    assert r.record.outcome == "SKIPPED"
    status, expires = env.db.execute("SELECT http_status, expires_at_ms FROM robots_cache").fetchone()
    assert status == 599
    assert expires - env.clock.now_ms() <= 15 * 60 * 1000
    assert r.next_poll_at_ms == expires


def test_robots_5xx_retries_soon_and_a_good_answer_keeps_a_day(env):
    env.add("a")
    env.board.robots["default"] = (503, "busy")
    run(env.poll_due())
    assert env.db.execute("SELECT expires_at_ms FROM robots_cache").fetchone()[0] - env.clock.now_ms() <= 15 * 60 * 1000
    env.clock.advance(15 * 60 * 1000 + 1)
    env.board.robots["default"] = (200, "User-agent: *\nAllow: /\n")
    _due(env)
    [r] = run(env.poll_due())
    assert r.record.outcome == "OK"
    assert env.db.execute("SELECT expires_at_ms FROM robots_cache").fetchone()[0] - env.clock.now_ms() == poller.ROBOTS_TTL_MS


def test_robots_crawl_delay_spaces_the_board_request(env):
    """The parsed Crawl-delay was never used."""
    env.add("a")
    env.board.robots["default"] = (200, "User-agent: *\nCrawl-delay: 30\nAllow: /\n")
    run(env.poll_due())
    assert any(s >= 30 for s in env.sleeps), env.sleeps


# ----- a poll always finishes -----

@pytest.mark.parametrize("bad_job", [
    {"id": 1, "title": "T", "location": "Remote", "absolute_url": "u", "content": ""},  # location as a string
    "not an object",
])
def test_an_unexpected_posting_shape_finishes_the_poll_and_retries(env, bad_job):
    """AttributeError escaped poll(); the unfinished fetch row blocked the employer until restart."""
    [emp] = env.add("odd")
    env.board.on("odd", (200, _body([bad_job]), {}))
    [r] = run(env.poll_due())
    assert r.record.outcome == "ERROR" and r.record.error.startswith("internal: ")
    assert env.db.execute("SELECT count(*) FROM source_fetches WHERE finished_at_ms IS NULL").fetchone()[0] == 0
    assert state(env.db, emp)["last_error"].startswith("internal: ")
    env.clock.advance(7 * 3600 * 1000)  # past any backoff
    claims, _ = scheduler.claim_due(env.disc, env.clock.now_ms())
    assert [c.employer_id for c in claims] == [emp]


def test_a_collision_forgets_the_etag_so_the_error_stays_visible(env):
    """The ETag from the failed poll turned the next poll into a 304 'success'."""
    [emp] = env.add("dup")
    job = GH["jobs"][0]
    twin = dict(job, title=job["title"] + " (other)")
    env.board.on("dup", (200, _body([job, twin]), {"ETag": 'W/"c1"'}), (304, b"", {"ETag": 'W/"c1"'}))
    [r1] = run(env.poll_due())
    assert r1.record.outcome == "ERROR" and r1.record.error.startswith("collision")
    assert state(env.db, emp)["etag"] is None
    env.clock.advance(7 * 3600 * 1000)
    _due(env)
    run(env.poll_due())
    req = env.board.board_requests()[-1]
    assert "if-none-match" not in req.headers


# ----- closing jobs -----

def test_a_job_removed_from_a_board_closes_while_the_board_answers_304(env):
    [emp] = env.add("b")
    jobs = GH["jobs"]
    env.board.on("b", (200, _body(jobs), {"ETag": 'W/"1"'}), (200, _body(jobs[:-1]), {"ETag": 'W/"2"'}),
                 (304, b"", {"ETag": 'W/"2"'}))
    gone = f"greenhouse:b:{jobs[-1]['id']}"
    seen = []
    for _ in range(3):
        run(env.poll_due())
        seen.append(env.db.execute("SELECT last_seen_at_ms FROM jobs WHERE source_job_id = ?", (gone,)).fetchone()[0])
        env.clock.advance(1000)
        _due(env)
    missing, closed = env.db.execute("SELECT missing_polls, closed_at_ms FROM jobs WHERE source_job_id = ?", (gone,)).fetchone()
    assert (missing, closed is not None) == (2, True)
    assert env.db.execute("SELECT count(*) FROM jobs WHERE closed_at_ms IS NULL").fetchone()[0] == len(jobs) - 1
    assert seen[2] == seen[0]  # the 304 did not mark the missing job as seen


def test_a_dead_board_closes_its_open_jobs(env):
    [emp] = env.add("gone")
    env.board.on("gone", (200, GH_BODY, {}), (404, b"", {}))
    run(env.poll_due())
    assert env.db.execute("SELECT count(*) FROM jobs WHERE closed_at_ms IS NULL").fetchone()[0] > 0
    for _ in range(3):
        env.clock.advance(7 * 3600 * 1000)
        _due(env)
        run(env.poll_due())
    assert env.db.execute("SELECT status FROM employers WHERE id = ?", (emp,)).fetchone()[0] == "dead"
    assert env.db.execute("SELECT count(*) FROM jobs WHERE closed_at_ms IS NULL").fetchone()[0] == 0


# ----- budget -----

def test_claimed_but_unfinished_polls_count_against_the_hourly_budget(env):
    """requests_made is written at finish, so queued claims ran past the budget."""
    env.add(*[f"e{i}" for i in range(10)])
    env.user.execute("UPDATE sources SET max_requests_per_hour = 3 WHERE kind = 'greenhouse'")
    claimed = []
    for _ in range(4):
        claims, _ = scheduler.claim_due(env.disc, env.clock.now_ms())
        claimed += claims
    assert len(claimed) == 3


# ----- salary -----

def _lever_job(interval):
    return {"id": "x", "text": "T", "categories": {}, "hostedUrl": "u", "applyUrl": "u",
            "salaryRange": {"min": 500, "max": 650, "currency": "USD", "interval": interval}}


def test_an_unknown_pay_interval_is_salary_unknown_not_a_year():
    """$650 per day was read as $650 a year and rejected below the floor."""
    e = Employer(1, "lever", "acme", "Acme")
    assert adapters.parse_listing(e, [_lever_job("per-day-wage")])[0].salary is None
    assert adapters.parse_listing(e, [_lever_job("per-hour-wage")])[0].salary.period == "HOUR"
    assert adapters.parse_listing(e, [_lever_job("per-year-salary")])[0].salary.period == "YEAR"
    a = Employer(2, "ashby", "acme", "Acme")
    comp = {"summaryComponents": [{"compensationType": "Salary", "currencyCode": "USD", "minValue": 500,
                                   "maxValue": 650, "interval": "1 DAY"}]}
    job = {"id": "1", "title": "T", "jobUrl": "u", "compensation": comp}
    assert adapters.parse_listing(a, {"jobs": [job]})[0].salary is None


# ----- naming companies -----

def test_naming_a_blocked_company_again_polls_it_again(db, paths):
    from jobforge_agent.db.connect import open_connection
    user = open_connection(paths.db, "user")
    admin.import_employers(user, [{"source": "greenhouse", "slug": "acme"}], 0, random.Random(1))
    db.execute("UPDATE employers SET status = 'blocked' WHERE slug = 'acme'")
    result = admin.import_employers(user, [{"source": "greenhouse", "slug": "acme"}], 0, random.Random(1))
    assert result["watched_again"] == 1
    assert db.execute("SELECT status FROM employers WHERE slug = 'acme'").fetchone()[0] == "active"
    user.close()


@pytest.mark.parametrize("slug", ["ac\tme", "../../evil", "a/b", "a?x=1", "..", "a b"])
def test_slugs_that_would_change_the_api_path_are_refused(db, paths, slug):
    from jobforge_agent.db.connect import open_connection
    user = open_connection(paths.db, "user")
    result = admin.import_employers(user, [{"source": "lever", "slug": slug}], 0, random.Random(1))
    assert result["added"] == 0 and result["skipped_unsupported"]
    user.close()


def test_every_built_in_slug_passes_the_slug_check():
    bad = [e["slug"] for e in directory.load()["entries"] if not admin._SLUG.fullmatch(e["slug"]) or ".." in e["slug"]]
    assert bad == []


def test_a_200_the_lookup_cannot_read_is_check_failed_not_not_found():
    def handler(req):
        return httpx.Response(200, json={"unexpected": True})

    async def go():
        http = HttpClient(transport=httpx.MockTransport(handler), limiter=HostLimiter(sleep=_nosleep))
        try:
            return await directory.probe(http, [("greenhouse", "boards-api.greenhouse.io", 1, 0)], "Acme")
        finally:
            await http.aclose()
    found, problems = asyncio.run(go())
    assert found == [] and problems == ["greenhouse: unexpected answer"]


# ----- the HTTP client -----

async def _nosleep(_s):
    return None


def _client(handler):
    return HttpClient(transport=httpx.MockTransport(handler), limiter=HostLimiter(sleep=_nosleep))


def _get(handler, url="https://api.lever.co/v0/postings/acme?mode=json"):
    async def go():
        http = _client(handler)
        try:
            return await http.get("api.lever.co", url, concurrency=1, spacing_s=0)
        finally:
            await http.aclose()
    return asyncio.run(go())


@pytest.mark.parametrize("location", ["http://127.0.0.1:8123/v1/profile", "https://evil.example/x",
                                      "http://api.lever.co/v0/postings/acme"])
def test_redirects_off_the_board_host_are_refused(location):
    def handler(req):
        if req.url.host == "api.lever.co" and req.url.scheme == "https":
            return httpx.Response(302, headers={"Location": location})
        raise AssertionError(f"followed a redirect to {req.url}")
    with pytest.raises(http_mod.ResponseRejected, match="redirect off"):
        _get(handler)


def test_a_same_host_redirect_is_followed():
    def handler(req):
        if req.url.path.endswith("/old"):
            return httpx.Response(301, headers={"Location": "/v0/postings/new"})
        return httpx.Response(200, json=[])
    r = _get(handler, "https://api.lever.co/v0/postings/old")
    assert (r.status, r.body) == (200, b"[]")


def test_an_oversized_body_is_refused(monkeypatch):
    assert http_mod.MAX_BODY_BYTES >= 64 * 1024 * 1024  # real boards must still fit
    monkeypatch.setattr(http_mod, "MAX_BODY_BYTES", 1000)
    with pytest.raises(http_mod.ResponseRejected, match="body over"):
        _get(lambda req: httpx.Response(200, content=b"x" * 5000))


def test_a_request_has_a_total_time_limit(monkeypatch):
    monkeypatch.setattr(http_mod, "REQUEST_TOTAL_TIMEOUT_S", 0.05, raising=True)

    async def slow(req):
        await asyncio.sleep(5)
        return httpx.Response(200)
    with pytest.raises(httpx.TimeoutException):
        _get(slow)
