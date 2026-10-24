# JobForge handoff (2026-10-24, end of session)

For the next session. Read `CLAUDE.md`, then `SPEC.md` (change log at the bottom, newest first),
`docs/ARCHITECTURE.md` and `DEFERRED_COMPLEXITY.md`. This file says where things stand and what is
next; those files hold the decisions and their reasons. No personal profile data belongs in the repo.

## State

- **Installed and running:** v0.3.13 (unelevated), from `main` at `eaa8828` (fast-forwarded from
  `ux-visual-foundation`, which carried the unmerged 0.3.10 and 0.3.11 commits too).
- **Open branch not merged:** `audit-fixes` (interp-v4, rules v4: proposed, not active; an evaluation change,
  so before/after plus Kevin's approval first). Its window commits were rebased into 0.3.10/0.3.11.
- **Versions:** E1 prompt `e1-v1`, segmenter `seg-v1`, interpreter `interp-v3`, E3 prompt `m1-v1`,
  verdict rules `v3` (v2 thresholds; v1 and v2 kept for comparison). Unchanged by the UX redesign.
- **Tests:** agent 770 (`uv run pytest -q` in `agent/`), desktop 39 (`npx vitest run` in `desktop/`).
- **the live data (read after installing v0.3.13):** 10 APPLY, 4 STRETCH, 11 REVIEW (at 0.3.12: 9 of
  the REVIEW jobs had a question, 2 only lines JobForge couldn't settle); 255 open suggestions in the inbox
  (untouched). The search ignores postings older than 14 days.
- **Accuracy tools are off by default since 0.3.13:** Your call and Technical details are hidden until the
  switch under "What you're looking for" is ticked. Kevin was told; check before relying on new golden labels.
- **Review copy:** `%LOCALAPPDATA%\jobforge-uxreview` holds a copy of the real data (about 300 MB) used for
  screenshot passes; test actions changed it (a skip and undo, a "Saved" status, 2 unconfirm/confirm, 250
  guesses turned down, two search saves). Never treat it as the live data. Kevin may ask to delete it.

## UX redesign (v0.3.12 -> v0.3.13)

Kevin is the non-technical user the window is designed for, and plans to share the app. Mockup (private
artifact): https://claude.ai/artifact/CY4F2pytKd5gs5952vMNpj. Five steps, each a SPEC change-log row:
1. Visual foundation: tokens, type scale, grouped menu with counts, page headers, keyboard fixes.
2. Job rows (pay, fit bar, why line, Sort menu, Undo toast) and the job page (one header, Your progress,
   verdict block, What you bring / Still open / Nice to have). `/v1/jobs` rows gained `salary` and `fit_counts`.
3. Wording: facts as sentences (`facts.ts`), "Asked for", "One or two gaps", Status's Needs-your-input split,
   the accuracy-tools switch (`viewPrefs.ts`).
4. Setup as a one-step guide; Profile summary and Confirm all (experience items only).
5. Search form: essentials + Fine-tune, tags, state menu, drafts (`draft.ts`); LM Studio checklist;
   suggestions 20 at a time and Turn down all.
Parked items: DEFERRED_COMPLEXITY.md, "UX redesign 2026-10-24" and the updated "UX review" section.

## Earlier this day (v0.3.1 -> v0.3.11)

See SPEC.md's change log for each row. In short:
- Exact-copy collapse in lists; "Open job posting" opens the listing.
- **Skip:** "I'd skip it" on rows and the job page records a SKIP call and moves the job (and copies) to
  "Skipped by you"; Undo withdraws it.
- Job list: "Everything found" tab and Eligibility column removed; column sorting; filters (fit,
  pay/place/level, reason, found within).
- Job page: headline built from evidence counts; "Pay, location and level" wording separates "Not stated"
  (posting) from "No preference set" (search); opportunity labels Favorable/Mixed/Unfavorable.
- "Needs your input" counts jobs, shared questions first; **Answer here** on that page and on the job
  page's question box: an empty item form in the user's own words (never prefilled from the posting), or
  the fact's typed form. Misread eligibility lines offer the fact form.
- New eligibility fact `availability` (on-call, weekends, holidays, night/rotating shifts, other time zone
  hours) and residency `time_zone`.
- **Reading fix (calibration cases E1-E2), approved by Kevin:** interp-v3 types hours/shift/on-call lines
  `availability` and zone/remote-region lines `residency`; rules v3 check them against facts. A new
  interpreter version re-reads each posting's stored E1 answer (no model call), and agent startup queues
  current-version analyses for passing jobs. Evidence: `docs/calibration/reading_fix_before_after.txt`.
  After Kevin set time zone and availability: Nash, Bridgeway x2 and PDI moved REVIEW -> APPLY.

## Rules for the next session (Kevin's, binding)

- **Evaluation changes** (E1-E6 prompts, reading rules, fact checks, verdict rules): build on a branch,
  run a before/after on a copy of real data with the noise baseline beside it, keep the regression
  cases in `docs/calibration/calibration_1_cases.json` holding, show the results, and merge or deploy only
  on his explicit approval. Manual "Your call" labels are optional. Applications and "I'd skip it" are
  high-confidence preference signals, saved is weak, views and inactivity are not verdicts; keep
  preference separate from qualification/eligibility accuracy. On-call is an availability fact, never a
  negative job attribute. (DEFERRED_COMPLEXITY.md, "Evaluation calibration 1".)
- Never verify eligibility facts for Kevin, never infer profile experience, never write a profile
  statement for him unless he dictates it. Generated claims only go to the inbox.
- Installs and builds need his go-ahead in the conversation; commit when he asks (he usually says
  "commit it"). Restate the goal of a non-trivial step first: twice this session a request was misread
  ("Needs your input" meant the job page's question box, then both), so confirm which screen.
- real data (`%LOCALAPPDATA%\dev.kevin.jobforge`) is read-only for checks; changes only through
  the app's own API when he asks. Drills use a separate `JOBFORGE_DATA_DIR`.

## Next work (none started)

1. **Calibration 1, the matching items** (DEFERRED_COMPLEXITY.md, "Evaluation calibration 1"): `direct`
   overclaims D1-D7, dropped qualifiers Q1-Q3, years tolerance T1 (2 years absolute makes any shortfall
   against a 2-year requirement minor), F1 (APPLY with zero direct matches), Y1-Y2 (gap text contradicts
   computed years). Same branch + before/after + approval process as the reading fix.
2. **Noise baseline, second half:** re-send identical E3 requests with reuse bypassed to separate sampling
   noise from sensitivity to profile text (11-14% of untouched lines moved when profile text changed).
3. **Preference scorecard (not built):** count applications and skips against JobForge's recommendation
   per rule version, kept apart from accuracy.
4. **"No" / "Not sure" answers** and grouping questions by concept: an evaluation change (absence would
   become a gap), so it follows rule 1 above.
5. **Slow big lists:** "Filtered out" takes about 720 ms over ~122k rows; fix is a stored copy-group key
   (DEFERRED, "Grouping makes the big job lists slower").
6. Job 128056: its interp-v2 analysis failed (budget_exhausted at max_tokens 4000); it needs an E1 call.

## How to

- **Screenshots of the real window** (used for every UX step): only one JobForge runs at a time (single-instance
  plugin), so exit the installed one (`JobForge.exe --exit`, unelevated via `explorer.exe` and a .cmd), then
  start `desktop\src-tauri\target\release\JobForge.exe` from a .cmd that sets `JOBFORGE_DATA_DIR` (the review
  copy), `JOBFORGE_AGENT_EXE`/`JOBFORGE_AGENT_ARGS` (agent venv), `JOBFORGE_BUILT_IN_LIST=0` and
  `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9333`; pause discovery through the copy's API.
  Drive and capture over the DevTools protocol (Node 24's built-in WebSocket: `Runtime.evaluate`,
  `Page.captureScreenshot`, `Emulation.setDeviceMetricsOverride` 1280x860). Afterwards exit it and start the
  installed one with `--background`; the installer also leaves JobForge stopped, so start it the same way.
  Screenshots with the real profile stay out of the repo.
- **Build and install** (Windows): bump the version in the 8 files the version commits touch
  (`agent/pyproject.toml`, `agent/src/jobforge_agent/__init__.py`, `agent/uv.lock`, `desktop/package.json`,
  `desktop/package-lock.json` x2, `desktop/src-tauri/Cargo.toml`, `Cargo.lock`, `tauri.conf.json`), run
  `scripts/build.ps1` from a non-elevated PowerShell with `%USERPROFILE%\.cargo\bin` on PATH, then install
  unelevated through `explorer.exe` running a .cmd with `start "" /wait "...\JobForge_X_setup.exe" /S`.
  Verify: `agent_runs` newest row has the version and `elevated = 0`; `logs/agent.jsonl` has no
  `"level": "error"`. The build's layout check names the active verdict rules file (now v3).
- **Before/after for an evaluation change:** copy the live DB with the SQLite backup API into the
  scratchpad; `git -c core.longpaths=true worktree add <scratch>/mw main`; run
  `scripts/compare_reading_fix.py run DB OUT.json` once with `PYTHONPATH=<worktree>/agent/src` (before) and
  once with the branch's `agent/src` (after), then `... diff BEFORE AFTER`. Check the before run reproduces
  the stored recommendations. `scripts/rollout_dry_run.py` simulates the rollout with a model server that
  counts calls. Remove the worktree with `rd /s /q \\?\path` (long paths).
- **Evidence:** `bash verify.sh [sections]` (raw output, no verdicts). The analysis section shows analyses
  by interpreter version, reuse, and eligibility lines still misread.

## Gotchas

- SPEC.md, ARCHITECTURE.md, DEFERRED_COMPLEXITY.md and build.ps1 are CRLF: edit with byte-level
  replacements or the Edit tool. Python patch scripts in heredocs mangle backslashes in regexes and Windows
  paths; use the Edit tool for those.
- Tests that pin the active rule version should use `VERDICT_RULE_VERSION`, not a literal.
- `extract.E1_ELIGIBILITY_TYPES` is fixed with prompt e1-v1; adding an E2 type must not change the E1
  schema (its hash is pinned). `items.AWAITING_CUES` lets a fact type exist before its cues.
- A profile edit makes every passing job re-match with the model; expect 11-14% of untouched lines to move.
