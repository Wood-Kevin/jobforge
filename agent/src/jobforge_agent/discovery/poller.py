"""One employer poll (ARCHITECTURE 3.3).

claim (scheduler, own transaction) -> robots -> conditional GET (no transaction open) ->
apply changes + finish + reschedule (one transaction). No transaction ever spans the
network await (ARCHITECTURE 3.4).
"""
from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from jobforge_agent.discovery import adapters, changes, scheduler
from jobforge_agent.discovery.http import USER_AGENT, HttpClient, RobotsRules
from jobforge_agent.discovery.models import Employer
from jobforge_agent.discovery.scheduler import Claim, FetchRecord
from jobforge_agent.filtering import pipeline

ROBOTS_TTL_MS = 24 * 3600 * 1000


@dataclass
class PollResult:
    record: FetchRecord
    next_poll_at_ms: int
    events: int


async def _robots(conn: sqlite3.Connection, http: HttpClient, claim: Claim, now_ms: int, rec: FetchRecord) -> tuple[RobotsRules, int]:
    row = conn.execute("SELECT http_status, body, expires_at_ms FROM robots_cache WHERE host = ?", (claim.host,)).fetchone()
    if row and row[2] > now_ms:
        return RobotsRules(row[0], row[1]), row[2]
    try:
        r = await http.get(claim.host, f"https://{claim.host}/robots.txt", concurrency=claim.concurrency, spacing_s=claim.spacing_s)
        status, body = r.status, r.body.decode("utf-8", "replace")
        rec.bytes_received += r.wire_bytes
    except httpx.HTTPError:
        status, body = 599, None  # unreachable: RFC 9309 says assume disallowed
    rec.requests_made += 1
    expires = now_ms + ROBOTS_TTL_MS
    conn.execute(
        "INSERT INTO robots_cache (host, fetched_at_ms, expires_at_ms, http_status, body) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(host) DO UPDATE SET fetched_at_ms = excluded.fetched_at_ms, expires_at_ms = excluded.expires_at_ms, "
        "http_status = excluded.http_status, body = excluded.body", (claim.host, now_ms, expires, status, body))
    return RobotsRules(status, body), expires


async def poll(conn: sqlite3.Connection, http: HttpClient, claim: Claim, now_fn, rng: random.Random) -> PollResult:
    rec = FetchRecord(outcome="ERROR")
    employer = Employer(claim.employer_id, claim.kind, claim.slug, claim.name)
    url = adapters.list_url(claim.kind, claim.slug)
    counts = None
    raws = None
    try:
        rules, robots_expiry = await _robots(conn, http, claim, now_fn(), rec)
        if not rules.allows(USER_AGENT, url):
            rec.outcome = "SKIPPED"
            rec.error = f"robots.txt disallows {urlsplit(url).path} (robots status {rules.http_status})"
            rec.next_poll_override_ms = robots_expiry
        else:
            headers = {}
            if claim.conditional == "etag" and claim.etag:
                headers["If-None-Match"] = claim.etag
            if claim.last_modified:
                headers["If-Modified-Since"] = claim.last_modified
            r = await http.get(claim.host, url, concurrency=claim.concurrency, spacing_s=claim.spacing_s, headers=headers)
            rec.requests_made += 1
            rec.bytes_received += r.wire_bytes
            rec.duration_ms = r.duration_ms
            rec.http_status = r.status
            rec.retry_after_s = r.retry_after_s
            if r.status == 304:
                rec.outcome = "NOT_MODIFIED"
            elif 200 <= r.status < 300:
                try:
                    raws = adapters.parse_listing(employer, json.loads(r.body))
                except (ValueError, adapters.ParseError, KeyError) as exc:
                    rec.error = f"parse: {type(exc).__name__}: {exc}"
                else:
                    rec.outcome = "OK" if raws else "EMPTY"
                    rec.complete = True
                    rec.postings_seen = len(raws)
                    rec.etag, rec.last_modified = r.etag, r.last_modified
            else:
                rec.error = f"HTTP {r.status}"
    except asyncio.CancelledError:
        rec.outcome = "INTERRUPTED"
        rec.error = "agent stopping"
        _close(conn, claim, rec, now_fn(), rng, None, employer)
        raise
    except httpx.HTTPError as exc:
        rec.error = f"network: {type(exc).__name__}: {exc}"[:500]
    return _close(conn, claim, rec, now_fn(), rng, raws, employer)


def _close(conn, claim, rec: FetchRecord, now: int, rng, raws, employer) -> PollResult:
    events = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        if rec.outcome in ("OK", "EMPTY") and raws is not None:
            try:
                counts = changes.apply_listing(conn, employer, raws, claim.fetch_id, now)
            except changes.CollisionError as exc:
                # Roll back the half-applied listing; record the poll as an error instead.
                conn.execute("ROLLBACK")
                conn.execute("BEGIN IMMEDIATE")
                rec.outcome, rec.complete, rec.error = "ERROR", False, f"collision: {exc}"
            else:
                rec.new_count, rec.changed_count, rec.closed_count = counts.new, counts.changed, counts.closed
                events = counts.events
                # Filter new/changed postings in the same transaction: a posting is never stored
                # without its filter verdict once a search profile exists.
                stats = pipeline.filter_jobs(conn, counts.touched, now)
                if stats is not None:
                    events += stats.queued
        elif rec.outcome == "NOT_MODIFIED":
            changes.touch_unchanged_board(conn, claim.employer_id, now)
        next_at = scheduler.finish(conn, claim, rec, now, rng)
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return PollResult(rec, next_at, events)
