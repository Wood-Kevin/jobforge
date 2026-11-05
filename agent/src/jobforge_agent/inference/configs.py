"""Model configs: list, load-and-snippet test, enable/disable (ARCHITECTURE 6.8).

"Model availability is checked, not assumed": a config can be enabled only after its test
passed (CHECK `enabled = 0 OR tested_at_ms IS NOT NULL`). The test lists the server's models,
then runs E1 + E2 on a fixed synthetic snippet and requires: the model is listed, the answer is
schema-valid, `finish_reason = stop`, at least one requirement, and every quote found.
"""
from __future__ import annotations

import dataclasses
import ipaddress
import json
import sqlite3
import urllib.parse

from jobforge_agent.analysis import extract
from jobforge_agent.analysis.interpret import interpret
from jobforge_agent.analysis.segment import segment
from jobforge_agent.inference import steps
from jobforge_agent.inference.client import InferenceClient, InferenceError, ModelConfig

# Synthetic: no personal data, stable across versions.
TEST_SNIPPET = ("Requirements:\n- Must be a US citizen.\n- 3+ years of Linux administration experience.\n\n"
                "Nice to have:\n- CCNA certification is a plus.")


class ConfigError(Exception):
    pass


# The test's own call never waits longer than this, whatever the config allows a real posting (another computer: 600 s):
# the window waits 180 s for a test (host lib.rs), and the snippet is short (the Mac answered in 24 s, 2026-11-04).
TEST_TIMEOUT_S = 150


def listing(conn: sqlite3.Connection) -> list[dict]:
    used = steps.listing(conn)
    rows = []
    for r in conn.execute("SELECT id, name, provider_label, provider_kind, base_url, model_id, params, structured_output_mode, "
                          "enabled, tested_at_ms FROM model_configs ORDER BY id"):
        d = dict(r)
        d["params"] = json.loads(d["params"])
        d["enabled"] = bool(d["enabled"])
        d["steps"] = [s for s, cid in used.items() if cid == d["id"]]
        d["out"] = steps.OUT.get(d["id"])
        rows.append(d)
    return rows


async def run_test(client: InferenceClient, cfg: ModelConfig) -> dict:
    """The model call. No database access here, so no transaction can span it."""
    report: dict = {"model_config_id": cfg.id, "model_id": cfg.model_id, "listed": None, "ok": False}
    try:
        models = await client.models(cfg)
    except InferenceError as exc:
        report["error"] = str(exc)
        return report
    report["listed"] = cfg.model_id in models
    if not report["listed"]:
        report["error"] = f"model not listed by {cfg.base_url}/models"
        return report
    if cfg.timeout_s > TEST_TIMEOUT_S:
        cfg = dataclasses.replace(cfg, params={**cfg.params, "timeout_s": TEST_TIMEOUT_S})
    try:
        result = await client.chat(cfg, extract.messages("Test posting", TEST_SNIPPET, cfg.structured_output_mode),
                                   extract.response_format(cfg.structured_output_mode))
    except InferenceError as exc:
        report["error"] = str(exc)
        return report
    report.update(seconds=result.seconds, finish_reason=result.finish_reason, usage=result.usage,
                  model_reported=result.model_reported)
    if result.finish_reason != "stop":
        report["error"] = f"finish_reason={result.finish_reason}"
        return report
    try:
        items = extract.parse(result.content)
    except extract.InvalidOutput as exc:
        report["error"] = f"invalid output: {exc}"
        return report
    reqs = interpret(segment(TEST_SNIPPET), items)
    model_rows = [r for r in reqs if r.origin == "model"]
    report["requirements"] = [{"jd_quote": r.jd_quote, "quote_found": r.quote_found, "final_class": r.final_class,
                               "interpretation_status": r.interpretation_status, "eligibility_type": r.eligibility_type}
                              for r in model_rows]
    report["ok"] = bool(model_rows) and all(r.quote_found for r in model_rows)
    if not report["ok"]:
        report["error"] = "no requirements" if not model_rows else "a quote was not found in the snippet"
    return report


def record_tested(conn: sqlite3.Connection, config_id: int, now: int) -> None:
    conn.execute("UPDATE model_configs SET tested_at_ms = ? WHERE id = ?", (now, config_id))


def set_enabled(conn: sqlite3.Connection, config_id: int, enabled: bool) -> None:
    row = conn.execute("SELECT tested_at_ms FROM model_configs WHERE id = ?", (config_id,)).fetchone()
    if row is None:
        raise LookupError(config_id)
    if enabled and row[0] is None:
        raise ConfigError("run the config test first (POST /v1/model-configs/{id}/test)")
    conn.execute("UPDATE model_configs SET enabled = ? WHERE id = ?", (int(enabled), config_id))


# Local configs (SPEC D19): a model on this PC or on another of the person's own computers. Prompts carry profile items
# and resume text over plain HTTP, so the server must be this PC (loopback) or reached through Tailscale, which encrypts
# between the person's devices: a 100.64.0.0/10 or fd7a:115c:a1e0::/48 address, or a MagicDNS name under .ts.net.
# Rejected: any home-network address (plain text on the LAN, readable by anything on it) and public hosts.
LOCAL_PARAMS = {"reasoning_effort": "none", "temperature": 0, "seed": 7, "max_tokens": 4000}
# Another computer is slower than this PC's GPU (MacBook Air, 2026-11-04: up to 264 s to read a long posting).
REMOTE_TIMEOUT_S = 600
_TAILNET = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def check_local_url(base_url: str) -> tuple[str, bool]:
    """(normalized base URL ending in /v1, is it this PC), or ConfigError saying why the address is not allowed."""
    raw = base_url.strip()
    if "://" not in raw:
        raw = "http://" + raw
    u = urllib.parse.urlsplit(raw)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ConfigError("enter the address as http://<computer>:<port>, for example http://100.101.102.103:11434")
    host = u.hostname.lower()
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    loopback = host == "localhost" or (ip is not None and ip.is_loopback)
    if not (loopback or (ip is not None and any(ip in n for n in _TAILNET)) or (ip is None and host.endswith(".ts.net"))):
        raise ConfigError("JobForge sends your profile to the model, so the model must run on this PC or on one of your "
                          "computers reached through Tailscale (an address starting with 100. or a name ending in .ts.net)")
    path = u.path.rstrip("/") or "/v1"
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), path, "", "")), loopback


