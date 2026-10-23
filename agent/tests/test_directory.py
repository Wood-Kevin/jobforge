"""The built-in company list and adding companies by name (P1.10)."""
import asyncio
import json
import random

import httpx
import pytest

from conftest import NOW
from jobforge_agent import setup
from jobforge_agent.db.connect import open_connection
from jobforge_agent.discovery import admin, directory
from jobforge_agent.discovery.http import HostLimiter, HttpClient
from test_analysis import FakeServer, _run_with_fake
from test_ipc import AUTH

LIST = [{"source": "greenhouse", "slug": "acme", "name": "Acme", "open_jobs": 3},
        {"source": "lever", "slug": "beta-labs", "name": "Beta Labs", "open_jobs": 1},
        {"source": "ashby", "slug": "gamma", "name": "Gamma, Inc.", "open_jobs": 2}]


def _rows(conn):
    return {r[0]: r[1:] for r in conn.execute("SELECT slug, enabled, origin, poll_interval_s FROM employers")}


def test_sync_adds_missing_rows_only_and_never_changes_existing(db, paths):
    disc = open_connection(paths.db, "discovery")  # the component that runs it at startup
    r = directory.sync(disc, NOW, random.Random(1), LIST)
    assert r == {"added": 3, "listed": 3, "watching": True}
    assert _rows(db) == {"acme": (1, "directory", 86400), "beta-labs": (1, "directory", 86400), "gamma": (1, "directory", 86400)}
    # First polls are spread over one interval, never all due at once.
    due = [r[0] for r in db.execute("SELECT next_poll_at_ms FROM employer_poll_state")]
    assert all(NOW <= d <= NOW + 86_400_000 for d in due) and len(set(due)) == 3
    # The person stops watching one and adds another themselves; a later sync touches neither.
    db.execute("UPDATE employers SET enabled = 0 WHERE slug = 'acme'")
    db.execute("UPDATE employers SET origin = 'user', poll_interval_s = NULL WHERE slug = 'gamma'")
    longer = LIST + [{"source": "greenhouse", "slug": "delta", "name": "Delta", "open_jobs": 1}]
    assert directory.sync(disc, NOW, random.Random(1), longer)["added"] == 1
    rows = _rows(db)
    assert rows["acme"] == (0, "directory", 86400) and rows["gamma"] == (1, "user", None) and rows["delta"][0] == 1
    disc.close()


def test_built_in_switch_moves_only_directory_rows(db, paths):
    disc, user = open_connection(paths.db, "discovery"), open_connection(paths.db, "user")
    directory.sync(disc, NOW, random.Random(1), LIST[:2])
    admin.import_employers(user, [{"source": "ashby", "slug": "mine", "name": "Mine"}], NOW, random.Random(1))
    assert admin.set_built_in_watch(user, False, NOW) == 2
    assert {k: v[0] for k, v in _rows(db).items()} == {"acme": 0, "beta-labs": 0, "mine": 1}
    # While off, companies new to the list arrive unwatched.
    directory.sync(disc, NOW, random.Random(1), LIST)
    assert _rows(db)["gamma"][0] == 0
    st = admin.status(db, NOW)
    assert [e["slug"] for e in st["employers"]] == ["mine"]  # the list is counted, not listed
    assert st["built_in"]["watching"] is False and st["built_in"]["total"] == 3 and st["built_in"]["enabled"] == 0
    assert admin.set_built_in_watch(user, True, NOW) == 3
    assert directory.watching(db) is True
    disc.close()
    user.close()


def test_naming_a_listed_company_watches_it_and_makes_it_yours(db, paths):
    disc, user = open_connection(paths.db, "discovery"), open_connection(paths.db, "user")
    directory.sync(disc, NOW, random.Random(1), LIST)
    admin.set_built_in_watch(user, False, NOW)
    r = admin.import_employers(user, [{"source": "greenhouse", "slug": "acme", "name": "Acme"},
                                      {"source": "lever", "slug": "beta-labs", "name": "Beta Labs"}], NOW, random.Random(1))
    assert (r["added"], r["existing"], r["watched_again"]) == (0, 0, 2)
    assert _rows(db)["acme"][:2] == (1, "user")
    admin.set_built_in_watch(user, True, NOW)
    admin.set_built_in_watch(user, False, NOW)
    assert _rows(db)["acme"][0] == 1  # the switch no longer touches it
    disc.close()
    user.close()


def test_a_named_list_company_polls_like_yours(db, paths):
    """Found live in P1.10: Stripe, named while on the list, kept the list's 24 h interval and a
    first poll hours away."""
    disc, user = open_connection(paths.db, "discovery"), open_connection(paths.db, "user")
    directory.sync(disc, NOW, random.Random(1), LIST)
    db.execute("UPDATE employer_poll_state SET next_poll_at_ms = ? WHERE employer_id = "
               "(SELECT id FROM employers WHERE slug = 'acme')", (NOW + 20 * 3600 * 1000,))
    r = admin.import_employers(user, [{"source": "greenhouse", "slug": "acme", "name": "Acme"}], NOW, random.Random(1))
    assert (r["existing"], r["watched_again"]) == (1, 0)
    interval, default, next_at = db.execute(
        "SELECT e.poll_interval_s, s.default_interval_s, p.next_poll_at_ms FROM employers e JOIN sources s ON s.id = e.source_id "
        "JOIN employer_poll_state p ON p.employer_id = e.id WHERE e.slug = 'acme'").fetchone()
    assert interval is None
    assert NOW <= next_at <= NOW + default * 1000
    disc.close()
    user.close()


