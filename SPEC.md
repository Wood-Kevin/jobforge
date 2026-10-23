# SPEC.md — JobForge

<!--
This file is the contract between the architect (me + conversational Claude)
and the runner (Claude Code). The runner implements against this file, not
against chat memory. Drift is defined as code that disagrees with this spec.
If the spec is wrong, change the spec first, then the code. Never the reverse
silently.

Status meanings:
  DRAFT      = still being shaped in conversation, do not implement
  AGREED     = frozen enough to build against
  SUPERSEDED = replaced; link the successor
-->

**Status:** AGREED
**Date agreed:** 2026-10-21 (Kevin, in chat: "go" after the remaining defaults were listed)
**Owner:** Kevin

Reasoning, diagrams, schema detail, rules, test tables, risks, spike results
and the full question list: `docs/ARCHITECTURE.md`. This file holds only what
the runner builds against.

---

## 1. Problem

<!-- One paragraph: what problem, for whom, and which lane this build
     serves (joy, career evidence, or both). Joy builds don't need market
     permission — no validation gate here. But be honest about the lane:
     a career-evidence build with no audience in mind is neither. -->

**Problem:** An earlier job-scanning pipeline (daily ATS scanner -> Supabase
`job_lean` -> daily Claude classification) finds jobs once a day and produces
misleading qualification judgments: scores with undefined denominators (seen
in `../resume-matching-engine`), title/location false positives (logged in
`../job-scanner/SPEC.md` section 8), and classifications made from truncated
summaries. JobForge is an installable Windows desktop app whose background
agent keeps discovering postings continuously within each source's limits,
even with the window closed, filters them deterministically, evaluates them
on a local LLM with evidence that code checks, and separates three questions
(can I be hired, do I fit, is it worth it) into a recommendation of APPLY,
STRETCH, REVIEW or SKIP. Applying costs nothing, so plausible stretch roles
should surface, but every surfaced job must carry specific evidence so the
list stays readable.

