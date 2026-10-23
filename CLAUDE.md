# JobForge

Windows desktop job discovery and evaluation app: Tauri host + Python background agent + SQLite.

Source of truth (read before changing anything):
- `SPEC.md` — the AGREED contract. Code that disagrees with it is drift; fix the spec first if the spec is wrong.
- `docs/ARCHITECTURE.md` — reasoning, schema detail, rules, spike evidence (Appendices A, B).
- `DEFERRED_COMPLEXITY.md` — parked work with triggers.

Layout: `agent/` (Python agent, all business logic), `desktop/` (Tauri host + React UI, added in P1.2), `spikes/` (measurement code, not app code).

Desktop commands (from `desktop/`; cargo and Tauri via PowerShell): `npm run build` (type-check + bundle), `npx vitest run` (sanitizer tests), `cargo test` in `src-tauri`, `npx tauri build --no-bundle` (host exe; run against a data copy with `JOBFORGE_DATA_DIR`, `JOBFORGE_AGENT_EXE`=agent venv python, `JOBFORGE_AGENT_ARGS=-m jobforge_agent`, started unelevated).

Agent commands (from `agent/`):
- Tests: `uv run pytest -q`
- Run: `uv run python -m jobforge_agent` (refuses to run elevated, exit 5)
- Evidence: `bash verify.sh [sections]` from the repo root (Git Bash; raw output, no verdicts; `VERIFY_DATA_DIR` for another data root). Update the matching probe whenever a probed behavior changes.
- Golden-set report: `uv run python -m jobforge_agent golden` (read only; `--suite interpretation` calls the default model)
- Installers: `scripts\build.ps1` from a non-elevated PowerShell writes `dist-installers\JobForge_<version>_setup.exe`

Rules that are easy to break (each has a test): DB-touching IPC handlers are `async def`; FastAPI types imported at module level; every connection comes from `db.connect.open_connection` with a component name (authorizer allow-lists); append-only tables are protected by triggers; no transaction spans a network or model call; posting analysis (`analysis/`, `inference/`) never reads profile tables; no enum value appears in two fields of a model output schema; the E1 prompt and schema are pinned to `EXTRACTION_PROMPT_VERSION` by a hash (E0/E2 behavior changes bump `SEGMENTER_VERSION` / `INTERPRETER_VERSION` in `analysis/versions.py`); the E3 prompt and schema likewise to `MATCH_PROMPT_VERSION` (bump also when E4 validation changes); verdict thresholds live in `evaluation/verdict_rules/vN.yaml` and any E5/E6 change is a new `VERDICT_RULE_VERSION` (recompute, no model calls); requirements with an eligibility type are matched by code (`evaluation/facts.py`), never sent to the model; citations go through the `match_citations` FK, never pre-filtered in code; a stored E3 answer is reused only for a byte-identical request body (hash includes the model id), never across models; resume suggestions (`profile/suggest.py`, prompt pinned to `RESUME_PROMPT_VERSION`) go only to the `inferred_claims` inbox and are kept only when their quote is in the resume text; SKIP only via rules 1, 2, 5 (property test in `tests/test_verdict.py`); posting HTML is rendered only through `desktop/src/sanitize.ts` and links only through the host's `open_url`; every agent call from the window lives in `desktop/src/api.ts` (route contract test); the window makes no network requests of its own; tests that message agent windows must target their own agent's pid; `golden_labels` is append-only (latest row per job is the label); `--restore` keeps the live database as a `before-restore-*` backup before replacing it; a new migration file also raises `SCHEMA_VERSION` in `db/migrate.py` (test), because the agent never takes its schema from the files on disk. The built-in company list (`discovery/directory.json`, built by `scripts/build_directory.py`) only adds missing `employers` rows at startup and never changes an existing one; its switch touches only `origin = 'directory'` rows; agents started by tests or drills set `JOBFORGE_BUILT_IN_LIST=0` so they never poll real boards; a name lookup that could not reach a board reports `check_failed`, never `not_found`.
