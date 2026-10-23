"""The built-in company list (P1.10, ARCHITECTURE 3.3 "Company directory").

Greenhouse, Lever and Ashby only answer per company, so discovery needs to know which companies to
check. `directory.json` (built by scripts/build_directory.py from Common Crawl's URL index, every
board checked against its API) ships with the agent, and `sync` adds its companies at startup so
nobody has to paste board links. It only ever adds missing rows: a company the person stopped
watching, or added themselves, is never changed by it.

`find` and `probe` serve "add a company by name": the list first, then the three boards' public
APIs under a few spellings of the name.
"""
from __future__ import annotations

import asyncio
import functools
import json
import random
import re
import sqlite3
from importlib import resources

from jobforge_agent.discovery.adapters import list_url
from jobforge_agent.discovery.http import HttpClient
from jobforge_agent.discovery.scheduler import respread_at

# Once a day: the list is long and the per-host budgets (0002_discovery.sql) are shared with the
# companies the person added, which keep the 15 min default. 24 h is also the scheduler's maximum.
DIRECTORY_INTERVAL_S = 24 * 3600
WATCH_SETTING = "directory_watch"
MAX_NAMES_PER_REQUEST = 10
_SUFFIX = re.compile(r"[\s,]+(inc|llc|ltd|corp|corporation|co|company|plc|gmbh)\.?$", re.I)


@functools.cache
def load() -> dict:
    text = (resources.files("jobforge_agent.discovery") / "directory.json").read_text(encoding="utf-8")
    doc = json.loads(text)
    for e in doc["entries"]:
        if e["source"] not in ("greenhouse", "lever", "ashby") or not e["slug"]:
            raise ValueError(f"directory.json: bad entry {e!r}")
    return doc


def watching(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (WATCH_SETTING,)).fetchone()
    return row is None or row[0] != "0"


def sync(conn: sqlite3.Connection, now_ms: int, rng: random.Random, entries: list[dict] | None = None) -> dict:
    """Add directory companies that are not in the database yet. First polls are spread over one
    interval, so a fresh install (or a longer list in a new version) never causes a burst."""
    entries = load()["entries"] if entries is None else entries
    sources = {r[1]: r[0] for r in conn.execute("SELECT id, kind FROM sources")}
    have = {(r[0], r[1]) for r in conn.execute("SELECT source_id, slug FROM employers")}
    enabled = int(watching(conn))
    added = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for e in entries:
            sid = sources.get(e["source"])
            if sid is None or (sid, e["slug"]) in have:
                continue
            emp_id = conn.execute(
                "INSERT INTO employers (source_id, slug, display_name, enabled, poll_interval_s, origin) "
                "VALUES (?, ?, ?, ?, ?, 'directory') RETURNING id",
                (sid, e["slug"], e["name"], enabled, DIRECTORY_INTERVAL_S)).fetchone()[0]
            conn.execute("INSERT INTO employer_poll_state (employer_id, next_poll_at_ms) VALUES (?, ?)",
                         (emp_id, respread_at(now_ms, DIRECTORY_INTERVAL_S, rng)))
            have.add((sid, e["slug"]))
            added += 1
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"added": added, "listed": len(entries), "watching": bool(enabled)}


def normalize(name: str) -> str:
    n = _SUFFIX.sub("", name.strip()).lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", n)


def find(name: str, entries: list[dict] | None = None) -> list[dict]:
    """Directory entries whose name or board id matches the typed name (case, spaces, punctuation
    and legal suffixes ignored)."""
    key = normalize(name)
    if not key:
        return []
    entries = load()["entries"] if entries is None else entries
    return [e for e in entries if normalize(e["name"]) == key or normalize(e["slug"]) == key]


def slug_candidates(name: str) -> list[str]:
    base = _SUFFIX.sub("", name.strip()).lower().replace("&", "and")
    out = [re.sub(r"[^a-z0-9]", "", base), re.sub(r"[^a-z0-9]+", "-", base).strip("-")]
    return [s for i, s in enumerate(out) if s and s not in out[:i]]


async def probe(http: HttpClient, sources: list[tuple], name: str) -> tuple[list[dict], list[str]]:
    """Ask each board's public API whether it has a board for this name. `sources` rows are
    (kind, host, max_concurrency, spacing_ms) for enabled sources. Returns (found entries, problems):
    a 404 means "not on this board"; anything else is reported, never treated as "not found"."""
    async def one(kind: str, host: str, concurrency: int, spacing_ms: int) -> tuple[list[dict], list[str]]:
        for slug in slug_candidates(name):
            url = list_url(kind, slug).replace("?content=true", "")  # Greenhouse: titles only
            try:
                r = await http.get(host, url, concurrency=concurrency, spacing_s=spacing_ms / 1000)
            except Exception as exc:  # network error: say so instead of "not found"
                return [], [f"{kind}: {type(exc).__name__}"]
            if r.status == 404:
                continue
            if r.status != 200:
                return [], [f"{kind}: HTTP {r.status}"]
            try:
                data = json.loads(r.body)
            except ValueError:
                return [], [f"{kind}: answer was not JSON"]
            if (kind == "lever" and isinstance(data, list)) or (kind != "lever" and isinstance(data, dict) and "jobs" in data):
                return [{"source": kind, "slug": slug, "name": name.strip()}], []
        return [], []
    results = await asyncio.gather(*(one(*s) for s in sources))
    return [f for r in results for f in r[0]], [p for r in results for p in r[1]]
