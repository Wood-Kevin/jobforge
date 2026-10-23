"""Evaluation rows: sync (snapshot, queue, recompute), claim, persist, read (ARCHITECTURE 3.4, 4.2, 6.4; SPEC D10).

Every write is one short transaction on the 'evaluation' connection. Like posting analysis, a
model call never runs inside one: claim, commit, call, then persist.

sync() is code only and runs whether or not inference is up:
1. snapshot the current profile (content-addressed; no change, no new snapshot);
2. queue an evaluation for every open job whose current posting has a done analysis and that
   passes the active search profile (or has an "evaluate anyway" override), if none exists yet
   for (analysis, snapshot, model config, match prompt version); an older pending one for the
   same job is superseded (one pending per job, migration 0004);
3. recompute: every job's latest done evaluation without a recommendation for the active
   search profile and verdict rule version gets one (zero inference calls).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field, replace

from jobforge_agent.analysis.segment import norm
from jobforge_agent.evaluation import assess, facts, snapshot, validate
from jobforge_agent.evaluation import match
from jobforge_agent.evaluation.match import MAX_INFERRED_CLAIMS
from jobforge_agent.inference.client import ModelConfig, load_config, request_body
from jobforge_agent.evaluation.versions import MATCH_PROMPT_VERSION, VERDICT_RULE_VERSION
from jobforge_agent.filtering import pipeline as filter_pipeline
from jobforge_agent.filtering import rules as filter_rules

MAX_ATTEMPTS = 3
FIT_CLASSES = ("negotiable", "preferred")
RECOMPUTE_CHUNK = 100


def _tx(conn: sqlite3.Connection, fn):
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = fn()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return result


def _event(conn, type_: str, job_id: int | None, ref: dict, now: int) -> None:
    conn.execute("INSERT INTO change_events (type, job_id, ref, created_at_ms) VALUES (?, ?, ?, ?)",
                 (type_, job_id, json.dumps(ref), now))


# ----- sync: snapshot, queue, recompute -----

_CANDIDATES = (
    "SELECT pa.id, pa.job_id, EXISTS (SELECT 1 FROM filter_overrides fo WHERE fo.job_id = pa.job_id) "
    "FROM posting_analyses pa JOIN jobs j ON j.id = pa.job_id "
    "WHERE pa.status = 'done' AND j.closed_at_ms IS NULL AND pa.raw_posting_id = j.current_raw_posting_id "
    "AND pa.id = (SELECT max(p2.id) FROM posting_analyses p2 WHERE p2.job_id = pa.job_id AND p2.status = 'done' "
    "             AND p2.raw_posting_id = j.current_raw_posting_id) "
    "AND (EXISTS (SELECT 1 FROM filter_results fr WHERE fr.raw_posting_id = j.current_raw_posting_id "
    "             AND fr.search_profile_id = ? AND fr.ruleset_version = ? AND fr.verdict = 'pass') "
    "     OR EXISTS (SELECT 1 FROM filter_overrides fo WHERE fo.job_id = pa.job_id)) "
    # A superseded row does not count: reverting the profile to an earlier snapshot must queue it again.
    "AND NOT EXISTS (SELECT 1 FROM evaluations e WHERE e.posting_analysis_id = pa.id AND e.profile_snapshot_id = ? "
    "                AND e.model_config_id = ? AND e.match_prompt_version = ? AND e.status <> 'superseded') "
    "ORDER BY pa.id")
# Pending work that can no longer produce a current recommendation: the job closed, its posting has
# a newer version (whose analysis queues its own evaluation when done), or it evaluates a profile
# snapshot or model config that is no longer current (a reverted edit; independent P1.7 review).
# Parameters: current snapshot id, default model config id.
_STALE_PENDING = (
    "UPDATE evaluations SET status = 'superseded', last_error = 'job closed, posting changed or profile no longer current' "
    "WHERE status = 'pending' AND (profile_snapshot_id <> ? OR model_config_id <> ? "
    "OR (SELECT j.closed_at_ms IS NOT NULL OR pa.raw_posting_id <> j.current_raw_posting_id FROM posting_analyses pa "
    "    JOIN jobs j ON j.id = pa.job_id WHERE pa.id = evaluations.posting_analysis_id))")

# The evaluation a job's recommendation comes from: among its done evaluations of the current
# posting, the one for the current profile snapshot and model config if there is one (a reverted
# edit returns to an older, already done evaluation; independent P1.7 review), else the newest.
# Only open jobs that pass the active search profile or have an override (the same rule that
# queues them). Parameters, in order: current snapshot id, model config id, search profile id,
# ruleset version. Use latest_done_params().
_LATEST_DONE = (
    "SELECT e.id, e.job_id FROM evaluations e JOIN posting_analyses pa ON pa.id = e.posting_analysis_id "
    "JOIN jobs j ON j.id = e.job_id "
    "WHERE e.status = 'done' AND j.closed_at_ms IS NULL AND pa.raw_posting_id = j.current_raw_posting_id "
    "AND e.id = (SELECT e2.id FROM evaluations e2 JOIN posting_analyses p2 ON p2.id = e2.posting_analysis_id "
    "            WHERE e2.job_id = e.job_id AND e2.status = 'done' AND p2.raw_posting_id = j.current_raw_posting_id "
    "            ORDER BY (e2.profile_snapshot_id = ? AND e2.model_config_id = ?) DESC, e2.id DESC LIMIT 1) "
    "AND (EXISTS (SELECT 1 FROM filter_results fr WHERE fr.raw_posting_id = j.current_raw_posting_id "
    "             AND fr.search_profile_id = ? AND fr.ruleset_version = ? AND fr.verdict = 'pass') "
    "     OR EXISTS (SELECT 1 FROM filter_overrides fo WHERE fo.job_id = e.job_id))")


def current_snapshot_id(conn: sqlite3.Connection) -> int | None:
    """The snapshot matching the profile as it is now, if one exists yet (read only)."""
    items, completeness = snapshot._current(conn)
    row = conn.execute("SELECT id FROM profile_snapshots WHERE sha256 = ?", (snapshot.digest(items, completeness),)).fetchone()
    return row[0] if row else None


def latest_done_params(conn: sqlite3.Connection, profile) -> tuple:
    sid = current_snapshot_id(conn)
    cfg = filter_pipeline.default_model_config_id(conn)
    return (-1 if sid is None else sid, -1 if cfg is None else cfg, profile.id, filter_rules.RULESET_VERSION)


@dataclass
class SyncResult:
    snapshot_id: int | None = None
    snapshot_created: bool = False
    queued: int = 0
    superseded: int = 0
    recomputed: int = 0


def sync(conn: sqlite3.Connection, now: int) -> SyncResult:
    out = SyncResult()
    profile = filter_pipeline.active_profile(conn)
    config_id = filter_pipeline.default_model_config_id(conn)
    items, completeness = snapshot._current(conn)
    sha = snapshot.digest(items, completeness)
    existing = conn.execute("SELECT id FROM profile_snapshots WHERE sha256 = ?", (sha,)).fetchone()
    sid = existing[0] if existing else None
    candidates = []
    if profile is not None and config_id is not None:
        probe = sid if sid is not None else -1  # no snapshot yet: every candidate is new
        candidates = conn.execute(_CANDIDATES, (profile.id, filter_rules.RULESET_VERSION, probe, config_id,
                                                MATCH_PROMPT_VERSION)).fetchall()
    stale = conn.execute("SELECT count(*) FROM evaluations e JOIN posting_analyses pa ON pa.id = e.posting_analysis_id "
                         "JOIN jobs j ON j.id = pa.job_id WHERE e.status = 'pending' "
                         "AND (e.profile_snapshot_id IS NOT ? OR e.model_config_id IS NOT ? "
                         "     OR j.closed_at_ms IS NOT NULL OR pa.raw_posting_id <> j.current_raw_posting_id)",
                         (sid, config_id)).fetchone()[0]
    if sid is None or candidates or stale:
        def go():
            nonlocal sid
            sid, out.snapshot_created = snapshot.ensure(conn, now)
            out.superseded += conn.execute(_STALE_PENDING, (sid, -1 if config_id is None else config_id)).rowcount
            for analysis_id, job_id, override in candidates:
                out.superseded += conn.execute(
                    "UPDATE evaluations SET status = 'superseded' WHERE job_id = ? AND status = 'pending'", (job_id,)).rowcount
                # The same (analysis, snapshot, config, prompt) superseded earlier is queued again
                # (the unique key allows one row per tuple; a superseded row holds no results).
                revived = conn.execute(
                    "UPDATE evaluations SET status = 'pending', attempts = 0, last_error = NULL, lease_until_ms = NULL "
                    "WHERE posting_analysis_id = ? AND profile_snapshot_id = ? AND model_config_id = ? AND match_prompt_version = ? "
                    "AND status = 'superseded'", (analysis_id, sid, config_id, MATCH_PROMPT_VERSION)).rowcount
                if not revived:
                    conn.execute(
                        "INSERT INTO evaluations (posting_analysis_id, job_id, profile_snapshot_id, model_config_id, match_prompt_version, "
                        "requested_by, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (analysis_id, job_id, sid, config_id, MATCH_PROMPT_VERSION, "override" if override else "pipeline", now))
                cfg = load_config(conn, config_id)
                if cfg is not None:
                    title = conn.execute("SELECT title FROM jobs WHERE id = ?", (job_id,)).fetchone()[0] or ""
                    conn.execute("UPDATE evaluations SET request_sha256 = ? WHERE posting_analysis_id = ? AND profile_snapshot_id = ? "
                                 "AND model_config_id = ? AND match_prompt_version = ? AND status = 'pending'",
                                 (prepare(conn, analysis_id, sid, title, cfg).sha, analysis_id, sid, config_id, MATCH_PROMPT_VERSION))
                out.queued += 1
            if out.queued:
                _event(conn, "evaluations_queued", None, {"count": out.queued, "snapshot_id": sid,
                                                          "superseded": out.superseded}, now)
        _tx(conn, go)
    out.snapshot_id = sid
    if profile is not None:
        out.recomputed = recompute(conn, profile, now)
    return out


def recompute(conn: sqlite3.Connection, profile, now: int, rule_version: str | None = None) -> int:
    """Recommendations for the active profile and rule version where missing. Code only.
    `rule_version` other than the active one is for side-by-side measurement (scripts)."""
    rule_version = rule_version or VERDICT_RULE_VERSION
    todo = [r for r in conn.execute(
        _LATEST_DONE + " AND NOT EXISTS (SELECT 1 FROM recommendations rc WHERE rc.evaluation_id = e.id "
        "AND rc.search_profile_id = ? AND rc.verdict_rule_version = ?) ORDER BY e.id",
        (*latest_done_params(conn, profile), profile.id, rule_version))]
    done = 0
    for i in range(0, len(todo), RECOMPUTE_CHUNK):
        chunk = todo[i:i + RECOMPUTE_CHUNK]
        _tx(conn, lambda: [write_recommendation(conn, eid, job_id, profile, now, rule_version) for eid, job_id in chunk])
        done += len(chunk)
    return done


# ----- requirements and matches as E5 reads them -----

def load_reqs(conn: sqlite3.Connection, analysis_id: int) -> list[facts.Req]:
    return [facts.Req(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], r[10], tuple(json.loads(r[11])))
            for r in conn.execute(
                "SELECT id, text, jd_quote, sentence, eligibility_type, final_class, interpretation_status, origin, cue_polarity, "
                "min_years, section_label, flags FROM requirements WHERE posting_analysis_id = ? ORDER BY id", (analysis_id,))]


def load_matches(conn: sqlite3.Connection, evaluation_id: int) -> dict[int, validate.Validated]:
    out = {}
    for r in conn.execute("SELECT id, requirement_id, matched_by, match_type, basis, bridge, transfer_limits, gap_size, "
                          "gap_materiality, validation_flags FROM requirement_matches WHERE evaluation_id = ?", (evaluation_id,)):
        cites = [c[0] for c in conn.execute("SELECT item_id FROM match_citations WHERE requirement_match_id = ? ORDER BY item_id", (r[0],))]
        out[r[1]] = validate.Validated(r[1], r[2], r[3], r[4], cites, r[5], r[6], json.loads(r[7]) if r[7] else None, r[8],
                                       json.loads(r[9]))
    return out


def split(reqs: list[facts.Req]) -> tuple[list[facts.Req], list[facts.Req]]:
    """(matched by code against facts, sent to the model)."""
    by_code = [r for r in reqs if r.eligibility_type and r.final_class not in ("context", "negated")
               and (r.interpretation_status in assess.FIT_STATUSES or r.origin == "unextracted_eligibility")]
    by_model = [r for r in reqs if r.origin == "model" and not r.eligibility_type and r.final_class in FIT_CLASSES
                and r.interpretation_status in assess.FIT_STATUSES]
    return by_code, by_model


def job_facts(conn: sqlite3.Connection, job_id: int, profile) -> assess.JobFacts:
    view, _, _ = filter_pipeline.job_view(conn, job_id)
    # The filter's own reading of location, work mode and salary under this profile, so
    # opportunity value never waits for a re-filter to land.
    return assess.JobFacts(view.title, view.posted_at, filter_rules.evaluate(view, profile.compiled))


def write_recommendation(conn: sqlite3.Connection, evaluation_id: int, job_id: int, profile, now: int,
                         rule_version: str | None = None) -> str:
    """E5 + E6 for one evaluation under `profile`. Caller holds the transaction."""
    rule_version = rule_version or VERDICT_RULE_VERSION
    analysis_id, snapshot_id = conn.execute("SELECT posting_analysis_id, profile_snapshot_id FROM evaluations WHERE id = ?",
                                            (evaluation_id,)).fetchone()
    rules_text = conn.execute("SELECT rules FROM search_profiles WHERE id = ?", (profile.id,)).fetchone()[0]
    reqs = load_reqs(conn, analysis_id)
    snap = snapshot.load(conn, snapshot_id)

    def alt_check(r: facts.Req, other_type: str) -> facts.FactCheck:
        # The evaluated snapshot's facts, re-checked under the model's eligibility type (code only).
        return facts.check(replace(r, eligibility_type=other_type), snap.facts(), snap.completeness, reqs)

    a = assess.assess(reqs, load_matches(conn, evaluation_id), job_facts(conn, job_id, profile),
                      json.loads(rules_text), assess.load_rules(rule_version), now, alt_check)
    conn.execute(
        "INSERT INTO recommendations (evaluation_id, search_profile_id, verdict_rule_version, eligibility, eligibility_reasons, fit, "
        "fit_counts, opportunity, opportunity_factors, recommendation, decisive_reasons, rank_key, computed_at_ms) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (evaluation_id, search_profile_id, verdict_rule_version) DO NOTHING",
        (evaluation_id, profile.id, rule_version, a.eligibility, json.dumps(a.eligibility_reasons), a.fit,
         json.dumps(a.fit_counts), a.opportunity, json.dumps(a.opportunity_factors), a.recommendation,
         json.dumps(a.decisive_reasons), a.rank_key, now))
    _event(conn, "recommendation_computed", job_id, {"evaluation_id": evaluation_id, "recommendation": a.recommendation,
                                                     "eligibility": a.eligibility, "fit": a.fit, "opportunity": a.opportunity}, now)
    return a.recommendation


# ----- the E3 request and reuse of a stored answer -----

@dataclass
class Prepared:
    """Everything one evaluation needs before (and instead of) a model call."""
    reqs: list[facts.Req]
    by_code: list[facts.Req]
    by_model: list[facts.Req]
    snap: snapshot.Snapshot
    msgs: list[dict] = field(default_factory=list)
    fmt: dict | None = None
    labels: dict[str, int] = field(default_factory=dict)
    sha: str | None = None   # None: no model call is needed (nothing to match, or no profile items)


def prepare(conn: sqlite3.Connection, analysis_id: int, snapshot_id: int, title: str, cfg: ModelConfig) -> Prepared:
    reqs = load_reqs(conn, analysis_id)
    by_code, by_model = split(reqs)
    snap = snapshot.load(conn, snapshot_id)
    p = Prepared(reqs, by_code, by_model, snap)
    experience = snap.experience()
    if by_model and experience:
        p.msgs, p.labels = match.messages(title, by_model, experience, cfg.structured_output_mode)
        p.fmt = match.response_format(cfg.structured_output_mode)
        body = request_body(cfg, p.msgs, p.fmt)
        p.sha = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return p


def stored_answer(conn: sqlite3.Connection, sha: str, model_config_id: int) -> dict | None:
    """The newest done evaluation that sent exactly this request to this config, and its answer.
    Only answers that finished (`stop`) and parsed count: a done evaluation implies both."""
    row = conn.execute("SELECT id, raw_response FROM evaluations WHERE request_sha256 = ? AND model_config_id = ? "
                       "AND status = 'done' ORDER BY id DESC LIMIT 1", (sha, model_config_id)).fetchone()
    if row is None or not row[1]:
        return None
    raw = json.loads(row[1])
    ans = raw.get("reused") or next((a for a in reversed(raw.get("attempts") or [])
                                     if a.get("finish_reason") == "stop" and "error" not in a), None)
    if not ans:
        return None
    return {"from_evaluation_id": raw.get("reused_from", row[0]), "content": ans["content"], "model": ans.get("model"),
            "usage": ans.get("usage")}


def claim_reusable(conn: sqlite3.Connection, now: int, lease_ms: int) -> Claim | None:
    """Claim the oldest pending evaluation whose exact request was already answered. No model call
    follows, so this runs whether or not inference is up."""
    def go():
        row = conn.execute(
            "UPDATE evaluations SET status = 'running', lease_until_ms = ? WHERE id = (SELECT e.id FROM evaluations e "
            "JOIN model_configs mc ON mc.id = e.model_config_id WHERE e.status = 'pending' AND mc.enabled = 1 "
            "AND e.request_sha256 IS NOT NULL AND EXISTS (SELECT 1 FROM evaluations d WHERE d.status = 'done' "
            "AND d.request_sha256 = e.request_sha256 AND d.model_config_id = e.model_config_id) ORDER BY e.id LIMIT 1) "
            "RETURNING id, posting_analysis_id, job_id, profile_snapshot_id, model_config_id", (now + lease_ms,)).fetchone()
        if row is None:
            return None
        title = conn.execute("SELECT title FROM jobs WHERE id = ?", (row[2],)).fetchone()[0]
        return Claim(row[0], row[1], row[2], row[3], row[4], title or "")
    return _tx(conn, go)


# ----- claim / persist / release -----

@dataclass(frozen=True)
class Claim:
    evaluation_id: int
    analysis_id: int
    job_id: int
    snapshot_id: int
    model_config_id: int
    title: str


def pending_config_ids(conn: sqlite3.Connection) -> tuple[list[int], int]:
    ready = [r[0] for r in conn.execute(
        "SELECT e.model_config_id FROM evaluations e JOIN model_configs mc ON mc.id = e.model_config_id "
        "WHERE e.status = 'pending' AND mc.enabled = 1 GROUP BY e.model_config_id ORDER BY min(e.id)")]
    waiting = conn.execute("SELECT count(*) FROM evaluations e JOIN model_configs mc ON mc.id = e.model_config_id "
                           "WHERE e.status = 'pending' AND mc.enabled = 0").fetchone()[0]
    return ready, waiting


def claim(conn: sqlite3.Connection, model_config_id: int, now: int, lease_ms: int) -> Claim | None:
    def go():
        row = conn.execute(
            "UPDATE evaluations SET status = 'running', lease_until_ms = ? WHERE id = (SELECT id FROM evaluations "
            "WHERE status = 'pending' AND model_config_id = ? ORDER BY id LIMIT 1) "
            "RETURNING id, posting_analysis_id, job_id, profile_snapshot_id", (now + lease_ms, model_config_id)).fetchone()
        if row is None:
            return None
        title = conn.execute("SELECT title FROM jobs WHERE id = ?", (row[2],)).fetchone()[0]
        return Claim(row[0], row[1], row[2], row[3], model_config_id, title or "")
    return _tx(conn, go)


def _insert_match(conn, evaluation_id: int, snapshot_id: int, v: validate.Validated) -> int:
    mid = conn.execute(
        "INSERT INTO requirement_matches (evaluation_id, requirement_id, matched_by, match_type, basis, bridge, transfer_limits, "
        "gap_size, gap_materiality, validation_flags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (evaluation_id, v.requirement_id, v.matched_by, v.match_type, v.basis, v.bridge, v.transfer_limits,
         json.dumps(v.gap_size) if v.gap_size is not None else None, v.gap_materiality, json.dumps(v.flags))).fetchone()[0]
    for item_id in v.citations:
        conn.execute("INSERT INTO match_citations (requirement_match_id, snapshot_id, item_id) VALUES (?, ?, ?)",
                     (mid, snapshot_id, item_id))
    return mid


def store_model_match(conn, c: Claim, req: facts.Req, m, snap, completeness, rules, now) -> validate.Validated:
    """Insert one model match. A citation outside the snapshot fails the FK; the match is then
    validated again without it (an inferred claim or invented id can never cover anything)."""
    rejected: set[int] = set()
    while True:
        v = validate.model_match(req, m, snap, completeness, rules, now, frozenset(rejected))
        conn.execute("SAVEPOINT match")
        try:
            _insert_match(conn, c.evaluation_id, c.snapshot_id, v)
        except sqlite3.IntegrityError as exc:
            conn.execute("ROLLBACK TO match")
            conn.execute("RELEASE match")
            if "FOREIGN KEY" not in str(exc).upper():
                raise
            bad = [i for i in v.citations if conn.execute(
                "SELECT 1 FROM profile_snapshot_items WHERE snapshot_id = ? AND item_id = ?", (c.snapshot_id, i)).fetchone() is None]
            if not bad:
                raise
            rejected.update(bad)
            continue
        conn.execute("RELEASE match")
        return v


def persist_done(conn: sqlite3.Connection, c: Claim, reqs: list[facts.Req], fact_matches: list[validate.Validated],
                 model_matches: dict[int, object], skipped: dict[int, str], claims: list, raw_response: dict,
                 model_reported: str | None, now: int, request_sha: str | None = None) -> dict:
    """Matches, citations, inferred claims, status done, then the recommendation, in one transaction."""
    snap = snapshot.load(conn, c.snapshot_id)
    rules = assess.load_rules(VERDICT_RULE_VERSION)
    by_id = {r.id: r for r in reqs}

    def go():
        updated = conn.execute(
            "UPDATE evaluations SET status = 'done', raw_response = ?, model_id_reported = ?, attempts = attempts + 1, "
            "lease_until_ms = NULL, last_error = NULL, request_sha256 = ? WHERE id = ? AND status = 'running'",
            (json.dumps(raw_response), model_reported, request_sha, c.evaluation_id)).rowcount
        if updated != 1:
            raise RuntimeError(f"evaluation {c.evaluation_id} is no longer running")
        out = []
        for v in fact_matches:
            _insert_match(conn, c.evaluation_id, c.snapshot_id, v)
            out.append(v)
        for rid, m in model_matches.items():
            out.append(store_model_match(conn, c, by_id[rid], m, snap, snap.completeness, rules, now))
        for rid, why in skipped.items():
            v = validate.not_sent(by_id[rid], why)
            _insert_match(conn, c.evaluation_id, c.snapshot_id, v)
            out.append(v)
        added = _store_claims(conn, c.evaluation_id, claims, raw_response.get("labels") or {}, now)
        counts = {t: sum(v.match_type == t for v in out) for t in ("direct", "transferable", "gap", "unknown")}
        counts.update(matches=len(out), by_fact=len(fact_matches), citations_rejected=sum(v.has_flag("citation_rejected") for v in out),
                      downgraded=sum(v.has_flag("downgraded") for v in out), inferred_claims=added)
        _event(conn, "evaluation_done", c.job_id, {"evaluation_id": c.evaluation_id, **counts}, now)
        profile = filter_pipeline.active_profile(conn)
        rec = write_recommendation(conn, c.evaluation_id, c.job_id, profile, now) if profile is not None else None
        return {**counts, "recommendation": rec}
    return _tx(conn, go)


def _store_claims(conn, evaluation_id: int, claims: list, labels: dict[str, int], now: int) -> int:
    """Inbox entries; the requirements they were proposed for are stored as requirement ids
    (the R labels mean nothing outside that one prompt)."""
    known = {norm(r[0]) for r in conn.execute("SELECT statement FROM inferred_claims")}
    added = 0
    for cl in claims[:MAX_INFERRED_CLAIMS]:
        key = norm(cl.statement)
        if not key or key in known:
            continue
        known.add(key)
        conn.execute("INSERT INTO inferred_claims (statement, kind, rationale, evidence, proposed_by_evaluation_id, created_at_ms) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (cl.statement.strip()[:500], cl.kind, cl.rationale.strip()[:1000],
                      json.dumps({"requirement_ids": [labels[r] for r in cl.requirements if r in labels]}), evaluation_id, now))
        added += 1
    return added


def persist_failed(conn: sqlite3.Connection, c: Claim, kind: str, detail: str, raw_response: dict | None, now: int) -> None:
    def go():
        conn.execute("UPDATE evaluations SET status = 'failed', attempts = attempts + 1, lease_until_ms = NULL, last_error = ?, "
                     "raw_response = ? WHERE id = ?",
                     (f"{kind}: {detail}"[:1000], json.dumps(raw_response) if raw_response is not None else None, c.evaluation_id))
        _event(conn, "evaluation_failed", c.job_id, {"evaluation_id": c.evaluation_id, "error": kind}, now)
    _tx(conn, go)


def release(conn: sqlite3.Connection, c: Claim, *, spend_attempt: bool, error: str | None, now: int) -> str:
    def go():
        attempts = conn.execute("SELECT attempts FROM evaluations WHERE id = ?", (c.evaluation_id,)).fetchone()[0]
        attempts += int(spend_attempt)
        newer = conn.execute("SELECT 1 FROM evaluations WHERE job_id = ? AND status = 'pending' AND id <> ?",
                             (c.job_id, c.evaluation_id)).fetchone() is not None
        status = "superseded" if newer else ("failed" if attempts >= MAX_ATTEMPTS else "pending")
        conn.execute("UPDATE evaluations SET status = ?, attempts = ?, lease_until_ms = NULL, last_error = ? WHERE id = ?",
                     (status, attempts, error, c.evaluation_id))
        if status == "failed":
            _event(conn, "evaluation_failed", c.job_id, {"evaluation_id": c.evaluation_id, "error": "attempts_exhausted"}, now)
        return status
    return _tx(conn, go)


# ----- reads -----

def counts(conn: sqlite3.Connection) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT status, count(*) FROM evaluations GROUP BY status")}


def _rec_row(r) -> dict:
    d = dict(r)
    for k in ("eligibility_reasons", "fit_counts", "opportunity_factors", "decisive_reasons"):
        d[k] = json.loads(d[k])
    return d


def job_recommendation(conn: sqlite3.Connection, job_id: int) -> dict | None:
    """The job's current recommendation (active profile, current rule version) with its evidence table."""
    profile = filter_pipeline.active_profile(conn)
    if profile is None:
        return None
    row = conn.execute(_LATEST_DONE + " AND e.job_id = ?", (*latest_done_params(conn, profile), job_id)).fetchone()
    if row is None:
        return None
    eid = row[0]
    rec = conn.execute("SELECT * FROM recommendations WHERE evaluation_id = ? AND search_profile_id = ? AND verdict_rule_version = ?",
                       (eid, profile.id, VERDICT_RULE_VERSION)).fetchone()
    ev = dict(conn.execute("SELECT id, posting_analysis_id, profile_snapshot_id, model_config_id, model_id_reported, "
                           "match_prompt_version, status, created_at_ms FROM evaluations WHERE id = ?", (eid,)).fetchone())
    matches = load_matches(conn, eid)
    table = []
    for r in load_reqs(conn, ev["posting_analysis_id"]):
        m = matches.get(r.id)
        table.append({"requirement_id": r.id, "quote": r.jd_quote, "class": r.final_class, "status": r.interpretation_status,
                      "origin": r.origin, "eligibility_type": r.eligibility_type,
                      "match": None if m is None else {"matched_by": m.matched_by, "match_type": m.match_type, "basis": m.basis,
                                                       "citations": m.citations, "bridge": m.bridge,
                                                       "transfer_limits": m.transfer_limits, "gap_size": m.gap_size,
                                                       "gap_materiality": m.gap_materiality, "flags": m.flags}})
    pending = conn.execute("SELECT count(*) FROM evaluations WHERE job_id = ? AND status IN ('pending', 'running')", (job_id,)).fetchone()[0]
    sid, cfg = latest_done_params(conn, profile)[:2]
    return {"job_id": job_id, "evaluation": ev, "recommendation": _rec_row(rec) if rec else None, "evidence": table,
            "current": ev["profile_snapshot_id"] == sid and ev["model_config_id"] == cfg,
            "newer_evaluation_pending": bool(pending)}