# The reading config of a local model (posting reading and resume suggestions copy text word for word): no presence
# penalty. Measured 2026-11-04 on Qwen 3.5 9B (its Ollama build defaults to 1.5), 15 postings: 232 lines instead of 226,
# 24 in conflict instead of 34, 151 instead of 129 the same as the stored reading. Matching keeps the server's default:
# with 0 it over-credited more (8 instead of 5 on the 24 labels). ARCHITECTURE 6.9.
READING_PARAMS = {"presence_penalty": 0}


def upsert_local_config(conn: sqlite3.Connection, base_url: str, model_id: str, reading: bool = False) -> int:
    """The config row for a local model at an address, created on first use (caller holds the transaction): the plain
    one (fallback, matching) or, with `reading`, the one for posting reading and resume suggestions (READING_PARAMS)."""
    url, loopback = check_local_url(base_url)
    model_id = model_id.strip()
    if not model_id:
        raise ConfigError("enter the model's name as the server lists it, for example qwen3.5:9b")
    # Names are unique: host and port tell two servers on one computer apart, and the reading config is named apart.
    name = f"{model_id} ({urllib.parse.urlsplit(url).netloc}{', reading' if reading else ''})"
    row = conn.execute("SELECT id FROM model_configs WHERE provider_kind = 'openai_compatible' AND name = ?", (name,)).fetchone()
    if row:
        return row[0]
    params = LOCAL_PARAMS if loopback else {**LOCAL_PARAMS, "timeout_s": REMOTE_TIMEOUT_S}
    if reading:
        params = {**params, **READING_PARAMS}
    return conn.execute(
        "INSERT INTO model_configs (name, provider_label, provider_kind, base_url, model_id, params, structured_output_mode, "
        "enabled) VALUES (?, ?, 'openai_compatible', ?, ?, ?, 'json_schema', 0) RETURNING id",
        (name, "This PC" if loopback else "Another computer", url, model_id, json.dumps(params))).fetchone()[0]


def reading_config(conn: sqlite3.Connection, config_id: int | None) -> int | None:
    """The turned-on reading config of the same server and model as `config_id`, or `config_id` itself."""
    if config_id is None:
        return None
    row = conn.execute("SELECT r.id FROM model_configs c JOIN model_configs r ON r.base_url = c.base_url AND r.model_id = c.model_id "
                       "AND r.name = substr(c.name, 1, length(c.name) - 1) || ', reading)' AND r.enabled = 1 "
                       "WHERE c.id = ? AND c.provider_kind = 'openai_compatible'", (config_id,)).fetchone()
    return row[0] if row else config_id


# ChatGPT configs (SPEC D18). The plan's models are reasoning models: a larger output limit than the local 4,000
# (reasoning tokens count against it; step 1 measured 1,150-1,580 output tokens per E3 call) and a longer wait.
CHATGPT_PARAMS = {"max_tokens": 16000, "timeout_s": 300}


def upsert_chatgpt_config(conn: sqlite3.Connection, slug: str, display_name: str) -> int:
    """The config row for one of the plan's models, created on first use (caller holds the transaction)."""
    from jobforge_agent.inference.chatgpt_auth import RESOURCE
    row = conn.execute("SELECT id FROM model_configs WHERE provider_kind = 'chatgpt' AND model_id = ?", (slug,)).fetchone()
    if row:
        return row[0]
    return conn.execute(
        "INSERT INTO model_configs (name, provider_label, provider_kind, base_url, model_id, params, structured_output_mode, "
        "enabled) VALUES (?, 'ChatGPT', 'chatgpt', ?, ?, ?, 'json_schema', 0) RETURNING id",
        (f"{display_name} (ChatGPT)", RESOURCE, slug, json.dumps(CHATGPT_PARAMS))).fetchone()[0]


_USAGE = (
    "SELECT date(t.created_at_ms / 1000, 'unixepoch', 'localtime') AS day, t.model_config_id, ? AS step, count(a.value) AS calls, "
    "coalesce(sum(json_extract(a.value, '$.usage.prompt_tokens')), 0) AS input_tokens, "
    "coalesce(sum(json_extract(a.value, '$.usage.completion_tokens')), 0) AS output_tokens "
    "FROM {table} t, json_each(t.raw_response, '$.attempts') a "
    # A reused E1 answer carries the original's attempts (analysis/worker._reuse): not a call.
    "WHERE t.raw_response IS NOT NULL AND json_extract(t.raw_response, '$.reused_from_analysis') IS NULL "
    "AND t.created_at_ms >= ? GROUP BY day, t.model_config_id")


def usage(conn: sqlite3.Connection, since_ms: int) -> list[dict]:
    """Model calls and tokens per day (local time, by when the work was queued), config and step, from the answers
    stored on each row (reused answers made no call and are not counted). Resume suggestions keep no rows, so they
    are not in it; their log line has the config."""
    rows = []
    for table, step in (("posting_analyses", "e1"), ("evaluations", "e3")):
        rows += [dict(r) for r in conn.execute(_USAGE.format(table=table), (step, since_ms))]
    return sorted(rows, key=lambda r: (r["day"], r["step"], r["model_config_id"]))
