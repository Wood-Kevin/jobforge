"""First-run setup (P1.8b): what is configured, and turning what a person has (a career-page link,
a watchlist file, job-scanner's config.yaml) into JobForge's own data. No JSON or patterns asked of
the user.
"""
from __future__ import annotations

import re
import sqlite3

import yaml

from jobforge_agent.inference import steps

# A careers link -> (board kind, company id on that board). Only the three reviewed sources.
_BOARD_URL = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
]
_SHORT = re.compile(r"^(greenhouse|lever|ashby)\s*[:/]\s*([A-Za-z0-9_.%-]+)$", re.I)


def parse_board_lines(text: str) -> tuple[list[dict], list[str]]:
    """Each non-empty line: a careers-page link, or "greenhouse:acme". Returns (employers, lines not understood)."""
    found: dict[tuple[str, str], dict] = {}
    bad = []
    for raw in (text or "").splitlines():
        line = raw.strip().rstrip("/")
        if not line or line.startswith("#"):
            continue
        hit = None
        m = _SHORT.match(line)
        if m:
            hit = (m.group(1).lower(), m.group(2))
        else:
            for kind, rx in _BOARD_URL:
                m = rx.search(line)
                if m:
                    hit = (kind, m.group(1))
                    break
        if hit is None:
            bad.append(line)
            continue
        kind, slug = hit[0], hit[1].lower() if hit[0] != "ashby" else hit[1]
        found[(kind, slug)] = {"source": kind, "slug": slug, "name": slug.replace("-", " ").replace("_", " ").title()}
    return list(found.values()), bad


def parse_company_lines(text: str) -> tuple[list[dict], list[str], list[str]]:
    """Board links plus company names (P1.10). Returns (employers from links, names to look up,
    lines not understood). A line that looks like a link but is not one of the three boards (a
    Workday page, say) is not understood rather than looked up as a name."""
    found, rest = parse_board_lines(text)
    names, bad = [], []
    for line in rest:
        if "/" in line or "://" in line or not re.search(r"[A-Za-z0-9]", line):
            bad.append(line)
        else:
            names.append(re.sub(r"\.(com|io|co|ai|net|org)$", "", line, flags=re.I).strip())
    return found, list(dict.fromkeys(names)), bad


def parse_watchlist(text: str) -> list[dict]:
    """job-scanner's watchlist.yaml (employers: [{name, ats, slug}]) or a plain list of the same."""
    data = yaml.safe_load(text or "")
    entries = data.get("employers") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("This file has no list of employers (expected an 'employers:' list).")
    return [e for e in entries if isinstance(e, dict)]


def parse_scanner_config(text: str) -> dict:
    data = yaml.safe_load(text or "")
    if not isinstance(data, dict) or not isinstance(data.get("title"), dict):
        raise ValueError("This does not look like job-scanner's config.yaml (no 'title:' section).")
    return data


def status(conn: sqlite3.Connection) -> dict:
    """Which setup steps are done; the window shows the setup guide until the first three are."""
    employers = conn.execute("SELECT count(*) FROM employers WHERE enabled = 1").fetchone()[0]
    profile = conn.execute("SELECT version FROM search_profiles ORDER BY version DESC LIMIT 1").fetchone()
    # The model on this PC (the fallback step, SPEC D18): setup step 3 turns it on; ChatGPT is optional, in Settings.
    local = steps.configured(conn, steps.FALLBACK)
    model = conn.execute("SELECT name, model_id, base_url, enabled, tested_at_ms FROM model_configs WHERE id = ?",
                         (local if local is not None else -1,)).fetchone()
    items = conn.execute("SELECT count(*) FROM experience_items WHERE kind <> 'eligibility'").fetchone()[0]
    facts, facts_verified = conn.execute(
        "SELECT count(*), coalesce(sum(status = 'verified'), 0) FROM experience_items WHERE kind = 'eligibility'").fetchone()
    return {
        "employers": {"done": employers > 0, "enabled": employers},
        "search_profile": {"done": profile is not None, "version": profile[0] if profile else None},
        "model": {"done": bool(model and model[3]), "name": model[0] if model else None, "model_id": model[1] if model else None,
                  "base_url": model[2] if model else None, "tested": bool(model and model[4])},
        "profile": {"done": items > 0, "items": items, "facts": facts, "facts_verified": facts_verified},
        "complete": employers > 0 and profile is not None and bool(model and model[3]) and items > 0,
    }