def recommendation_list(conn: sqlite3.Connection, category: str | None, limit: int = 500) -> list[dict]:
    """Each row says whether it was computed against the profile as it is now (`current`) and
    whether a re-evaluation is waiting: after an edit the last result is shown until the new one
    lands, labelled, never passed off as current (Codex adversarial review, P1.7)."""
    profile = filter_pipeline.active_profile(conn)
    if profile is None:
        return []
    params = latest_done_params(conn, profile)
    rows = conn.execute(
        "SELECT rc.recommendation, rc.eligibility, rc.fit, rc.opportunity, rc.rank_key, rc.decisive_reasons, j.id AS job_id, "
        "j.title, j.company, j.location_text, rc.evaluation_id, "
        "(e.profile_snapshot_id = ? AND e.model_config_id = ?) AS current, "
        "EXISTS (SELECT 1 FROM evaluations ep WHERE ep.job_id = j.id AND ep.status IN ('pending', 'running')) AS newer_evaluation_pending "
        "FROM (" + _LATEST_DONE + ") le JOIN evaluations e ON e.id = le.id "
        "JOIN recommendations rc ON rc.evaluation_id = le.id AND rc.search_profile_id = ? AND rc.verdict_rule_version = ? "
        "JOIN jobs j ON j.id = le.job_id WHERE (? IS NULL OR rc.recommendation = ?) "
        "ORDER BY CASE rc.recommendation WHEN 'APPLY' THEN 0 WHEN 'STRETCH' THEN 1 WHEN 'REVIEW' THEN 2 ELSE 3 END, "
        "rc.rank_key DESC, j.id LIMIT ?",
        (params[0], params[1], *params, profile.id, VERDICT_RULE_VERSION, category, category, limit)).fetchall()
    return [{**dict(r), "decisive_reasons": json.loads(r["decisive_reasons"]), "current": bool(r["current"]),
             "newer_evaluation_pending": bool(r["newer_evaluation_pending"])} for r in rows]


def review_questions(conn: sqlite3.Connection) -> list[dict]:
    """REVIEW questions grouped across jobs, so one answer (a new fact) resolves many (6.5 noise control)."""
    groups: dict[tuple, dict] = {}
    for rec in recommendation_list(conn, "REVIEW"):
        for reason in rec["decisive_reasons"]:
            for q in reason.get("questions", []):
                key = (q["kind"], q.get("type")) if q["kind"] == "eligibility" else (q["kind"], q["question"])
                g = groups.setdefault(key, {"kind": q["kind"], "type": q.get("type"), "example": q["question"], "jobs": []})
                g["jobs"].append({"job_id": rec["job_id"], "title": rec["title"], "company": rec["company"],
                                  "question": q["question"], "requirement_id": q.get("requirement_id"),
                                  "current": rec["current"], "newer_evaluation_pending": rec["newer_evaluation_pending"]})
    return sorted(groups.values(), key=lambda g: -len(g["jobs"]))