def test_names_match_the_list_loosely():
    assert [e["slug"] for e in directory.find("gamma inc", LIST)] == ["gamma"]
    assert [e["slug"] for e in directory.find("  BETA-LABS ", LIST)] == ["beta-labs"]
    assert [e["slug"] for e in directory.find("Acme Corp.", LIST)] == ["acme"]
    assert directory.find("Acmes", LIST) == [] and directory.find("!!", LIST) == []
    assert directory.slug_candidates("Red Hat, Inc.") == ["redhat", "red-hat"]
    assert directory.slug_candidates("Plaid") == ["plaid"]


def test_company_lines_split_links_names_and_other_links():
    links, names, bad = setup.parse_company_lines(
        "Stripe\nhttps://jobs.lever.co/plaid\nstripe.com\nDatadog\nhttps://x.wd5.myworkdayjobs.com/en-US/jobs\n---\n")
    assert [(e["source"], e["slug"]) for e in links] == [("lever", "plaid")]
    assert names == ["Stripe", "stripe", "Datadog"]
    assert bad == ["https://x.wd5.myworkdayjobs.com/en-US/jobs", "---"]


SOURCES = [("greenhouse", "boards-api.greenhouse.io", 1, 0), ("lever", "api.lever.co", 1, 0), ("ashby", "api.ashbyhq.com", 1, 0)]


def _http(handler) -> HttpClient:
    async def no_sleep(_s):
        return None
    return HttpClient(transport=httpx.MockTransport(handler), limiter=HostLimiter(sleep=no_sleep))


def test_probe_finds_boards_and_reports_failures_instead_of_not_found():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.host == "api.lever.co" and req.url.path.endswith("/red-hat"):
            return httpx.Response(200, json=[{"id": "1"}])
        if req.url.host == "api.ashbyhq.com":
            return httpx.Response(503)
        return httpx.Response(404, json={"error": "not found"})

    async def go():
        http = _http(handler)
        try:
            return await directory.probe(http, SOURCES, "Red Hat")
        finally:
            await http.aclose()
    found, problems = asyncio.run(go())
    assert found == [{"source": "lever", "slug": "red-hat", "name": "Red Hat"}]
    assert problems == ["ashby: HTTP 503"]
    gh = [u for u in seen if "greenhouse" in u]
    assert gh == ["https://boards-api.greenhouse.io/v1/boards/redhat/jobs", "https://boards-api.greenhouse.io/v1/boards/red-hat/jobs"]


def test_startup_adds_the_list_and_names_are_added_through_ipc(paths, monkeypatch):
    monkeypatch.delenv("JOBFORGE_BUILT_IN_LIST")
    monkeypatch.setattr(directory, "load", lambda: {"version": "test", "entries": LIST})

    async def check(client, runtime):
        st = (await client.get("/v1/discovery/status", headers=AUTH)).json()
        assert st["built_in"]["total"] == 3 and st["built_in"]["enabled"] == 3 and st["employers"] == []
        s = (await client.get("/v1/setup/status", headers=AUTH)).json()
        assert s["employers"] == {"done": True, "enabled": 3}  # step 1 needs nothing from the person
        r = (await client.post("/v1/employers/add", headers=AUTH, json={"text": "Gamma Inc"})).json()
        assert (r["added"], r["existing"], r["not_found"]) == (0, 1, [])
        assert [e["slug"] for e in (await client.get("/v1/discovery/status", headers=AUTH)).json()["employers"]] == ["gamma"]
        r = await client.post("/v1/employers/built-in", headers=AUTH, json={"watch": False})
        assert r.json() == {"watching": False, "changed": 2}
        assert (await client.post("/v1/employers/built-in", headers=AUTH, json={"watch": "no"})).status_code == 422
        assert (await client.get("/v1/health", headers=AUTH)).json()["directory_error"] is None

    _run_with_fake(paths, FakeServer(), check)


def test_a_broken_list_is_shown_not_fatal(paths, monkeypatch):
    monkeypatch.delenv("JOBFORGE_BUILT_IN_LIST")

    def broken():
        raise ValueError("directory.json: bad entry")
    monkeypatch.setattr(directory, "load", broken)

    async def check(client, runtime):
        h = (await client.get("/v1/health", headers=AUTH)).json()
        assert h["directory_error"] == "ValueError: directory.json: bad entry"

    _run_with_fake(paths, FakeServer(), check)


def test_shipped_list_is_valid():
    """The real directory.json: loads, no duplicate boards, every entry had open jobs when built."""
    directory.load.cache_clear()
    doc = directory.load()
    keys = [(e["source"], e["slug"]) for e in doc["entries"]]
    assert len(keys) == len(set(keys)) and len(keys) > 100
    assert all(e["open_jobs"] >= 1 and e["name"].strip() for e in doc["entries"])
    assert doc["version"] and doc["crawls"]
