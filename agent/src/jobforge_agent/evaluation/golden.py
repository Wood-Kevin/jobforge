"""The golden set (ARCHITECTURE 6.7, P1.9): the user's own calls on jobs, and the reports that compare
JobForge against them.

Labels are append-only rows in `golden_labels` (migration 0008); the latest row per job is the label
(a withdrawal is a row too). A label records the posting version and the recommendation shown at the
time, so a later model, prompt or rule version is measured against the same call.

Reports print numbers, never a verdict (Engineering Baseline, "Evidence over verdicts"):
- `report(conn)`: per version tuple (model, E1 prompt, interpreter, E3 prompt, rule version): false
  SKIP (labeled APPLY/STRETCH, system SKIP), false APPLY (labeled SKIP, system APPLY/STRETCH), REVIEW
  share, REVIEWs the user called obvious, and the 4x4 confusion table. Labels from anyone but the user
  are provisional (Q27) and counted only once `reviewed_by` is set.
- `interpretation_report(...)`: E1 with a real model on the layer-A snippets, then E2; per area, how
  often the model's raw reading agreed with the code's cues and how often E2 caught a disagreement.
"""
from __future__ import annotations

import json
import sqlite3
from importlib import resources

import yaml

from jobforge_agent.evaluation import store

CATEGORIES = ("APPLY", "STRETCH", "REVIEW", "SKIP")
ELIGIBILITY = ("ELIGIBLE", "BLOCKED", "UNKNOWN")
TARGET_LABELS = (20, 30)


class LabelError(ValueError):
    pass


# ----- labeling (user connection, caller holds the transaction) -----

def label(conn: sqlite3.Connection, job_id: int, fields: dict, now: int) -> dict:
    row = conn.execute("SELECT current_raw_posting_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise LookupError(f"job {job_id}")
    rec = fields.get("recommendation")
    if rec not in CATEGORIES:
        raise LabelError("recommendation must be APPLY, STRETCH, REVIEW or SKIP")
    elig = fields.get("eligibility")
    if elig is not None and elig not in ELIGIBILITY:
        raise LabelError("eligibility must be ELIGIBLE, BLOCKED or UNKNOWN (or left out)")
    obvious = fields.get("obvious", False)
    if not isinstance(obvious, bool):
        raise LabelError("obvious must be true or false")
    misread = fields.get("misread_requirement_ids") or []
    if not isinstance(misread, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in misread):
        raise LabelError("misread_requirement_ids must be a list of requirement ids")
    if misread:
        known = {r[0] for r in conn.execute(
            "SELECT r.id FROM requirements r JOIN posting_analyses pa ON pa.id = r.posting_analysis_id WHERE pa.job_id = ?",
            (job_id,))}
        bad = sorted(set(misread) - known)
        if bad:
            raise LabelError(f"requirements {bad} are not from this job")
    note = fields.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 2000):
        raise LabelError("note must be text up to 2000 characters")
    shown = store.job_recommendation(conn, job_id)
    shown_id = shown["recommendation"]["id"] if shown and shown.get("recommendation") else None
    new_id = conn.execute(
        "INSERT INTO golden_labels (job_id, raw_posting_id, recommendation, eligibility, obvious, misread_requirement_ids, note, "
        "labeled_by, shown_recommendation_id, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, 'user', ?, ?) RETURNING id",
        (job_id, row[0], rec, elig, int(obvious), json.dumps(sorted(set(misread))), note or None, shown_id, now)).fetchone()[0]
    return current_label(conn, job_id) | {"id": new_id}


def withdraw(conn: sqlite3.Connection, job_id: int, now: int) -> None:
    cur = current_label(conn, job_id)
    if cur is None:
        raise LookupError(f"label for job {job_id}")
    conn.execute("INSERT INTO golden_labels (job_id, raw_posting_id, state, labeled_by, created_at_ms) "
                 "VALUES (?, ?, 'withdrawn', 'user', ?)", (job_id, cur["raw_posting_id"], now))


