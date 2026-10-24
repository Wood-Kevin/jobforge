"""The only code that talks to job boards (ARCHITECTURE 3.3, "Per-host budget").

Ported from ats_scanner/http.py (edd1528): identifying User-Agent and RFC 9309 robots
semantics. Changed for continuous polling:
- no retries inside a request: a failure becomes an outcome, and the scheduler reschedules
  with backoff, so every attempt is a visible source_fetches row;
- per-host concurrency and minimum spacing (plus jitter and the robots Crawl-delay) across
  all polls of that host, not per run;
- conditional requests (If-None-Match / If-Modified-Since): an unchanged board answers 304
  with an empty body (spike P1.0c);
- bytes on the wire are counted for the measured-volume evidence.
"""
from __future__ import annotations

import asyncio
import email.utils
import random
import time
from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

import httpx

from jobforge_agent import __version__

USER_AGENT = f"JobForge/{__version__} (personal job search; self-hosted; reads public job board APIs)"
REQUEST_TIMEOUT_S = 60.0  # per step (connect, each read)
# Whole request, redirects and body included: a server trickling bytes would otherwise hold the
# host's only slot forever (per-step timeouts never fire).
REQUEST_TOTAL_TIMEOUT_S = 300.0
# Decoded body cap. The largest boards with content=true are tens of MB; a gzip bomb is not.
MAX_BODY_BYTES = 128 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_RETRY_AFTER_S = 6 * 3600


class ResponseRejected(httpx.HTTPError):
    """An answer the client refuses to take (too large, a redirect off the board's host). An
    HTTPError, so the poll records it as a network failure with the reason."""


@dataclass
class Response:
    status: int
    body: bytes
    etag: str | None
    last_modified: str | None
    retry_after_s: int | None
    wire_bytes: int
    duration_ms: int


class RobotsRules:
    """robots.txt outcome for one host, per RFC 9309: 2xx -> obey rules; 4xx (incl. 401/403)
    -> no rules; 5xx or unreachable -> treat as fully disallowed."""

    def __init__(self, http_status: int, body: str | None):
        self.http_status = http_status
        self._parser: RobotFileParser | None = None
        if 200 <= http_status < 300:
            p = RobotFileParser()
            p.parse((body or "").splitlines())
            p.modified()  # can_fetch() returns False until the parser is marked fresh
            self._parser = p
        self._allow_all = 400 <= http_status < 500

    def allows(self, user_agent: str, url: str) -> bool:
        if self._parser is not None:
            return self._parser.can_fetch(user_agent, url)
        return self._allow_all

    def crawl_delay_s(self, user_agent: str) -> float | None:
        if self._parser is None:
            return None
        d = self._parser.crawl_delay(user_agent)
        return float(d) if d is not None else None


def parse_retry_after(value: str | None, now_s: float) -> int | None:
    """Seconds form or HTTP-date form; capped so one odd header cannot park a host for days."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return min(int(value), MAX_RETRY_AFTER_S)
    try:
        when = email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        return None
    return max(0, min(int(when - now_s), MAX_RETRY_AFTER_S))


class HostLimiter:
    """Per-host concurrency and spacing, shared by every poll of that host."""

    def __init__(self, sleep=asyncio.sleep, monotonic=time.monotonic, rng: random.Random | None = None):
        self._sleep = sleep
        self._mono = monotonic
        self._rng = rng or random.Random()
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._last: dict[str, float] = {}

    def _sem(self, host: str, concurrency: int) -> asyncio.Semaphore:
        if host not in self._sems:
            self._sems[host] = asyncio.Semaphore(max(1, concurrency))
        return self._sems[host]

    async def run(self, host: str, concurrency: int, spacing_s: float, coro_fn):
        async with self._sem(host, concurrency):
            # Spacing plus up to 1 s of jitter, so polls of one host never line up.
            gap = spacing_s + self._rng.uniform(0, 1.0)
            wait = self._last.get(host, -1e9) + gap - self._mono()
            if wait > 0:
                await self._sleep(wait)
            try:
                return await coro_fn()
            finally:
                self._last[host] = self._mono()


class HttpClient:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, limiter: HostLimiter | None = None):
        # Redirects are followed by hand (below), only on the same host over https: the board APIs
        # are fixed hosts, and a redirect elsewhere (including 127.0.0.1, the agent's own IPC port)
        # is never something a poll should fetch.
        self._client = httpx.AsyncClient(headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
                                         timeout=REQUEST_TIMEOUT_S, follow_redirects=False, transport=transport)
        self.limiter = limiter or HostLimiter()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, host: str, url: str, *, concurrency: int, spacing_s: float,
                  headers: dict | None = None) -> Response:
        async def fetch() -> Response:
            t0 = time.perf_counter()
            target, wire = httpx.URL(url), 0
            for _ in range(MAX_REDIRECTS + 1):
                async with self._client.stream("GET", target, headers=headers or {}) as r:
                    if r.is_redirect and r.headers.get("location"):
                        wire += r.num_bytes_downloaded
                        nxt = target.join(r.headers["location"])
                        if nxt.scheme != "https" or nxt.host != target.host:
                            raise ResponseRejected(f"redirect off {target.host} to {nxt.scheme}://{nxt.host}")
                        target = nxt
                        continue
                    chunks, size = [], 0
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_BODY_BYTES:
                            raise ResponseRejected(f"body over {MAX_BODY_BYTES} bytes")
                        chunks.append(chunk)
                    wire += r.num_bytes_downloaded
                return Response(
                    status=r.status_code, body=b"".join(chunks), etag=r.headers.get("etag"),
                    last_modified=r.headers.get("last-modified"),
                    retry_after_s=parse_retry_after(r.headers.get("retry-after"), time.time()),
                    wire_bytes=wire, duration_ms=int((time.perf_counter() - t0) * 1000),
                )
            raise ResponseRejected(f"more than {MAX_REDIRECTS} redirects")

        async def go() -> Response:
            try:
                return await asyncio.wait_for(fetch(), REQUEST_TOTAL_TIMEOUT_S)
            except TimeoutError:
                raise httpx.ReadTimeout(f"no complete answer within {REQUEST_TOTAL_TIMEOUT_S:.0f} s") from None
        return await self.limiter.run(host, concurrency, spacing_s, go)
