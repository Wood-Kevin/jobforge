# JobForge

Windows desktop job discovery and evaluation app: Tauri host + Python background agent + SQLite.

Source of truth (read before changing anything):
- `SPEC.md` — the AGREED contract. Code that disagrees with it is drift; fix the spec first if the spec is wrong.
- `docs/ARCHITECTURE.md` — reasoning, schema detail, rules, spike evidence (Appendices A, B).
- `DEFERRED_COMPLEXITY.md` — parked work with triggers.

Layout: `agent/` (Python agent, all business logic), `desktop/` (Tauri host + React UI, added in P1.2), `spikes/` (measurement code, not app code).

Agent commands (from `agent/`):
- Tests: `uv run pytest -q`
- Run: `uv run python -m jobforge_agent` (refuses to run elevated, exit 5)

Rules that are easy to break (each has a test): DB-touching IPC handlers are `async def`; FastAPI types imported at module level; every connection comes from `db.connect.open_connection` with a component name (authorizer allow-lists); append-only tables are protected by triggers; no transaction spans a network or model call; posting analysis (`analysis/`, `inference/`) never reads profile tables; no enum value appears in two fields of a model output schema; the E1 prompt and schema are pinned to `EXTRACTION_PROMPT_VERSION` by a hash (bump the version, also when E0/E2 behavior changes); the E3 prompt and schema likewise to `MATCH_PROMPT_VERSION` (bump also when E4 validation changes); verdict thresholds live in `evaluation/verdict_rules/vN.yaml` and any E5/E6 change is a new `VERDICT_RULE_VERSION` (recompute, no model calls); requirements with an eligibility type are matched by code (`evaluation/facts.py`), never sent to the model; citations go through the `match_citations` FK, never pre-filtered in code; SKIP only via rules 1, 2, 5 (property test in `tests/test_verdict.py`).