def current_label(conn: sqlite3.Connection, job_id: int) -> dict | None:
    r = conn.execute("SELECT id, raw_posting_id, state, recommendation, eligibility, obvious, misread_requirement_ids, note, "
                     "labeled_by, reviewed_by, shown_recommendation_id, created_at_ms FROM golden_labels "
                     "WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)).fetchone()
    if r is None or r[2] == "withdrawn":
        return None
    return {"id": r[0], "raw_posting_id": r[1], "recommendation": r[3], "eligibility": r[4], "obvious": bool(r[5]),
            "misread_requirement_ids": json.loads(r[6]), "note": r[7], "labeled_by": r[8], "reviewed_by": r[9],
            "shown_recommendation_id": r[10], "created_at_ms": r[11]}


# ----- golden-set report (read only) -----

_LABELS = (
    "SELECT g.job_id, g.raw_posting_id, g.recommendation, g.eligibility, g.obvious, g.misread_requirement_ids, "
    "g.labeled_by, g.reviewed_by FROM golden_labels g "
    "WHERE g.id = (SELECT max(g2.id) FROM golden_labels g2 WHERE g2.job_id = g.job_id) AND g.state = 'active'")

# Every recommendation for the labeled posting version, newest first, with the versions that made it.
_SYSTEM = (
    "SELECT rc.id, rc.recommendation, rc.eligibility, rc.verdict_rule_version, e.match_prompt_version, mc.model_id, "
    "pa.extraction_prompt_version, pa.interpreter_version FROM recommendations rc "
    "JOIN evaluations e ON e.id = rc.evaluation_id JOIN posting_analyses pa ON pa.id = e.posting_analysis_id "
    "JOIN model_configs mc ON mc.id = e.model_config_id "
    "WHERE e.job_id = ? AND pa.raw_posting_id = ? ORDER BY rc.id DESC")


def report(conn: sqlite3.Connection) -> dict:
    labels, provisional = [], 0
    for r in conn.execute(_LABELS):
        if r[6] != "user" and not r[7]:
            provisional += 1
            continue
        labels.append({"job_id": r[0], "raw_posting_id": r[1], "label": r[2], "eligibility": r[3], "obvious": bool(r[4]),
                       "misread": json.loads(r[5])})
    by_version: dict[tuple, dict] = {}
    unanswered = []
    for lab in labels:
        seen = set()
        for s in conn.execute(_SYSTEM, (lab["job_id"], lab["raw_posting_id"])):
            key = (s[5], s[6], s[7], s[4], s[3])  # model, E1 prompt, interpreter, E3 prompt, rules
            if key in seen:
                continue  # newest recommendation per version tuple per job
            seen.add(key)
            by_version.setdefault(key, []).append({**lab, "system": s[1], "system_eligibility": s[2]})
        if not seen:
            unanswered.append(lab["job_id"])
    versions = []
    for key, rows in sorted(by_version.items(), key=lambda kv: -len(kv[1])):
        confusion = {a: {b: 0 for b in CATEGORIES} for a in CATEGORIES}
        for x in rows:
            confusion[x["label"]][x["system"]] += 1
        system_review = [x for x in rows if x["system"] == "REVIEW"]
        elig = [x for x in rows if x["eligibility"]]
        versions.append({
            "model": key[0], "extraction_prompt": key[1], "interpreter": key[2], "match_prompt": key[3], "rules": key[4],
            "labeled_with_answer": len(rows),
            "false_skip": [x["job_id"] for x in rows if x["label"] in ("APPLY", "STRETCH") and x["system"] == "SKIP"],
            "false_apply": [x["job_id"] for x in rows if x["label"] == "SKIP" and x["system"] in ("APPLY", "STRETCH")],
            "review_share": round(len(system_review) / len(rows), 3) if rows else None,
            "review_count": len(system_review),
            "review_labeled_obvious": [x["job_id"] for x in system_review if x["label"] != "REVIEW" and x["obvious"]],
            "eligibility_labeled": len(elig),
            "eligibility_agree": sum(1 for x in elig if x["eligibility"] == x["system_eligibility"]),
            "confusion": confusion,
        })
    return {"labels": len(labels), "target": list(TARGET_LABELS), "provisional_unreviewed": provisional,
            "labels_without_system_answer": unanswered,
            "misread_requirements": sum(len(x["misread"]) for x in labels), "versions": versions}


def format_report(r: dict) -> str:
    out = [f"Golden set: {r['labels']} labels (target {r['target'][0]}-{r['target'][1]}); "
           f"provisional unreviewed (not counted): {r['provisional_unreviewed']}; "
           f"requirements marked misread: {r['misread_requirements']}",
           f"Labeled jobs with no recommendation for the labeled posting version: {len(r['labels_without_system_answer'])} "
           f"{r['labels_without_system_answer'] or ''}"]
    for v in r["versions"]:
        out += ["", f"== model {v['model']} | E1 {v['extraction_prompt']} | E2 {v['interpreter']} | E3 {v['match_prompt']} | "
                    f"rules {v['rules']}",
                f"  labeled jobs with an answer: {v['labeled_with_answer']}",
                f"  false SKIP (labeled APPLY/STRETCH, system SKIP): {len(v['false_skip'])} jobs {v['false_skip']}",
                f"  false APPLY (labeled SKIP, system APPLY/STRETCH): {len(v['false_apply'])} jobs {v['false_apply']}",
                f"  REVIEW share: {v['review_count']}/{v['labeled_with_answer']} = {v['review_share']}; "
                f"labeled obvious: {len(v['review_labeled_obvious'])} {v['review_labeled_obvious']}",
                f"  eligibility: {v['eligibility_agree']} of {v['eligibility_labeled']} labeled agree",
                "  confusion (rows = your label, columns = JobForge):",
                "           " + "".join(f"{c:>9}" for c in CATEGORIES)]
        out += [f"  {a:>9}" + "".join(f"{v['confusion'][a][b]:>9}" for b in CATEGORIES) for a in CATEGORIES]
    return "\n".join(out)


# ----- interpretation report (a real model on the layer-A snippets) -----

def interpretation_cases() -> list[dict]:
    text = (resources.files("jobforge_agent.analysis") / "fixtures" / "layer_a.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def _words(s: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in s.casefold()).split())


def _fields_match(row, want: dict) -> bool:
    names = [f["flag"] for f in row.flags]
    for key, value in want.items():
        if key == "flags":
            if not set(value) <= set(names):
                return False
        elif key == "no_flags":
            if set(value) & set(names):
                return False
        elif key == "years":
            if [f.get("values") for f in row.flags if f["flag"] == "years"][:1] != [value]:
                return False
        elif getattr(row, key, None) != value:
            return False
    return True


def score_case(case: dict, rows) -> dict:
    """Compare E2 rows produced from a real model's E1 answer with the case's expectations."""
    model_rows = [r for r in rows if r.origin == "model"]
    out = {"expected": 0, "extracted": 0, "as_expected": 0, "raw_agreed": 0, "conflict_caught": 0, "recall_ok": None}
    for quote, want in (case.get("expect") or {}).items():
        out["expected"] += 1
        q = _words(quote)
        hit = next((r for r in model_rows if q and (q in _words(r.jd_quote) or _words(r.jd_quote) in q) and _words(r.jd_quote)), None)
        if hit is None:
            continue
        out["extracted"] += 1
        out["as_expected"] += _fields_match(hit, want)
        out["raw_agreed"] += hit.interpretation_status == "confirmed"
        out["conflict_caught"] += hit.interpretation_status == "conflict"
    if "recall" in case:
        out["recall_ok"] = [r.eligibility_type for r in rows if r.origin == "unextracted_eligibility"] == case["recall"]
    return out


def format_interpretation(per_area: dict, meta: dict) -> str:
    out = [meta["stopped"]] if meta.get("stopped") else []
    out += [f"Interpretation report: model {meta['model']} | E1 {meta['extraction_prompt']} | E2 {meta['interpreter']} | "
           f"{meta['cases']} cases, {meta['failed']} model calls failed {meta['failed_ids'] or ''}",
           f"{'area':<12}{'cases':>6}{'expected':>9}{'extracted':>10}{'as expected':>12}{'raw agreed':>11}{'E2 caught':>10}{'recall ok':>10}"]
    for area, a in sorted(per_area.items()):
        recall = f"{a['recall_ok']}/{a['recall_cases']}" if a["recall_cases"] else "-"
        out.append(f"{area:<12}{a['cases']:>6}{a['expected']:>9}{a['extracted']:>10}{a['as_expected']:>12}"
                   f"{a['raw_agreed']:>11}{a['conflict_caught']:>10}{recall:>10}")
    out.append("raw agreed = the model's reading matched the code's cues (status confirmed); E2 caught = they disagreed and the "
               "row was stored as a conflict with both readings.")
    return "\n".join(out)


async def run_interpretation(client, cfg, cases: list[dict]) -> tuple[dict, dict]:
    """E1 (real model) + E0/E2 on each case with expectations; no database access."""
    from jobforge_agent.analysis import extract
    from jobforge_agent.analysis.interpret import interpret
    from jobforge_agent.analysis.segment import segment
    from jobforge_agent.analysis.versions import EXTRACTION_PROMPT_VERSION, INTERPRETER_VERSION
    from jobforge_agent.inference.client import InferenceError

    per_area: dict[str, dict] = {}
    failed = []
    stopped = None
    scored = [c for c in cases if c.get("expect") or "recall" in c]
    for case in scored:
        a = per_area.setdefault(case["area"], {"cases": 0, "expected": 0, "extracted": 0, "as_expected": 0, "raw_agreed": 0,
                                               "conflict_caught": 0, "recall_cases": 0, "recall_ok": 0})
        a["cases"] += 1
        try:
            result = await client.chat(cfg, extract.messages("Interpretation suite", case["text"], cfg.structured_output_mode),
                                       extract.response_format(cfg.structured_output_mode))
            items = extract.parse(result.content)
        except InferenceError as exc:
            failed.append(f"{case['id']} ({exc.kind}: {exc.detail[:80]})")
            if exc.kind == "unavailable":
                stopped = f"stopped: the model at {cfg.base_url} is not reachable ({exc.detail}); start it and rerun"
                break
            continue
        except extract.InvalidOutput as exc:
            failed.append(f"{case['id']} (invalid output: {str(exc)[:80]})")
            continue
        s = score_case(case, interpret(segment(case["text"]), items))
        for k in ("expected", "extracted", "as_expected", "raw_agreed", "conflict_caught"):
            a[k] += s[k]
        if s["recall_ok"] is not None:
            a["recall_cases"] += 1
            a["recall_ok"] += s["recall_ok"]
    meta = {"model": cfg.model_id, "extraction_prompt": EXTRACTION_PROMPT_VERSION, "interpreter": INTERPRETER_VERSION,
            "cases": len(scored), "failed": len(failed), "failed_ids": failed, "stopped": stopped}
    return per_area, meta
