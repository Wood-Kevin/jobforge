"""Model configs: list, load-and-snippet test, enable/disable (ARCHITECTURE 6.8).

"Model availability is checked, not assumed": a config can be enabled only after its test
passed (CHECK `enabled = 0 OR tested_at_ms IS NOT NULL`). The test lists the server's models,
then runs E1 + E2 on a fixed synthetic snippet and requires: the model is listed, the answer is
schema-valid, `finish_reason = stop`, at least one requirement, and every quote found.
"""
from __future__ import annotations

import json
import sqlite3

from jobforge_agent.analysis import extract
from jobforge_agent.analysis.interpret import interpret
from jobforge_agent.analysis.segment import segment
from jobforge_agent.inference.client import InferenceClient, InferenceError, ModelConfig

# Synthetic: no personal data, stable across versions.
TEST_SNIPPET = ("Requirements:\n- Must be a US citizen.\n- 3+ years of Linux administration experience.\n\n"
                "Nice to have:\n- CCNA certification is a plus.")


class ConfigError(Exception):
    pass


def listing(conn: sqlite3.Connection) -> list[dict]:
    default = conn.execute("SELECT value FROM settings WHERE key = 'default_model_config_id'").fetchone()
    rows = []
    for r in conn.execute("SELECT id, name, provider_label, base_url, model_id, params, structured_output_mode, enabled, "
                          "tested_at_ms FROM model_configs ORDER BY id"):
        d = dict(r)
        d["params"] = json.loads(d["params"])
        d["enabled"] = bool(d["enabled"])
        d["default"] = default is not None and default[0] == str(d["id"])
        rows.append(d)
    return rows


async def run_test(client: InferenceClient, cfg: ModelConfig) -> dict:
    """The model call. No database access here, so no transaction can span it."""
    report: dict = {"model_config_id": cfg.id, "model_id": cfg.model_id, "listed": None, "ok": False}
    try:
        models = await client.list_models(cfg.base_url)
    except InferenceError as exc:
        report["error"] = str(exc)
        return report
    report["listed"] = cfg.model_id in models
    if not report["listed"]:
        report["error"] = f"model not listed by {cfg.base_url}/models"
        return report
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
