"""P1.0c spike: what does polling each Phase 1 source cost?

Measures, per source (one small and one large board each):
- robots.txt status and whether the board path is allowed
- bytes on the wire (gzip) and decompressed, request time
- caching/rate-limit headers (ETag, Last-Modified, Cache-Control, RateLimit-*, Retry-After)
- whether a conditional repeat (If-None-Match / If-Modified-Since) returns 304
- whether two back-to-back responses are byte-identical (volatile fields?)
- which per-job timestamp fields exist (list-then-detail feasibility)
- Greenhouse only: size of the list without content=true

Throwaway measurement code, not app code. Prints raw evidence, no verdicts.
Saves a few postings to samples/ for spike P1.0b.

Run: python -I spikes/p1_0/source_cost.py
"""
from __future__ import annotations

import gzip
import hashlib
import json
import pathlib
import sys
import time
import urllib.robotparser

import httpx

UA = "jobforge-spike/0.1 (personal job search tool; polling-cost measurement)"
SPACING_S = 2.0
OUT = pathlib.Path(__file__).parent / "results"
SAMPLES = pathlib.Path(__file__).parent / "samples"

BOARDS = [
    ("greenhouse", "tailscale", "https://boards-api.greenhouse.io/v1/boards/tailscale/jobs?content=true"),
    ("greenhouse", "databricks", "https://boards-api.greenhouse.io/v1/boards/databricks/jobs?content=true"),
    ("lever", "jumpcloud", "https://api.lever.co/v0/postings/jumpcloud?mode=json"),
    ("lever", "palantir", "https://api.lever.co/v0/postings/palantir?mode=json"),
    ("ashby", "render", "https://api.ashbyhq.com/posting-api/job-board/render?includeCompensation=true"),
    ("ashby", "openai", "https://api.ashbyhq.com/posting-api/job-board/openai?includeCompensation=true"),
]
EXTRA = [
    ("greenhouse", "databricks (no content)", "https://boards-api.greenhouse.io/v1/boards/databricks/jobs"),
]
HEADERS_OF_INTEREST = [
    "etag", "last-modified", "cache-control", "age", "expires", "vary",
    "retry-after", "ratelimit-limit", "ratelimit-remaining", "ratelimit-reset",
    "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
    "content-encoding", "content-type", "cf-cache-status", "x-cache",
]


def fetch(client: httpx.Client, url: str, extra_headers: dict | None = None) -> dict:
    """Raw fetch with gzip requested; measures compressed and decompressed size."""
    headers = {"Accept-Encoding": "gzip"}
    if extra_headers:
        headers.update(extra_headers)
    t0 = time.perf_counter()
    with client.stream("GET", url, headers=headers) as r:
        wire = b"".join(r.iter_raw())
        elapsed_ms = round((time.perf_counter() - t0) * 1000)
        enc = r.headers.get("content-encoding", "")
        body = gzip.decompress(wire) if enc == "gzip" and wire else wire
        time.sleep(SPACING_S)
        return {
            "status": r.status_code,
            "wire_bytes": len(wire),
            "body_bytes": len(body),
            "ms": elapsed_ms,
            "headers": {h: r.headers[h] for h in HEADERS_OF_INTEREST if h in r.headers},
            "body": body,
        }


def jobs_of(source: str, data) -> list[dict]:
    if source == "greenhouse":
        return data.get("jobs", [])
    if source == "lever":
        return data if isinstance(data, list) else []
    if source == "ashby":
        return data.get("jobs", [])
    return []


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    SAMPLES.mkdir(parents=True, exist_ok=True)
    report: dict = {"user_agent": UA, "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "robots": {}, "boards": []}
    with httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=False) as client:
        # robots.txt once per host
        for source, _, url in BOARDS:
            host = httpx.URL(url).host
            if host in report["robots"]:
                continue
            r = fetch(client, f"https://{host}/robots.txt")
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(r["body"].decode("utf-8", "replace").splitlines())
            report["robots"][host] = {
                "status": r["status"],
                "body": r["body"].decode("utf-8", "replace")[:2000],
                # stdlib parser treats 4xx as disallow-all; record raw status so the
                # RFC 9309 reading (4xx = allowed) can be applied by the reader
                "stdlib_can_fetch_board_path": rp.can_fetch(UA, url) if r["status"] == 200 else None,
            }

        for source, slug, url in BOARDS + EXTRA:
            first = fetch(client, url)
            entry = {k: v for k, v in first.items() if k != "body"}
            entry.update(source=source, board=slug, url=url)
            try:
                data = json.loads(first["body"])
            except ValueError:
                data = None
            jobs = jobs_of(source, data) if data is not None else []
            entry["job_count"] = len(jobs)
            entry["bytes_per_job"] = round(first["body_bytes"] / len(jobs)) if jobs else None
            entry["job_keys"] = sorted(jobs[0].keys()) if jobs else []
            entry["timestamp_fields_on_job"] = {
                k: jobs[0][k] for k in (jobs[0].keys() if jobs else [])
                if any(t in k.lower() for t in ("updated", "created", "published", "date"))
            }

            # repeat: unconditional (identical bytes?) then conditional (304?)
            second = fetch(client, url)
            entry["repeat_identical_body"] = (
                hashlib.sha256(first["body"]).hexdigest() == hashlib.sha256(second["body"]).hexdigest()
            )
            cond = {}
            if "etag" in first["headers"]:
                cond["If-None-Match"] = first["headers"]["etag"]
            if "last-modified" in first["headers"]:
                cond["If-Modified-Since"] = first["headers"]["last-modified"]
            if cond:
                third = fetch(client, url, cond)
                entry["conditional_request"] = {"sent": cond, "status": third["status"], "wire_bytes": third["wire_bytes"]}
            else:
                entry["conditional_request"] = "no ETag or Last-Modified offered"
            report["boards"].append(entry)

            # samples for P1.0b: up to 2 postings with full descriptions per small board
            if slug in ("tailscale", "jumpcloud", "render") and jobs:
                for j in jobs[:2]:
                    native = str(j.get("id"))
                    (SAMPLES / f"{source}_{slug}_{native}.json").write_text(json.dumps(j, indent=2), encoding="utf-8")

    path = OUT / "source_cost.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2)[:20000])
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