**Lane:** Not tracked for this project (Kevin, 2026-10-21: "lane doesn't
matter"). Stays at the top level of `claude-projects`; no career-lane gate.

## 2. Scope

**In scope (this build = Phase 1, see docs/ARCHITECTURE.md section 9):**
- Windows 10/11 x64 desktop app: Tauri 2 host (tray, window, agent
  supervision, IPC forwarding) + React/TypeScript UI + Python background
  agent packaged with PyInstaller onedir, **console subsystem, started with
  `CREATE_NO_WINDOW`**, bundled by directory mapping with a layout check.
  Per-user NSIS installer (installs to `%LOCALAPPDATA%\JobForge\`), no admin,
  no Docker, Postgres or separate Python. Proven in spike P1.0d
  (ARCHITECTURE Appendix B).
- Explicit per-user behavior regardless of token (D16, Kevin 2026-10-21):
  autostart written by JobForge to `HKCU\...\Run` (no autostart plugin); the
  agent refuses to run elevated (exit 5; `--shutdown`/`--status` allowed,
  `--backup`/`--restore` refused because they create files in the data root); an elevated host relaunches itself unelevated through
  the shell and exits, and refuses with a message when that cannot be
  guaranteed (relaunch fails, or the elevated token's user differs from the
  signed-in shell user). The agent logs its elevation on every start.
- Process lifecycle per ARCHITECTURE 3.2: agent survives window close and host
  crash; attach-or-spawn; watchdog; single agent per user (OS file lock);
  optional autostart at sign-in (HKCU Run); tray with open, pause/resume
  discovery, status, autostart toggle, exit; graceful shutdown on exit,
  sign-out and installer hooks.
- SQLite (WAL, `synchronous=FULL`, `foreign_keys=ON`, STRICT tables), opened
  only by the agent, in the data root `%LOCALAPPDATA%\<identifier>\` (never
  the install directory); resumes as BLOBs; logs and backups in the same
  per-user tree; nothing written to the install directory. DB-touching IPC
  handlers are `async def`; FastAPI types imported at module level.
- Replacements for the Postgres guarantees per ARCHITECTURE 4.3: per-connection
  authorizers, append-only triggers, `BEGIN IMMEDIATE` claims, startup orphan
  recovery, FK-checked citations, in-transaction `change_events`.
- Numbered SQL migrations with `user_version`, pre-migration backup, refuse
  newer DB, restore command; daily backups.
- IPC: HTTP/JSON on ephemeral `127.0.0.1` port with a per-start bearer token
  in a user-only `ipc.json`, called only by the Rust host; SSE event stream
  relayed to the UI (Q23, default accepted 2026-10-21; named pipes deferred).
- Continuous incremental discovery (ARCHITECTURE 3.3) from Greenhouse, Lever,
  Ashby via adapters copied from `ats_scanner` with origin commit `edd1528`
  recorded; `../job-scanner` is not modified (Q3, default accepted 2026-10-21): intervals (default 15 min, floored by `min_interval_s`,
  robots Crawl-delay, Ashby's 60 s cache), ETag/304 polling, persisted poll
  state, leases, partial unique index on in-flight fetches, jitter, per-host
  budget, Retry-After, backoff, bounded catch-up, sleep/resume handling,
  persisted pause.
- Change detection by `source_job_id` + `material_sha256` / `payload_sha256`;
  closure after 2 successful complete listings without the job; supersession.
- Every posting stored, including filter rejects; re-filter; "evaluate anyway".
- Deterministic, versioned filter ruleset driven by a versioned search profile.
- Resume upload, experience items incl. eligibility facts, verify action,
  completeness flags, inferred-claims inbox.
- Evaluation E0-E6 (ARCHITECTURE 6) with automatic resumption when inference
  returns; APPLY / STRETCH / REVIEW / SKIP via the v1 rules in 6.5; E2
  eligibility-type lexicon and eligibility recall scan (6.4 checks 6-7);
  schema lint; per-config reasoning control (default off) with `max_tokens`
  and budget-exhausted handling (6.8). Default model: **gemma-4-12b-qat in
  LM Studio, reasoning disabled** (Kevin, Q4, 2026-10-21); gpt-oss-20b is a
  supported alternative config.
- Spike reference labels (`spikes/p1_0/reference.py`) are **provisional, for
  testing only** (Kevin, Q27, 2026-10-21). They do not enter the golden set
  until each spike miss has been independently reviewed (ARCHITECTURE 6.7).
- Interpretation, verdict, scheduler, lifecycle, persistence-guarantee and
  migration test suites.
- UI: discovery dashboard (freshness, errors, backoff, volume, queues,
  inference), live refresh, jobs, rejected view, REVIEW questions, profile,
  preferences, applications.
- Manual updates via installer with safe agent shutdown, migration and
  documented rollback; golden-set and interpretation reports; verify.sh.

**Explicitly out of scope:**
<!-- Anything cut mid-build goes to DEFERRED_COMPLEXITY.md per the baseline,
     but known cuts get listed here up front so the runner never "helpfully"
     builds them. -->
- Docker, PostgreSQL, any browser-hosted UI, any network listener beyond
  `127.0.0.1`.
- Windows Service mode; macOS/Linux builds.
- Auto-updater, code signing (Q22: unsigned while Kevin is the only user), named-pipe IPC.
- UI or any process other than the agent opening the database.
- Business logic in Rust.
- Shared/hosted multi-user; DB encryption.
- Any automatic job application or outbound request to an employer other
  than reading public job-board APIs.
- Any read or write to Supabase `job_lean` or changes to `../job-scanner`.
- Workday, SmartRecruiters, USAJobs, Indeed, LinkedIn, HTML scraping.
- Polling faster than a source's `min_interval_s`; claims of instant detection.
- Redis, Celery, schedulers, brokers; Alembic; LangChain; provider plugins.
- Any single numerical score or composite of the three assessments.
- A second LLM interpretation pass; LLM-judged opportunity value.
- Non-English postings; retention pruning; adaptive intervals.
- Notifications (Q10: none in Phase 1), resume tailoring, cover letters.

## 3. Architecture decisions

<!-- One block per meaningful decision. Same rule as "Justify every change"
     in the baseline, applied at design time: the shape, the why, and the
     alternative not taken. These blocks double as ADRs and as content seeds
     for the Career evidence section. -->

Decisions D1-D16 accepted by Kevin 2026-10-21. Kevin waived the
own-words restatement step for this project ("this is just slowing
development"), so the tier is kept for reference only and items are tagged
accepted, not owned.

### D1: Three separate assessments and four categories, computed by code
- **Tier:** consequential (core product mechanic). Accepted by Kevin 2026-10-21.
- **Choice:** Eligibility, qualification fit and opportunity value computed
  separately by versioned rules from a validated evidence table, mapped to
  APPLY / STRETCH / REVIEW / SKIP by the ordered rules in ARCHITECTURE 6.5.
- **Why:** Separate assessments show which question drove the outcome;
  REVIEW gives uncertainty somewhere to go other than a wrong APPLY or SKIP.
- **Rejected alternative and why:** Weighted composite or model score. Hides
  which assessment decided; small local models drift on numbers.

### D2: Inferred experience can never be cited or verified, enforced inside SQLite
- **Tier:** consequential (data model + requirement 5). Accepted by Kevin 2026-10-21.
- **Choice:** Inferences live in `inferred_claims`, never in profile
  snapshots, so the `match_citations` FK to `profile_snapshot_items` rejects
  them. `experience_items` is writable only by the user-action connection;
  discovery and evaluation connections get an authorizer that denies writes to
  it (and to applications, settings, completeness). Only the verify handler
  sets `verified`.
- **Why:** Replaces Postgres role grants with mechanisms that still fail
  loudly on a bug or on model output steering a code path.
- **Rejected alternative and why:** Convention plus code review only. Cheaper,
  but one wrong import silently removes the guarantee. Separate database files
  per trust level would isolate harder but break cross-table transactions.

### D3: Windows desktop: Tauri 2 host + PyInstaller onedir Python agent
- **Tier:** consequential (system design / packaging). Accepted by Kevin 2026-10-21.
- **Choice:** Rust host for tray, window, single instance, autostart, agent
  supervision and IPC forwarding only; all business logic in a Python agent
  built with PyInstaller `--onedir --console`, shipped as a bundle resource
  (directory mapping) and started with `CREATE_NO_WINDOW`; watchdog never
  respawns while its last child lives and stops on exit code 4; NSIS per-user
  installer with stop-agent hooks and a `taskkill` backstop.
- **Why:** Tray, single-instance and the installer come as maintained Tauri
  features (autostart is written explicitly, D16); a separate agent process outlives the UI; the
  scanner code and tests stay in Python. Onedir avoids onefile's temp
  extraction, leftover folders and higher antivirus hit rate. Spike P1.0d
  demonstrated survival of window close and host crash, single instance,
  crash recovery, autostart, update with a running agent, rollback and
  uninstall, and found ten defects now encoded as rules (Appendix B).
- **Rejected alternative and why:** Python-only (pywebview + pystray + Inno
  Setup): one language, but UI and agent share a process and installer,
  autostart, single-instance and updates are hand-rolled. Electron: heavy for
  no gain. Nuitka: fallback if P1.0d shows PyInstaller problems.

### D4: The agent is the only process that opens the database
- **Tier:** consequential (concurrency design). Accepted by Kevin 2026-10-21.
- **Choice:** UI and host go through IPC; inside the agent, one serialized
  writer (short `BEGIN IMMEDIATE` transactions, never across a network or
  model await), read-only readers, WAL.
- **Why:** No cross-process SQLite locking, one place for enforcement, and
  long model calls cannot block user actions.
- **Rejected alternative and why:** UI reads SQLite directly. Faster reads,
  but two processes contending for one file and a second place rules must hold.

### D5: SQLite queue claims, instance lock and orphan recovery replace SKIP LOCKED
- **Tier:** consequential (concurrency/recovery design). Accepted by Kevin 2026-10-21.
- **Choice:** Claims are `BEGIN IMMEDIATE` + `UPDATE ... RETURNING`; leases for
  in-process timeouts; one agent per user via `LockFileEx` on `agent.lock`;
  at startup every `running` item and unfinished fetch is an orphan by
  definition and is reset (failed after 3 attempts); partial unique index on
  in-flight `source_fetches`.
- **Why:** SQLite's single writer makes a claim atomic; the OS lock is released
  by Windows when the process dies, so a crash never leaves a stale lock and
  recovery needs no lease timeouts.
- **Rejected alternative and why:** A heartbeat row as the single-instance
  guard. Portable, but a crashed agent's row looks alive until it expires.

### D6: Only eligibility-type requirements can be hard blockers, and only when code confirms the reading
- **Tier:** consequential (core product mechanic). Accepted by Kevin 2026-10-21.
- **Choice:** `final_class = hard` only for work authorization, sponsorship,
  clearance held, legally required license, residency/location, travel,
  physical, or program status (e.g. active-duty SkillBridge), and only with
  quote found, not negated, candidate subject, required modality, no
  equivalence clause, **and an eligibility type assigned by the E2 code
  lexicon** (the model's type is a hint). Contractual certifications such as
  DoD 8570/8140 baselines are negotiable, not hard (Q18, default accepted
  2026-10-21).
- **Why:** A strongly worded skill line is how a viable stretch job becomes a
  false SKIP. Spike P1.0b: models labeled found blockers `hard` reliably but
  got the eligibility type right on only 1-2 of 4, so the type that decides
  which declared fact is checked must come from code.
- **Rejected alternative and why:** Model labels any requirement hard, or
  model-assigned eligibility type is trusted.

### D7: SKIP requires positive evidence; uncertainty goes to REVIEW
- **Tier:** consequential (error-cost tradeoff). Accepted by Kevin 2026-10-21.
- **Choice:** SKIP only via a confirmed blocker failed by a declared fact, a
  WEAK fit on sufficient evidence, or a STRETCH with LOW opportunity value.
  REVIEW shown as grouped profile questions. Property test.
- **Why:** False SKIP is the costly error (A9).
- **Rejected alternative and why:** Missing evidence treated as a gap.

### D8: Code checks the model's interpretation, not just its quote
- **Tier:** consequential (evidence validation). Accepted by Kevin 2026-10-21.
- **Choice:** E2 computes its own reading (section, negation, modality,
  equivalence, subject), stores it beside the model's; conflicts never block.
- **Why:** "Degree not required" is a real quote and a wrong requirement.
- **Rejected alternative and why:** Second LLM pass (doubles GPU cost first).

### D15: Eligibility recall scan turns missed blockers into REVIEW
- **Tier:** consequential (error-cost tradeoff). Accepted by Kevin 2026-10-21.
- **Choice:** E2 runs the eligibility lexicon over every posting sentence
  outside EEO/privacy text (corrected in P1.6: benefits sections are scanned,
  because real postings put residency rules there: Vultr's state list under
  "Compensation", Lyft's "must be based in the US" under "Benefits:"); any cue
  sentence not covered by an
  extracted requirement becomes `unextracted_eligibility`, which makes
  eligibility UNKNOWN (REVIEW with a question), never SKIP.
- **Why:** Spike P1.0b: the best-scoring model missed the SkillBridge
  active-duty condition outright; without a backstop that is a silent false
  ELIGIBLE. The lexicon is cheap and testable; its false positives cost a
  REVIEW, not a SKIP.
- **Rejected alternative and why:** Trust model recall, or a second model
  pass dedicated to eligibility (doubles GPU cost; the lexicon already
  catches the observed miss).

### D9: Append-only history and continuous discovery state live in rows, protected by triggers
- **Tier:** consequential (data integrity, provenance, recovery). Accepted by Kevin 2026-10-21.
- **Choice:** All postings stored incl. rejects; new raw row only on
  `material_sha256` change; `BEFORE UPDATE/DELETE` triggers abort on
  `raw_postings`, `search_profiles`, `profile_snapshots`,
  `profile_snapshot_items`, `application_events`; due times in
  `employer_poll_state`; next poll computed from completion time with jitter;
  ETag/304 polling.
- **Why:** Evaluations stay defensible against the text they saw; re-filtering
  needs rejects; row state survives restarts and sleep; 304s make 15-minute
  polling cost near-zero bytes on unchanged boards (measured).
- **Rejected alternative and why:** Overwrite in place / drop rejects / fixed
  schedule slots (burst after downtime).

### D10: Analysis, evaluation and recommendation cached by their own dependencies
- **Tier:** standard-practice.
- **Choice:** E0-E2 per posting version + prompt; E3-E4 per analysis + profile
  snapshot + prompt; E5-E6 per evaluation + search profile + rule version.
- **Why:** Profile edits do not redo extraction; rule changes cost zero GPU.
- **Rejected alternative and why:** One evaluation row per job.

### D11: IPC is localhost HTTP with a per-start token, called only by the Rust host
- **Tier:** consequential (security tradeoff). Accepted by Kevin 2026-10-21.
- **Choice:** Agent binds `127.0.0.1:0`, writes `{port, token, pid,
  protocol_version}` to a user-only `ipc.json`; every request needs the
  token and Host; the webview calls a Tauri command and never sees the token;
  events over SSE relayed as Tauri events; protocol-version handshake.
- **Why:** Testable with curl, reuses FastAPI, survives host restarts; a token
  the browser cannot read closes the CSRF/rebinding surface.
- **Rejected alternative and why:** Named pipe with user-only DACL: stronger
  isolation, but the Python server needs undocumented asyncio internals or
  pywin32 (deferred hardening). Sidecar stdio: dies with the host.

### D12: Forward-only SQL migrations with backup-before-migrate and refuse-newer
- **Tier:** standard-practice.
- **Choice:** Numbered SQL files, `PRAGMA user_version`, one transaction each,
  `VACUUM INTO` backup first, agent exits 4 on a newer DB, `--restore` command.
- **Why:** SQLite DDL is transactional; rollback = old installer + backup.
- **Rejected alternative and why:** Alembic with down migrations (rarely
  tested, little value on SQLite rebuilds).

### D13: One OpenAI-compatible client; providers are config rows
- **Tier:** standard-practice.
- **Choice:** Single client; `model_configs` rows incl. reasoning args
  (default off) and `max_tokens`; Pydantic validation; context overflow and
  `finish_reason = length` recorded as failures; output schemas pass a lint
  that forbids enum values shared across fields; a config is enabled only
  after a load-and-snippet test.
- **Why:** LM Studio, Ollama and others share the protocol.
- **Rejected alternative and why:** Provider plugins / LangChain.

### D16: Explicit per-user behavior; refuse elevation when it cannot be made safe
- **Tier:** consequential (security / install behavior). Choice made by Kevin
  2026-10-21 in his words: "Prefer explicit per-user behavior; refuse
  elevation if safe behavior cannot be guaranteed."
- **Choice:** JobForge writes and removes its own `HKCU\...\Run` value (the
  autostart plugin is dropped). The agent exits 5 if started elevated in run
  mode or for `--restore`. An elevated host relaunches itself at medium
  integrity through the shell (Explorer's token) and exits; if the relaunch
  fails, or the elevated token's user SID differs from the interactive shell
  user's, it shows "JobForge must run as you, without administrator rights"
  and exits.
- **Why:** Spike P1.0d: the plugin chose `HKLM` (machine-wide) when elevated.
  An elevated agent would create files owned by Administrators inside the
  user's data root, and under "Run as different user" the per-user paths
  would belong to another account. Writing HKCU directly and keeping the
  agent at medium integrity makes the per-user guarantee independent of how
  the app was launched.
- **Rejected alternative and why:** Refuse every elevated launch outright
  (simpler, but punishes a common accident such as an elevated "launch after
  install" when a safe relaunch is available); or keep the plugin and trust
  its mode selection (the spike showed it is token-dependent).

### D14: Stack
- **Tier:** standard-practice.
- **Choice:** Agent: Python 3.14, asyncio, httpx, stdlib sqlite3 (3.50.4
  bundled), pydantic, FastAPI + uvicorn on a pre-bound socket, structlog,
  pytest. Host: Tauri 2 with single-instance and notification
  plugins. UI: React + TypeScript + Vite, types written by hand in one module
  (`desktop/src/api.ts`) and checked against the agent's served routes by a test
  (corrected in P1.8: the routes return plain JSON objects, so OpenAPI has no
  response schemas to generate types from). DOMPurify sanitizes posting HTML.
- **Why:** Scanner code is Python/httpx; SQLite features needed (STRICT,
  partial indexes, RETURNING, authorizer) are all in the bundled version.
- **Rejected alternative and why:** SQLAlchemy ORM (adds a layer over plain
  SQL the triggers and authorizer already shape).

## 4. Data model

<!-- Tables/collections, ownership, and the access rule per table.
     For anything multi-tenant or user-owned, state the enforcement
     mechanism explicitly (RLS forced, not just enabled). -->

Columns: docs/ARCHITECTURE.md 4.2. One SQLite file per Windows user; no
`owner_id` (the file is the owner). Write access is per agent component,
enforced by per-connection authorizers (allow-lists in one module), plus
triggers and constraints that apply to every connection.

| Table | Owned by | Access rule | Enforcement |
|-------|----------|-------------|-------------|
| sources, employers | global | User-action writes config; discovery updates backoff/status | Authorizer allow-lists |
| employer_poll_state, robots_cache | global | Discovery only (user action may set next_poll_at for poll-now) | Authorizer |
| source_fetches | global | Discovery only | Authorizer + partial unique index on in-flight rows |
| raw_postings | global | Insert only | Append-only triggers + unique (source_job_id, material_sha256) |
| jobs | global | Discovery writes | Authorizer |
| change_events | global | Any writer component inserts; delete only rows > 7 days | Trigger |
| posting_analyses, requirements | global | Evaluation writes; discovery and user action (evaluate anyway) insert pending analyses and may only change `status` (supersede) | Authorizer (column-level for discovery and user) + partial unique index (one pending per job) |
| settings | personal | User action only | Authorizer |
| search_profiles | personal | Insert-only versions by user action | Append-only triggers |
| filter_results | personal | Discovery inserts (poll and re-filter); never updated | Authorizer + unique (raw_posting_id, search_profile_id, ruleset_version) |
| filter_overrides | personal | User action inserts only | Authorizer |
| resumes | personal | User action writes; others read | Authorizer |
| experience_items, profile_completeness | personal | User action only; `verified` only via the verify handler | Authorizer (discovery/evaluation denied) + CHECK (status <> 'verified' OR verified_at_ms IS NOT NULL) + grep test on connection factory imports |
| inferred_claims | personal | Evaluation inserts; user action changes state | Authorizer |
| profile_snapshots, profile_snapshot_items | personal | Insert only | Append-only triggers |
| match_citations | personal | Evaluation inserts | FK (snapshot_id, item_id) -> profile_snapshot_items |
| model_configs | personal | User action writes | Authorizer |
| evaluations, requirement_matches, recommendations | personal | Evaluation writes; user action inserts pending evaluations | Authorizer + unique keys |
| applications, application_events | personal | User action only | Authorizer; events append-only triggers |
| agent_runs | global | Agent lifecycle | Authorizer |

## 5. Security posture

<!-- The baseline deployment floor applies automatically and is not
     restated here. This section is only for what is ABOVE the floor
     for this project, or any floor item deferred (which also goes to
     DEFERRED_COMPLEXITY.md with a reason). -->

**Above the floor:**
- Only listener: agent IPC on `127.0.0.1`, per-start 256-bit token, Host check,
  no CORS; `run\` directory DACL restricted to the user SID, verified at start.
- Token never in the webview; Tauri capabilities limited to `agent_call`,
  window and tray; strict CSP; job-description HTML sanitized, links opened in
  the system browser through an allow-list.
- Verified-experience and history protection per section 4 and ARCHITECTURE
  4.3, with the stated limit: protects against bugs and model-steered code
  paths, not against malware running as Kevin.
- Log redaction (no resume text, experience, eligibility facts, prompts,
  model output, token); covered by a test.
- LM Studio on localhost, LAN serving off (verified not reachable on
  Ethernet, Tailscale, VMware adapters, 2026-10-21).
- Per-user install, no admin; data outside the install directory.
- Sources disabled until a terms/robots review is recorded; Crawl-delay,
  Retry-After, budgets honored; 401/403 stops polling.

**Floor deferrals (should be empty):**
- Web-deployment items (edge 404 probes, Cloudflare, app robots.txt): no web
  deployment exists (DEFERRED_COMPLEXITY.md). Accepted by Kevin 2026-10-21.
- Code signing (installer and agent): deferred while Kevin is the only user;
  SmartScreen warning accepted (Q22, 2026-10-21; DEFERRED_COMPLEXITY.md).

**Adversarial pass scheduled:** yes, after P1.8 (accepted 2026-10-21): a hostile
local web page against the IPC port, a malicious job description against the
webview, and every forbidden write per component against the authorizers.

## 6. Verification plan

<!-- This section IS the input to verify.sh. Every row names a behavior and
     the raw signal that proves it against the live system. If a behavior
     has no signal listed, it is not verifiable, which means it is not done.
     Per the baseline: the trace, not the claim. -->

verify.sh reads `<data root>\run\ipc.json` (`%LOCALAPPDATA%\<identifier>\`) for the port and
token, opens the database read-only (`mode=ro`) for SQL probes, and shells to
PowerShell for process, registry and ACL probes.

| Behavior | Command / probe | Signal that proves it |
|----------|----------------|-----------------------|
| Agent is up | `curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$PORT/v1/health` | 200 JSON: version, protocol_version, db ok, inference status, heartbeat age |
| IPC rejects missing token | Same without the header | 401 |
| IPC rejects foreign Host | `-H "Host: evil.example"` with token | 400 |
| Loopback only | `Get-NetTCPConnection -OwningProcess <agent pid> -State Listen` | Only `127.0.0.1:$PORT` |
| ipc.json is user-only | `icacls %LOCALAPPDATA%\JobForge\run` | Only Kevin's SID (plus SYSTEM/Administrators if inherited is shown, printed as-is) |
| One agent per user | `Get-Process jobforge-agent` | One process; `agent_runs` shows no overlapping start/stop intervals |
| Agent survives window close | Close window; `Get-Process`; SQL `source_fetches` started after close time | Same agent PID; new fetch rows after the close |
| Autostart setting | `Get-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\Run` | JobForge value present iff the setting is on |
| Crash and restart history | SQL `agent_runs` last 10 | start/stop times, stop_reason (user_exit, crash_recovered, update, uninstall, shutdown) |
| Agent not elevated | `agent.jsonl` `agent_start` lines | `"elevated": false` on every start |
| Elevated agent refuses | Start `jobforge-agent.exe` from an elevated shell | Exit code 5; log `refused_elevated` |
| Elevated host relaunches unelevated | Start `JobForge.exe` from an elevated shell | `host.log` `relaunched_unelevated`; the resulting agent logs `"elevated": false` |
| Autostart is per-user only | `Get-ItemProperty` HKCU and HKLM `...\Run`, after enabling from an elevated and from a normal launch | Value in HKCU only; none in HKLM |
| Different-user elevation refused | Manual: "Run as different user" with another admin account | Message shown; no agent started; no files under the other account's data root (manual step for Kevin) |
| No hung-agent pile-up | `Get-Process jobforge-agent` count; `host.log` "not spawning another" lines | Count <= 1 |
| Bootloader failures visible | `agent-console.log` tail | Raw text (empty when healthy) |
| Paused state persists | SQL `settings.discovery_paused`; fetches since pause | Value printed; no fetches while 1 |
| Data in per-user dirs only | List install dir files with LastWriteTime after install; list `%LOCALAPPDATA%\JobForge` tree | Install dir unchanged since install; DB, logs, backups in the data tree |
| DB pragmas and integrity | SQL `PRAGMA journal_mode`, `user_version`, `integrity_check`, `foreign_key_check` | wal, current version, ok, no rows |
| Append-only triggers present | SQL `sqlite_master` triggers per protected table | Both UPDATE and DELETE triggers on each listed table |
| Backups exist | List `backups\` | Daily files (<= 7) and pre-migration files with sizes and dates |
| Discovery is continuous | SQL per source: enabled employers, `max(now - last_success_at_ms)` | Max age < 2x effective interval, except employers listed with failure/backoff/paused |
| Intervals as configured | SQL median gap between successive fetches per employer, 24 h | Within 10% of effective interval; none below `min_interval_s` |
| Conditional requests working | SQL share of NOT_MODIFIED per host, 24 h; bytes per host | Printed shares and bytes |
| Jitter, no bursts | SQL fetch starts per minute per host, 2 h | Printed histogram |
| No overlapping polls | SQL overlapping [started, finished] pairs per employer | Zero rows |
| Retry-After / backoff honored | SQL rows with retry_after_s and the next request to that host | Next request >= response + Retry-After |
| Request volume within budget | SQL requests/hour per host vs `max_requests_per_hour` | Printed numbers |
| Source errors visible | SQL employers with failures or dead/blocked + `/v1/discovery/status` | Same set in both |
| Recovery from interruption | SQL INTERRUPTED fetches and their successors; `running` items older than agent start | Each INTERRUPTED has a later fetch; zero stale `running` |
| Bounded catch-up | SQL requests per host in the first interval after each `agent_runs` start or detected gap | Printed numbers vs budget |
| Change detection | SQL 24 h new / changed / closed per source; raw rows vs fetches | Raw rows only for new/changed |
| Rejected postings retained | SQL raw_postings with only `reject` results | Count > 0, growing |
| Every stored posting has a verdict | `scripts/verify_filter.py` coverage section | Open jobs without a verdict for the active profile: printed count (0 after a re-filter) |
| Re-filter keeps history | `scripts/verify_filter.py` history section | One row set per profile version; older versions' counts unchanged after a new save |
| Filter port matches the scanner | `scanner\.venv\Scripts\python scripts/verify_filter.py DB --scanner ..\job-scanner` | Same-verdict and same-rule counts; each disagreement listed with both reasons |
| Only passes and overrides queue analysis | `scripts/verify_filter.py` analysis queue section | Pending analyses with neither a pass nor an override: printed count (0) |
| One pending analysis per job | SQL jobs with > 1 pending analysis | Zero rows |
| Evaluation history preserved | SQL jobs with > 1 raw version and their evaluations | Old evaluations present, pointing at old raw rows |
| Discovery independent of inference | `python scripts/verify_evaluation.py <data dir>` agent-log section (SQL over each inference-down window from `agent.jsonl`) | Fetch count and largest gap between one employer's fetches inside the window, vs its interval |
| Evaluations resume automatically | `python scripts/verify_evaluation.py <data dir>` agent-log section | `inference_unavailable`, `inference_available`, then `evaluation_done` lines with timestamps and no `agent_start` between |
| Live refresh | Subscribe `curl -N .../v1/events` across a poll that finds a posting; window open on Jobs > All open | `posting_new` within 5 s of the fetch's finished_at; the job at the top of the open window without reload (screenshot) |
| Host restart replays missed events | Stop the host, make changes through the agent, start the host; `<data root>\host\event_cursor`, `logs\host.log` | `event_stream_connected after=N` then `event_stream_first_event id=N+1`; cursor = last missed id |
| Postings render inert | `npx vitest run` (from `desktop/`); open a posting containing `<script>`, `onerror`, `javascript:` | Pass/fail counts; screenshot shows the text with no script effect, no image, no live `javascript:` link |
| Window calls only served routes | `uv run pytest tests/test_views.py -q` (from `agent/`) | Pass/fail (route contract, outbound-request scan, applications) |
| Application history | SQL `applications` and `application_events` after a status change | One application per job; one event row per status change (append-only) |
| Verified-experience protection | `uv run pytest tests/test_authorizer.py tests/test_profile.py -q` (from `agent/`) | Pass/fail counts (each forbidden write per component; the verify-only source scan) |
| Resume round trip | Download `/v1/resumes/{id}/original`, sha256 vs the file on disk | Identical hashes per resume |
| Verified items carry a time | SQL `experience_items` with status verified and `verified_at_ms` NULL | Zero rows (CHECK constraint) |
| No profile content in logs | Search `agent.jsonl` for substrings of resume text and item statements | Zero hits |
| Nothing contacts an employer | `uv run pytest tests/test_views.py -k outbound -q` | Pass: HTTP clients only in `discovery/`, `inference/` and the loopback-only CLI (`main.py`); host requests only `127.0.0.1`; no fetch/XHR/WebSocket in `desktop/src` |
| Recommendations carry versions and assessments | `python scripts/verify_evaluation.py <data dir>` "Last 5 recommendations" | eligibility, fit, opportunity, decisive_reasons, rule/prompt versions, model_id_reported, snapshot non-null |
| Recommendation distribution | `python scripts/verify_evaluation.py <data dir>` "Recommendation distribution", "Per job", "REVIEW causes" | Printed counts per category, assessment and decisive rule; per-job rows; REVIEW causes |
| SKIP only from positive evidence | `python scripts/verify_evaluation.py <data dir>` SKIP section; `uv run pytest tests/test_verdict.py -k skip_only -q` (from `agent/`) | Zero rows, each SKIP's reason printed; property test pass/fail |
| Invented quotes and conflicts excluded | `python scripts/verify_evaluation.py <data dir>` "Invented quotes and conflicts excluded" | Zero blocks from a non-confirmed or recall requirement; eligibility reasons by effect |
| Missed blockers surface | `python scripts/verify_evaluation.py <data dir>` "Missed blockers surface" | Rows listed with sentence, outcome and effect; none `blocks` |
| Verdict suite (layer B) | `uv run pytest tests/test_verdict.py tests/test_evaluation.py -q` (from `agent/`; fixtures `tests/fixtures/verdict/`) | Pass/fail counts (incl. SKIP property test, FK-rejected inferred citation, recompute with zero model calls, long model call vs user action) |
| Citations only from the evaluated snapshot | `python scripts/verify_evaluation.py <data dir>` evidence-table section | Citations outside their evaluation's snapshot: 0; citations of non-snapshot ids: 0 (FK) |
| Eligibility matched by code | `python scripts/verify_evaluation.py <data dir>` evidence-table section | Hard rows: `matched_by` only `eligibility_fact` |
| Eligibility type from code | SQL requirements where model type != lexicon type | Listed; all `conflict` (a `hard` class is allowed and makes eligibility UNKNOWN, never a block; corrected in P1.6) |
| Schema lint | `uv run pytest tests/test_analysis.py -k schema_lint -q` (from `agent/`) | Pass/fail |
| Model budget failures | SQL analyses/evaluations with `failed: budget_exhausted` or `context_overflow`, 7 days | Counts per model config |
| Interpretation suite | `uv run pytest tests/test_interpretation.py -q` (from `agent/`; fixtures `tests/fixtures/interpretation/layer_a.yaml`) | Pass/fail counts |
| Conflicts keep both readings | SQL `requirements` with status conflict: `model_*` vs `cue_*` columns and the `conflict` flag | Every row has both readings and at least one `{field, model, cue}` |
| Model config tested before use | SQL `model_configs` enabled with `tested_at_ms` NULL; `POST /v1/model-configs/{id}/test` report | Zero rows (CHECK); report with listed, finish_reason, quotes found |
| Analysis independent of the profile | After a profile edit: `/v1/analysis/status` counts, `analysis_done` log lines | Counts and line count unchanged |
| Interpretation report | `jobforge-agent golden --suite interpretation` | Per-area agreement numbers |
| Golden-set agreement | `jobforge-agent golden` | False SKIP, false APPLY, REVIEW share, confusion table |
| Lifecycle and migration suites | `pytest agent/tests/lifecycle -q` | Pass/fail counts (kill/restart, migration interrupt, refuse-newer, restore) |
| All tests | `pytest agent -q`; `cargo test` in `desktop/src-tauri` | Pass counts, failures listed |
| Secrets/personal data not tracked | `git ls-files` checks | No `.env` except example, no `*.db`, no data or backup paths |
| Runtime log tail | Last 50 lines of `agent.jsonl` and `host.log` | Raw lines |

## 7. Career evidence

<!-- Career evidence is shifted left, same as security. These artifacts get
     produced DURING the build as a side effect, not after it as a project.
     The audience is hiring managers and engineering peers, not customers.
     Check them off as they happen.

     Shipped, for this lane, means live plus narrated: deployed, verify.sh
     evidence in hand, and this checklist done. "Technically complete" with
     this section empty means the project is not complete. -->

Optional for this project (no lane, Kevin 2026-10-21); not a completion gate.

- [ ] One debugging story written up while fresh (bug, hunt, root cause)
- [ ] README written for a stranger, not for me
- [ ] One decision block from section 3 turned into a post draft

## 8. Change log

<!-- Spec changes after AGREED get a dated line here. If the code and this
     spec disagree and the spec is the one that is wrong, the fix lands
     here in the same session, per "Docs move with the code." -->

| Date | Change | Why |
|------|--------|-----|
| 2026-10-21 | Initial DRAFT from architecture brief | First assignment: architecture before implementation |
| 2026-10-21 | DRAFT rev 2: four categories, three assessments, requirement classes, match types, E0-E6 with interpretation checks, SKIP-requires-evidence, all postings retained | Kevin: middle ground between conservative screening and optimistic matching |
| 2026-10-21 | DRAFT rev 3: continuous incremental discovery, change detection, supersession, two workers, dashboard and live refresh | Kevin: near-real-time discovery within responsible limits |
| 2026-10-21 | P1.0 spikes (a) and (c) run; (b) stopped by Kevin before the first model call. Results in ARCHITECTURE Appendix A | Kevin: test before anything else |
| 2026-10-23 | P1.8c profile from a resume: "Suggest items" on an uploaded resume (`POST /v1/resumes/{id}/suggest-items`, `profile/suggest.py`, prompt `r1-v1` pinned by a hash). Suggestions go to the inferred-claims inbox, unconfirmed (D2 unchanged: the inbox is never in a snapshot); code drops any whose quote is not in the resume text, never pads a year-only date to a month, and skips statements already known. Accepting gives a self-reported item with the resume's dates; confirming stays a separate click. Migration 0007 (`inferred_claims.proposed_by_resume_id`, `proposed_fields`); the user connection may now insert inbox rows. Setup step 4 is upload + suggest + check; the prepared profile file is a fallback. Host allows `/suggest-items` 330 s. Live: two of real resumes, 46 and 45 suggestions, 0 quotes not found, about 54 s each; every entry of both skills sections suggested (a draft without the skills-section prompt line kept 10 of 27) (`docs/evidence/p1_8c_resume_suggestions_live.txt`) | Kevin: proceed (suggestion after P1.8b: a new user had no way to build a profile) |
| 2026-10-23 | P1.8b usability pass. First-run Setup opens until companies, search profile and model are done (`GET /v1/setup/status`). Companies from pasted careers links (Greenhouse, Lever, Ashby; short forms like `lever:plaid`) or a job-scanner watchlist file; Stop watching / Watch again. Search profile as a plain form (`GET\|POST /v1/search-profile/form`, `filtering/simple.py`): typed words become phrase patterns; rules imported from job-scanner are shown as plain words where simple and kept unchanged where they hold regex logic (listed read-only); generic defaults in `filtering/defaults.yaml` (no personal terms); errors are sentences. The rules-JSON editor is removed from the window. AI model: one "Check and turn on" with plain failure messages. Plain wording across the window (`labels.ts`: no NEGOTIABLE / CONFIRMED / rule numbers on screen; job detail leads with a summary, details collapsed; Dashboard became Status with a "needs attention" list). Installer rebuilt; its layout check now also requires `verdict_rules/v2.yaml`, `filtering/defaults.yaml` and the newest migration. Still not plain: the profile step loads a prepared profile file (DEFERRED_COMPLEXITY.md) | Kevin: "this beast is no user friendly at all"; "most users aren't going to know what json even is" |
| 2026-10-23 | P1.8 implemented (UI): Jobs (APPLY+STRETCH default, Rejected, All, search), job detail (three assessments, reasons, eligibility checks, opportunity factors, evidence table with the snapshot's cited items, application, sanitized posting), REVIEW questions grouped, Profile (items, typed facts from `/v1/profile/schema`, verify, completeness, resumes, inferred claims, JSON import of a reviewed profile), Applications, Dashboard, Preferences. Agent: job list/detail, applications (migration 0006: one per job, events append-only), profile schema routes. Host: `agent_call` with bodies and PUT/PATCH/DELETE keeping the agent's error messages, `agent_upload`, `open_url` (http(s) only), event cursor persisted across host restarts. DOMPurify sanitizer. Spec corrections: D14 (types written by hand + route contract test; no OpenAPI schemas exist), the no-outbound criterion also allows the loopback-only CLI. Test fixes: integration launches used owner-only temp folders the unelevated Explorer could not open, and leaked them. Evidence: `docs/evidence/p1_8_ui_live.txt` | Implementation |
| 2026-10-23 | Rules v2 made active (`VERDICT_RULE_VERSION = v2`): a hard requirement whose only interpretation conflict is the eligibility type is checked under both readings and is answered only when verified facts meet both; still never blocks. E2 `interp-v2`: residency cues for "be local to" / "local to the ... area" and on-site or in-office days per week (Keeper's location rules had been read as experience); layer-A cases and controls added. Measured on the resume-built profile in an evidence copy: E1 replayed, E3 reused on 41 of 44 jobs (3 Gemma calls); 4 location rules now eligibility rows | Kevin: "turn on v2 and fix the local to gap" |
| 2026-10-23 | P1.7 follow-up: an E3 answer is reused for an identical request (sha256 of the full request body, same model config; migration 0005 `evaluations.request_sha256`), also while inference is down. A verify, an eligibility-fact or completeness edit and an E4/E5 version bump no longer cost model calls (drill: 44 re-evaluations in 0.2 s, 0 calls). Rejected: a manual replay command | Kevin: proceed with the P1.7 suggestions |
| 2026-10-22 | P1.7 implemented (evaluation E3-E6): content-addressed profile snapshots, evaluation queue with supersession (migration 0004: `evaluations.job_id`, `superseded`, one pending per job; snapshot items keep years and dates), eligibility requirements matched by code against typed facts, E3 prompt `m1-v1`, E4 validation with FK-checked citations (savepoint retry), E5/E6 with thresholds in `verdict_rules/v1.yaml`, recompute in the worker loop and `POST /v1/recompute`, recommendation and REVIEW-question routes, optional `opportunity` section in the search profile (Q8 data). Spec corrections: E3 row (every eligibility-typed requirement is matched by code, not only those with a declared fact); 8 (eligibility facts also live in snapshots and match rows). Points 6.5 left open decided and recorded there (recall rows, self-reported fail, fit denominators and ladder order, no preferred upgrade, opportunity factors; a gap counts only when evidenced, per D7). Two independent reviews before commit (a second agent, then Codex adversarially) found 14 defects, each fixed with a test (ARCHITECTURE 6.4). Rules v2 (type-only conflicts checked under both readings) built and measured, not active (Kevin's decision; DEFERRED_COMPLEXITY.md). Test-isolation bug fixed: the integration sign-out test messaged the first agent window on the machine and stopped a running agent. Live drill: LM Studio stopped 61 min, 433 polls on all 84 employers, 44 evaluations pending with 0 attempts, drained after restart of LM Studio with no agent restart; profile change recomputed 44 recommendations with 0 model calls; on the thin drill profile 40 of 44 are REVIEW. Evidence: `docs/evidence/p1_7_evaluation_live.txt` | Implementation |
| 2026-10-22 | P1.6 implemented (posting analysis E0-E2): segmenter with heading lexicon (`privacy` label added), E1 prompt `e1-v1`, E2 interpreter with checks 1-7, analysis worker with health gate, model-config test/enable. Spec corrections: E1 polarity values are affirmative/negated (6.4 still showed the spike's defective `required/negated`); preferred wording inside a Requirements section is not a conflict (6.4 check 3); the "Eligibility type from code" verification row allowed no `hard` conflict rows, which contradicts 6.5 (conflicting hard -> UNKNOWN); test paths in section 6. D15 corrected: benefits sections are scanned and can hold eligibility conditions (live run: Vultr and Lyft put residency rules there, which the first design turned into `context`). E2 rules added from real model output: list lead-in joined to a bullet is located, context rows ignore importance disagreements, eligibility conditions with a disputed subject stay requirements (ARCHITECTURE 6.4). `httpx` moved from dev to runtime dependencies (discovery already imported it). Evidence: `docs/evidence/p1_6_analysis_live.txt` | Implementation |
| 2026-10-22 | P1.5 implemented (profile): resume upload as raw bytes with text extraction (Markdown, text, DOCX via standard library, PDF via pypdf), experience item CRUD, verify/unverify (edit of a verified item clears it), typed eligibility facts per E2 eligibility type, completeness flags, inferred-claims inbox, `profile_changed` events carrying ids only. Verify-only rule pinned by an AST source scan (planned `ipc/routes/` grep test adapted to the real layout). Evidence: `docs/evidence/p1_5_profile_live.txt` (14 real resumes byte-identical, 0 resume text in logs) | Implementation |
| 2026-10-22 | P1.4 implemented (filter + retention): scanner rules ported rule for rule (edd1528) with home area as profile data; every failed rule recorded beside the deciding one; filtering inside the poll transaction; versioned profile save with validation (422 naming the field) and CLI import of the scanner config.yaml; re-filter of every job's current posting in 300-job transactions; evaluate-anyway; default model seeded (migration 0003); user grants for overrides and pending analyses. Scanner `CLOSED` rule replaced by job closure. Docs corrected: writes are serialized by the event loop, not an asyncio lock (ARCHITECTURE 3.4). Evidence: `docs/evidence/p1_4_filter_live.txt` (13,205 postings, scanner differential 0 disagreements) | Implementation; drift fix (re-filter covers closed jobs per the P1.4 acceptance row) |
| 2026-10-22 | P1.3 implemented (discovery): ported Greenhouse/Lever/Ashby parsers and robots handling (edd1528), ETag polling, change detection with closure and reopen, lease-based scheduler with jitter, per-host spacing and hourly budget, Retry-After host backoff, exponential employer backoff, dead/blocked marking, respread after downtime, watchlist import over IPC, discovery status route. Migration 0002 adds `jobs.employer_id` and seeds the three reviewed sources; Greenhouse budget 300/h (watchlist of 58 Greenhouse employers needs ~232/h at 15 min) | Implementation; budget sized from the real watchlist |
| 2026-10-22 | P1.2 implemented (desktop shell): Tauri host with tray, close-to-tray, attach-or-spawn, watchdog with exit codes 3/4/5, HKCU autostart, D16 unelevated relaunch, SSE event relay, minimal React window; agent gained pause/resume, `/v1/events`, sign-out handling, WAL-safe close order. Evidence: `docs/evidence/p1_2_desktop_lifecycle.log`, `docs/evidence/p1_2_window.png`. Q26 one-time close notice deferred | Implementation |
| 2026-10-21 | P1.1 implemented (agent core). Clarified: `--backup` is also refused elevated; backups use SQLite's backup API; schema columns implied by checks 6-7, the model load test and source terms gate added to 0001 (ARCHITECTURE 4.2) | Implementation detail; no behavior change to agreed decisions |
| 2026-10-21 | Status DRAFT to AGREED. Defaults applied: Q3 copy adapters (origin `edd1528`), Q10 no notifications, Q18 contractual certs negotiable, Q22 unsigned while single user, Q23 localhost HTTP + token; Cloudflare deferral accepted; adversarial pass after P1.8; source terms review recorded (ARCHITECTURE Appendix A); all remaining ARCHITECTURE questions closed with their proposed defaults | Kevin: "go" |
| 2026-10-21 | Lane and own-words restatement waived for this project; D1-D16 marked accepted | Kevin: "lane doesn't matter nor does me restating. this is just slowing development" |
| 2026-10-21 | Kevin's answers: Q4 default model gemma-4-12b-qat with reasoning disabled; Q27 spike labels provisional for testing, misses reviewed independently before any golden-set use; Q28 explicit per-user behavior, refuse elevation when safety cannot be guaranteed. D16 added (autostart plugin dropped, agent exit 5 when elevated, host unelevated relaunch or refusal); verification rows added | Kevin's decisions |
| 2026-10-21 | P1.0b model evaluation and P1.0d packaging spike run (ARCHITECTURE Appendix A, B). D3, D6, D13 updated; D15 (eligibility recall scan) added; data root moved off the install directory; console-subsystem agent; watchdog exit-code rules; async DB handlers; schema lint; reasoning off by default; verification rows added | Kevin: two experiments before freezing the spec |
| 2026-10-21 | DRAFT rev 4: Windows desktop pivot. Tauri 2 host + PyInstaller onedir Python agent + SQLite WAL; Docker, Postgres, browser UI removed. Postgres guarantees replaced (authorizers, triggers, IMMEDIATE claims, instance lock, orphan recovery, FK citations); IPC token design; lifecycle; installer/update/rollback; ETag polling from spike (c). D2-D5, D9, D11 rewritten; D12, D14 new; `owner_id` dropped; verification table rewritten | Kevin: JobForge must keep discovering and evaluating with its window closed |
