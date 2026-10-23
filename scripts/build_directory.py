"""Build JobForge's built-in company directory (P1.10): discovery/directory.json.

Maintainer tool, run from agent/:  uv run python ../scripts/build_directory.py [--crawls 4]

Where the companies come from: Common Crawl's public URL index lists pages it crawled under
boards.greenhouse.io, job-boards.greenhouse.io, jobs.lever.co and jobs.ashbyhq.com. The first path
segment of those URLs is the company's board id. Nothing is guessed: every candidate is checked
against its board's public API (the same endpoints the agent polls) and kept only when the board
answers with at least one open job. Requests are sequential per host and spaced like the agent's
own polling, under the agent's User-Agent.

Output is public company data (board ids and names), never anything personal.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from datetime import date
from pathlib import Path

import httpx

from jobforge_agent.discovery.adapters import list_url
from jobforge_agent.discovery.http import USER_AGENT

CC_COLLINFO = "https://index.commoncrawl.org/collinfo.json"
PREFIXES = [("greenhouse", "boards.greenhouse.io"), ("greenhouse", "job-boards.greenhouse.io"),
            ("lever", "jobs.lever.co"), ("ashby", "jobs.ashbyhq.com")]
SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
NOT_SLUGS = {"embed", "api", "robots.txt", "favicon.ico", "sitemap.xml", "jobs", "v1", "static", "assets", "oauth", "careers"}
SPACING_S = {"greenhouse": 1.2, "lever": 1.2, "ashby": 1.2}  # Lever robots.txt asks for Crawl-delay: 1


def cc_get(client: httpx.Client, url: str, tries: int = 6) -> str | None:
    """The index server answers 502/504 under load; retry with backoff, then give up loudly."""
    for i in range(tries):
        try:
            r = client.get(url, timeout=180)
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:  # "No Captures found" for this prefix in this crawl
                return ""
            print(f"  cc {r.status_code} (try {i + 1}) {url}", file=sys.stderr)
        except httpx.HTTPError as exc:
            print(f"  cc {type(exc).__name__} (try {i + 1}) {url}", file=sys.stderr)
        time.sleep(10 * (i + 1))
    print(f"  cc GAVE UP {url}", file=sys.stderr)
    return None


def candidates(client: httpx.Client, crawls: list[str], cache: Path) -> tuple[dict[tuple[str, str], int], list[str]]:
    """(kind, slug) -> number of crawled URLs seen; plus the index queries that failed."""
    seen: dict[tuple[str, str], int] = {}
    failed = []
    for crawl in crawls:
        for kind, host in PREFIXES:
            base = f"https://index.commoncrawl.org/{crawl}-index?url={host}/*&output=json&fl=url,status"
            mf = cache / f"{crawl}-{host}-pages.json"
            meta = mf.read_text(encoding="utf-8") if mf.exists() else cc_get(client, base + "&showNumPages=true")
            if not meta:
                failed.append(f"{crawl} {host} (page count)")
                continue
            mf.write_text(meta, encoding="utf-8")
            pages = json.loads(meta).get("pages", 0)
            for p in range(pages):
                f = cache / f"{crawl}-{host}-{p}.jsonl"
                text = f.read_text(encoding="utf-8") if f.exists() else cc_get(client, f"{base}&page={p}")
                if text is None:
                    failed.append(f"{crawl} {host} page {p}")
                    continue
                f.write_text(text, encoding="utf-8")
                for line in text.splitlines():
                    try:
                        url = json.loads(line)["url"]
                    except (ValueError, KeyError):
                        continue
                    m = re.match(r"https?://[^/]+/(?:embed/job_board\?for=)?([^/?#&]+)", url)
                    if not m:
                        continue
                    slug = m.group(1).lower()
                    if SLUG.match(slug) and slug not in NOT_SLUGS:
                        seen[(kind, slug)] = seen.get((kind, slug), 0) + 1
            print(f"{crawl} {host}: {pages} pages, {len(seen)} candidates so far", file=sys.stderr)
    return seen, failed


def pretty(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


def verify(kind: str, slugs: list[str], out: list[dict], errors: dict[str, int]) -> None:
    client = httpx.Client(headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}, timeout=30, follow_redirects=False)
    for n, slug in enumerate(slugs, 1):
        time.sleep(SPACING_S[kind])
        url = list_url(kind, slug).replace("?content=true", "")  # Greenhouse: titles only, much smaller
        try:
            r = client.get(url)
        except httpx.HTTPError as exc:
            errors[type(exc).__name__] = errors.get(type(exc).__name__, 0) + 1
            continue
        if r.status_code != 200:
            errors[str(r.status_code)] = errors.get(str(r.status_code), 0) + 1
            continue
        try:
            data = r.json()
        except ValueError:
            errors["not json"] = errors.get("not json", 0) + 1
            continue
        jobs = data if kind == "lever" else data.get("jobs") if isinstance(data, dict) else None
        if not isinstance(jobs, list) or not jobs:
            errors["no open jobs"] = errors.get("no open jobs", 0) + 1
            continue
        name = pretty(slug)
        if kind == "greenhouse" and isinstance(jobs[0], dict) and str(jobs[0].get("company_name") or "").strip():
            name = str(jobs[0]["company_name"]).strip()  # Greenhouse job objects carry the company name
        out.append({"source": kind, "slug": slug, "name": name, "open_jobs": len(jobs)})
        if n % 100 == 0:
            print(f"  {kind}: {n}/{len(slugs)} checked, {sum(1 for e in out if e['source'] == kind)} kept", file=sys.stderr)
    client.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--crawls", type=int, default=4, help="how many recent Common Crawl indexes to read")
    ap.add_argument("--cache", type=Path, required=True, help="folder for downloaded index pages (re-runs reuse them)")
    ap.add_argument("--out", type=Path, default=Path("src/jobforge_agent/discovery/directory.json"))
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    with httpx.Client(headers={"User-Agent": USER_AGENT}) as client:
        crawls = [c["id"] for c in json.loads(cc_get(client, CC_COLLINFO) or "[]")[: args.crawls]]
        if not crawls:
            print("could not read the Common Crawl index list", file=sys.stderr)
            return 1
        seen, failed = candidates(client, crawls, args.cache)
    by_kind: dict[str, list[str]] = {}
    for kind, slug in sorted(seen):
        by_kind.setdefault(kind, []).append(slug)
    print({k: len(v) for k, v in by_kind.items()}, "candidates to check", file=sys.stderr)
    kept: list[dict] = []
    errors: dict[str, dict[str, int]] = {k: {} for k in by_kind}
    threads = [threading.Thread(target=verify, args=(k, v, kept, errors[k])) for k, v in by_kind.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    kept.sort(key=lambda e: (e["source"], e["slug"]))
    doc = {
        "version": date.today().isoformat(),
        "method": "Common Crawl URL index under the board hosts; each board checked against its public API and kept with >= 1 open job",
        "crawls": crawls,
        "index_queries_failed": failed,
        "candidates": {k: len(v) for k, v in by_kind.items()},
        "rejected": errors,
        "entries": kept,
    }
    args.out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {args.out}: {len(kept)} companies {dict((k, sum(1 for e in kept if e['source'] == k)) for k in by_kind)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
