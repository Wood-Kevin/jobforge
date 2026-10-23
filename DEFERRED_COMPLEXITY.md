# DEFERRED_COMPLEXITY.md — JobForge

Parked, not forgotten. Each entry: what, why deferred, trigger to revisit.
All entries below are PROPOSED with the DRAFT spec (2026-10-21, rev 4) and need Kevin's acceptance.

## Obsolete since rev 4 (kept for the record)
- **Row-level security / Postgres roles:** no Postgres. Replaced by per-user database files and per-connection SQLite authorizers (ARCHITECTURE 4.3).
- **Authentication for a browser UI:** no browser UI. The IPC token (ARCHITECTURE 5) is the only access control; there is no login because the app runs inside Kevin's Windows session.
- **Docker networking to LM Studio:** no containers.

## Web deployment floor items (edge 404 probes, Cloudflare, app robots.txt)
- **What:** Baseline floor for web app deployments.
- **Why deferred:** JobForge is no longer deployed to the web. The only listener is the agent's token-protected `127.0.0.1` IPC port, which returns 404 for unknown paths anyway. `.env*` ignore and `git ls-files` secret checks still apply.
- **Trigger:** Any public or LAN-reachable surface.

## Shared or hosted multi-user
- **What:** Several people using one JobForge instance (household server, hosted service).
- **Why deferred:** Rev 4 multi-user = one Windows account, one agent, one database file. A shared instance needs a server database (Postgres again), real auth and RLS.
- **Trigger:** A second person who cannot simply install their own copy.

## Named-pipe IPC
- **What:** Replace localhost HTTP + token with a Windows named pipe restricted to the user's SID.
- **Why deferred:** The Python server side needs undocumented asyncio internals (`ProactorEventLoop.start_serving_pipe`) or pywin32 overlapped I/O; the token design already blocks browsers and other users.
- **Trigger:** Any finding that another local process can obtain the token, or a distribution to users who run untrusted local software.

## Auto-updater
- **What:** Tauri updater plugin with signed manifests.
- **Why deferred:** Needs an update-signing key and a release host. Phase 1 builds and tests update *safety* (agent shutdown, migration, rollback) through manual installs.
- **Trigger:** More than one install to keep current, or the open-source release.

## Code signing
- **What:** Authenticode signing of the installer, host and agent.
- **Why deferred:** Costs a certificate; only Kevin installs in Phase 1 (SmartScreen warning accepted, pending Q22).
- **Trigger:** Any distribution beyond Kevin, or antivirus quarantining the unsigned agent (P1.0d records this).

## Windows Service mode
- **What:** Run the agent as a service so it works before sign-in.
- **Why deferred:** Requirement is per-user; services run in session 0 under another account, need admin to install, and complicate per-user paths and tray control.
- **Trigger:** A need to discover while nobody is signed in (e.g. an always-on host, Q20).

## Database encryption at rest
- **What:** SQLCipher or Windows DPAPI-protected keys for the database.
- **Why deferred:** The database is protected by Windows account permissions; disk encryption (BitLocker) is the system-level control. App-level encryption complicates backups and packaging.
- **Trigger:** Distribution to users on shared machines, or storing anything beyond resumes and eligibility facts.

## macOS / Linux builds
- **What:** Tauri supports both, but lifecycle, paths, autostart and installer are Windows-specific in rev 4.
- **Why deferred:** Windows-first by product decision.
- **Trigger:** Open-source release with non-Windows users.

## Workday and other undocumented sources
- **What:** The scanner's Workday adapter (undocumented career-site JSON, 2000-posting ceiling).
- **Why deferred:** Terms/stability risk; Phase 1 uses documented public job-board APIs only.
- **Trigger:** Phase 2 source expansion, after a recorded terms review.

## Adaptive polling intervals
- **What:** Poll employers that post often more frequently, quiet ones less.
- **Why deferred:** ETag 304s already make unchanged polls nearly free (spike c); needs measured posting rates.
- **Trigger:** 30 days of `source_fetches` data showing a clear split, or a budget the fixed interval cannot fit.

## Second LLM interpretation pass
- **What:** A focused model call re-reading each requirement's sentence for importance, negation and subject.
- **Why deferred:** Code cue checks (E2) are free and testable; the interpretation report measures whether they are enough.
- **Trigger:** The report shows cue checks missing a class of misreadings the golden set cares about.

## Material-change threshold
- **What:** Ignore tiny description edits (diff ratio below a threshold).
- **Why deferred:** Adds a tunable with no evidence it is needed.
- **Trigger:** Re-analysis from changed postings is a large share of GPU time.

## Retention pruning of postings
- **What:** Archive or prune old raw postings for closed, never-qualifying jobs.
- **Why deferred:** Keeping everything enables re-filtering. Measured in P1.3: 405 MB for 13,204 postings (not the ~100 MB first estimated); see "Raw posting storage size".
- **Trigger:** Database or backup size becomes a practical problem.

## Items surfaced by the P1.0b / P1.0d spikes (2026-10-21)

### ~~Forcing the autostart plugin into per-user mode~~ (resolved 2026-10-21)
- Resolved by D16 (Kevin, Q28): the plugin is dropped; JobForge writes `HKCU\...\Run` itself, and elevated launches relaunch unelevated or refuse. Kept for the record of why the plugin was removed: in spike P1.0d it wrote `HKLM\...\Run` when elevated.

### Different-user elevation test
- **What:** The D16 refusal path for "Run as different user" (elevated token belongs to another account) needs a second admin account to exercise.
- **Why deferred:** Not available in this session; the check itself is part of P1.2.
- **Trigger:** P1.2 acceptance; manual step for Kevin.

### Export of post-upgrade data before rollback
- **What:** Restoring a pre-migration backup discards everything recorded after the upgrade (seen in the spike). An export of new rows (applications, labels, profile edits) before restore would preserve the user's own work.
- **Why deferred:** Phase 1 states the loss clearly in the restore screen; an export format is new scope.
- **Partly resolved in P1.9 (v0.2.0):** `--restore` keeps the live database as `before-restore-v<N>-<time>.db` first, so
  nothing is lost for good: reinstalling the newer version and restoring that file brings it back. Still open: moving
  only the post-upgrade rows (an application, a label) into the restored older database.
- **Trigger:** The first real rollback, or any migration that ships to more than one install.

### LM Studio runtime update for qwen3.5 models
- **What:** `qwen3.5-9b-tuned` fails to load ("qwen35.rope.dimension_sections has wrong array length; expected 4, got 3"), so it could not be evaluated.
- **Why deferred:** Updating LM Studio's runtime is Kevin's call and outside JobForge.
- **Trigger:** Kevin updates LM Studio, or wants that model measured; rerun `spikes/p1_0/eval_spike.py qwen3.5-9b-tuned:fast`.

### Variance and larger-sample model evaluation
- **What:** The spike ran each configuration once on 6 postings and 22 snippets against Claude-authored labels; differences of 1-3 probes between models are not meaningful.
- **Why deferred:** The golden set (the hand labels, 20-30 postings) is the Phase 1 measuring instrument (P1.9); repeating the spike now would refine the wrong reference.
- **Trigger:** Golden set exists; then run each candidate configuration at least 3 times.

### Packaging behaviors not exercised by the spike
- **What:** Clicking the tray menu itself, a real sign-out/sign-in, sleep/resume, graceful handling of `WM_QUERYENDSESSION`/logoff, SmartScreen on a downloaded (Mark-of-the-Web) installer, Windows 10, a second machine.
- **Why deferred:** Not automatable from this session or needs another machine; each is already a Phase 1 acceptance item (P1.2, P1.9) rather than a design question.
- **Trigger:** P1.2 and P1.9 acceptance runs (manual steps for Kevin where noted).

### Source terms beyond the API documentation
- **What:** The 2026-10-21 terms review read each source's API documentation only (Greenhouse, Lever, Ashby). Their general website terms of service were not reviewed, and all three describe the API as powering an employer's own careers site without addressing third-party readers.
- **Why deferred:** Single-user, low-volume, robots-compliant, conditional-request polling with an identifying User-Agent; the API docs state no restriction on the GET endpoints.
- **Trigger:** Any distribution beyond Kevin (open-source release, a second user), or any 401/403/429 pattern from a source.

### ~~Agent handling of sign-out and system shutdown~~ (done in P1.2)
- Implemented with a hidden window (`agent/src/jobforge_agent/sessionend.py`); tested by sending `WM_ENDSESSION` to a real agent. A real sign-out has not been exercised (needs Kevin to sign out once with JobForge running and check `agent_runs.stop_reason`).

### (historical) Agent handling of sign-out and system shutdown (P1.2)
- **What:** ARCHITECTURE 3.2 has the agent stop gracefully on `CTRL_LOGOFF_EVENT` / `CTRL_SHUTDOWN_EVENT` / `WM_QUERYENDSESSION`. P1.1 handles Ctrl+C and `/v1/shutdown`; a sign-out currently ends the agent abruptly, and startup recovery repairs the state (tested: `crash_recovered`).
- **Why deferred:** The agent runs without a console window once the host starts it with `CREATE_NO_WINDOW`; the right hook (console control handler vs hidden message window) is best chosen and tested together with the host in P1.2.
- **Trigger:** P1.2 acceptance.

### One-time "JobForge keeps running in the tray" notice (Q26)
- **What:** Q26 asked for a one-time notice the first time the window is closed.
- **Why deferred:** Needs the Tauri notification plugin and an AppUserModelID for Windows toasts; the window already says it in text. Not a lifecycle guarantee.
- **Trigger:** P1.8 UI work.

### Tray menu clicks and real sign-in/sign-out, observed by hand
- **What:** The P1.2 script drives every tray action through the same functions (IPC routes, `--exit`, `--autostart`) but does not click the tray menu; sign-in is simulated by running the registered command line; sign-out by sending `WM_ENDSESSION`.
- **Why deferred:** Not automatable from this session.
- **Trigger:** Kevin, once: click each tray item; sign out and back in with autostart on; then check `host.log` and `agent_runs`.

### Sleep/resume observed on the real machine (P1.3 acceptance item)
- **What:** ARCHITECTURE 9 P1.3 asks for "sleep the PC 30 min -> no burst beyond budget". The gap detection and respread are unit-tested with an injected clock (backlog of 100 overdue employers spreads over one interval; wall clock jumps do not double-poll), but a real sleep was not performed.
- **Why deferred:** Putting Kevin's PC to sleep is not something this session should do.
- **Trigger:** Kevin, once: with JobForge running, sleep the PC for 30+ min, wake it, then run `python scripts\verify_discovery.py` and read the "Jitter, no bursts" and "Request volume" sections.

### Watchlist hygiene
- **What:** The scanner watchlist includes Workday, Workable and We Work Remotely entries; JobForge Phase 1 skips them (reported by the import).
- **Why deferred:** Those sources are out of Phase 1 scope.
- **Trigger:** Phase 2 source expansion.

### Raw posting storage size (measured P1.3)
- **What:** 84 employers, 13,204 postings -> 405 MB database. raw_postings: payload 171.6 MB, `description_raw` 124.1 MB, `description_text` 87.4 MB. For Greenhouse and Ashby `description_raw` is a byte-for-byte copy of a field inside the payload (~31% of the file). Payloads compress 3.1x with zlib (300-row sample). Daily backups (keep 7) of a 400 MB file are ~2.8 GB.
- **Options:** (a) store `description_raw` only when it is not recoverable from the payload (Lever's stitched sections), derive it otherwise: about -30%, no format change; (b) compress payload and raw markup (zlib BLOB columns, migration): about -60%, still lossless; (c) both. Rejected: pruning rejected postings (breaks re-filtering, requirement "preserve rejected jobs").
- **Why deferred:** Not harmful at current scale (disk has ~1 TB free; growth is only new or materially changed postings). It is a storage-format decision for Kevin, not a P1.3 defect.
- **Trigger:** Database over 1 GB, daily backup over 60 s, or Kevin choosing an option.

## Filter regression suite depends on the scanner's config.yaml (P1.4)
- **What:** `agent/tests/test_filter_rules.py` (the scanner's filter + salary suite: 175 cases, plus 2 JobForge additions) reads a tuned profile from `../job-scanner/config.yaml` (or `JOBFORGE_SCANNER_CONFIG`). Without it the module is skipped with a printed reason; the synthetic-profile tests in `test_filter_pipeline.py` still run.
- **Why deferred:** The profile is personal data and stays out of the repo. Once JobForge holds the profile, the scanner file stops being the source of truth and will drift from it.
- **Trigger:** The first profile change made in JobForge instead of the scanner, or retiring the scanner. Then point the suite at a profile exported from the JobForge database (still outside the repo).

## "Evaluate anyway" cannot be undone (P1.4)
- **What:** A `filter_overrides` row is insert-only and permanent; while it exists, every re-filter keeps queueing the job's new posting versions for analysis.
- **Why deferred:** No UI yet (P1.8) and no undo requirement in the spec. Adding one needs either a delete grant or a `revoked_at_ms` column (preferred: keeps history).
- **Trigger:** P1.8 rejected view, or an override Kevin wants to take back.

## Re-filter covers each job's current posting only (P1.4)
- **What:** A re-filter gives every job (open or closed) a verdict for its *current* raw posting. Older raw versions of a changed posting keep only the verdicts they got under earlier profiles.
- **Why deferred:** A verdict on an outdated version under a newer profile has no use in Phase 1 (nothing reads it); filtering every historical version would multiply the work by the version count.
- **Trigger:** A history view that compares old posting versions under the current profile.

## Duplicate resume uploads are stored twice (P1.5)
- **What:** Uploading the same file again creates a second `resumes` row (P1.5 live: two of the 14 PDFs were byte-identical copies in different folders, stored as two rows with one sha256).
- **Why deferred:** Harmless at this size (~30 KB each) and the UI does not exist yet to say "already uploaded as ...". Refusing silently, or deduping by sha256, would both hide something from Kevin.
- **Trigger:** P1.8 resume view; return the existing id with a `duplicate_of` field instead of inserting.

## Loaded context length is not checked (P1.6)
- **What:** The default config says `context_length: 16384`, but the agent cannot see what context LM Studio actually loaded the model with (the OpenAI-compatible API does not report it). If LM Studio JIT-loads Gemma with a smaller default context, long postings fail as `context_overflow` (measured: HTTP 400 `exceed_context_size_error`, never silent truncation), visible per analysis but not explained.
- **Why deferred:** Checking needs LM Studio's own REST API (`/api/v0/models`), a provider-specific path the single-client design (6.8) avoids. Failures are already visible, not wrong.
- **Trigger:** Any `context_overflow` failure in practice, or a second provider (Ollama) whose load behavior differs.

## Model config edits do not reset the test (P1.6)
- **What:** `tested_at_ms` is set by a passing load-and-snippet test. There is no route to edit a config's params or model yet; when one is added it must clear `tested_at_ms` and `enabled`, or an untested combination would run.
- **Why deferred:** Configs are seeded by migration and only enabled/disabled in Phase 1 so far; editing comes with the P1.8 settings view.
- **Trigger:** The first route or UI that changes `model_configs.params`, `model_id` or `base_url`.

## Recall rows are stored for every uncovered eligibility sentence (P1.6)
- **What:** The recall scan (D15) stores an `unextracted_eligibility` row for each eligibility-cue sentence the model did not cover, including good news ("We do sponsor visas!", Anthropic) and negations ("No clearance required"). Each row carries the code's reading (`cue_polarity`, class), but E2 does not decide which ones should make eligibility UNKNOWN.
- **Why deferred:** That is an assessment decision (P1.7, 6.5): e.g. a sponsorship sentence matters only if the profile says sponsorship is needed. Dropping rows in E2 would hide model misses, the failure D15 exists for.
- **Trigger:** P1.7 eligibility rules; the golden-set REVIEW share (Q16) shows how many REVIEWs come from recall rows.
- **Resolved in P1.7:** E5 decides (ARCHITECTURE 6.5, "Points the rules above leave open"): negated rows are ignored, rows met by a verified fact pass, everything else is UNKNOWN, never a block. The rows stay in E2 unchanged.

## A type-only interpretation conflict makes a job REVIEW whatever the profile says (P1.7)
- **What:** 6.5 says any conflicting hard requirement makes eligibility UNKNOWN. In the P1.7 live run, every Vultr posting's state-list residency rule is a `conflict` only because Gemma typed it `work_authorization` (code: `residency`); the verified residency fact meets it, yet the job is REVIEW with a question no profile answer can resolve. `scripts/verify_evaluation.py` prints how many REVIEWs have no other cause.
- **Why deferred:** It is Kevin's rule (D7, 6.5) and the safe direction (never a wrong ELIGIBLE). A candidate change: for a conflict whose only disagreement is the eligibility type, check the requirement under both types and treat it as answered only if verified facts meet both; it would still never block. That is a new verdict rule version, decided by Kevin.
- **Trigger:** Kevin's decision, or the golden-set REVIEW share (Q16) showing these as "obvious" REVIEWs.
- **Resolved 2026-10-23:** Kevin made v2 active after the measurement. Before that it was **built, not active (P1.7, Kevin asked for it to be built and measured):** `verdict_rules/v2.yaml` (`eligibility.type_only_conflict: check_both`), layer-B cases and a DB test; `VERDICT_RULE_VERSION` stays `v1`. `scripts/compare_verdict_rules.py DATA_DIR v1 v2` recomputes both in a temporary copy (zero model calls) and lists every job whose result changes. Switching is a one-line version change plus a recompute.

## A verify or any small profile edit re-runs E3 for every open job (P1.7)
- **What:** Snapshots are content-addressed over every item including its status, so verifying one item, or several edits in a row, makes a new snapshot and queues a new evaluation (one model call, ~15-25 s with Gemma) for every open job. Pending ones are superseded, so only the evaluation already running is wasted, but a burst of edits still costs a full re-run.
- **Why deferred:** It is D10 as agreed (E3-E4 per snapshot) and correct; it is only slow. Cheaper options each add a rule: recompute E4 from the previous matches when only `status` changed (basis is code-computed), or wait for the profile to be idle for N seconds before snapshotting.
- **Trigger:** Re-evaluation after profile edits gets in Kevin's way (UI shows evaluations pending for long after an edit), or open jobs exceed ~200.
- **Resolved (P1.7 follow-up):** stored answers are reused for an identical E3 request (ARCHITECTURE 6.4, migration 0005). A verify, a fact or completeness change and an E4/E5 version bump cost no model call; an edit to what an item says (text, kind, years, dates) still re-runs E3 for every open job, because each prompt lists the whole profile. That part remains deferred, same trigger.

## Soft-skill requirements are almost always "unknown" (P1.7)
- **What:** "Strong written and verbal communication skills", "Proficiency with the English language" and similar lines are negotiable requirements no profile item states, so E3 returns `unknown` and they count toward the 30% uncertainty that makes fit INSUFFICIENT.
- **Why deferred:** Dropping them needs a lexicon or a class E2 does not have; marking them covered would be an inferred claim, which D2 forbids. Measured effect first.
- **Trigger:** Golden-set REVIEWs (Q16) where fit is INSUFFICIENT mostly because of such lines.

## Verdict thresholds are package data, not user settings (P1.7)
- **What:** `verdict_rules/v1.yaml` ships with the agent (`--collect-data`, checked by `scripts/build.ps1`). Tuning means a new file and `VERDICT_RULE_VERSION`, then a recompute (zero model calls).
- **Why deferred:** 6.5 calls for versioned rules measured against labels; a settings screen would let thresholds drift without a version.
- **Trigger:** The golden-set report (P1.9) shows a threshold worth tuning.

## REVIEW share is 91% on the drill profile (P1.7)
- **What:** On the 44 real postings, rules v1 give REVIEW 40, STRETCH 3, SKIP 1, APPLY 0 (`docs/evidence/p1_7_evaluation_live.txt`). Fit is INSUFFICIENT on 35, mostly because 184 of 442 negotiable requirements are unknown: the drill profile has no skill items, and uncited model gaps count as unknown (D7). Eligibility UNKNOWN on 19 (type-only conflicts, self-reported drill facts).
- **Why deferred:** The profile was a drill stand-in; the numbers say little about a real profile. 6.5 and Q16 tune thresholds against the golden-set labels, not intuition, and the REVIEW view is meant to resolve these by asking for the missing items and facts.
- **Trigger:** a real profile entered (P1.8 UI): rerun with `scripts/compare_verdict_rules.py` and the replay approach (stored model answers, zero model calls); golden-set report (P1.9).

## The window is functional, not friendly (P1.8 -> P1.8b, next)
- **What:** Kevin: "this beast is not user friendly at all". Picked: he cannot start it himself (no installed current build, no
  first-run data), internal jargon on screen (NEGOTIABLE / CONFIRMED / flag names / rule numbers), too much on screen (every
  requirement, 85 employers), manual setup (watchlist, search profile, model test/enable, profile import), and JSON in
  front of users ("most users aren't going to know what json even is").
- **Why deferred:** P1.8 acceptance is functional; Kevin chose to commit it and do a focused usability pass before P1.9.
- **Resolved in P1.8b** (SPEC change log), except the profile step below.

## ~~Building a profile inside the app~~ (P1.8b; resolved in P1.8c)
- **Resolved in P1.8c:** "Suggest items" on an uploaded resume (SPEC change log, ARCHITECTURE 4.2 / profile routes). What remains is recorded below.
- **What:** Setup step 4 loads a prepared profile file (one was drafted from resumes outside the app and reviewed
  by him). A new user has no such file; their only path is adding items one by one in Profile. Uploaded resumes are stored
  and their text extracted (P1.5), but nothing turns that text into proposed items.
- **Why deferred:** doing it well is a model call whose output must land as unconfirmed claims (D2: inferred items are
  never cited or verified) with a review screen; that is a feature of its own, not wording.
- **Trigger:** a second user, or Kevin rebuilding the profile from a new resume.

## UI interaction tests are manual (P1.8)
- **What:** Component behavior (clicks, forms, live reload) was verified in the real window by screenshot and scripted clicks;
  only the sanitizer and the route contract are automated. There is no webview automation in CI.
- **Why deferred:** WebView2 automation (WebDriver for Tauri) is a setup of its own; P1.9's verify.sh and the release drill
  exercise the installed app.
- **Trigger:** a UI regression that a screenshot pass missed, or the usability pass adding forms whose validation matters.

## P1.9 measurements that need inputs (P1.9)
- **What:** (1) Golden-set report numbers: the tooling is live ("Your call" on a job, `jobforge-agent golden`,
  Status shows the count) but the report needs Kevin's 20-30 labels on real jobs. (2) Interpretation report
  (`golden --suite interpretation`): the drill run reached no model (LM Studio was not running; 61 ConnectErrors),
  so no agreement numbers exist yet.
- **Why deferred:** both need something only Kevin can provide: the user's own calls, and LM Studio running.
- **Trigger:** 20 labels in Status ("Your calls on jobs: 20 of ..."); LM Studio's server started. Then
  `VERIFY_INTERPRETATION=1 bash verify.sh golden` and record the output in `docs/evidence/`.

## verify.sh manual rows (P1.9)
- **What:** Rows that need a person or a second account are printed as instructions, not gathered: window close
  survival, elevated agent/host launches, different-user elevation, live refresh and inert-posting screenshots.
- **Why deferred:** automating them needs WebView2 automation and a second Windows account (see "UI interaction
  tests are manual").
- **Trigger:** a regression in one of those behaviors, or WebView2 automation added.

## Installer built from an elevated shell (P1.9)
- **What:** PyInstaller warns "running as administrator ... PyInstaller 7.0 will block this" when `build.ps1` runs
  elevated (this session's shell). The build is otherwise identical.
- **Why deferred:** nothing breaks today; building unelevated is a habit, not code.
- **Trigger:** upgrading PyInstaller to 7.x, or any build failure mentioning administrator.

## Resume suggestions: recall not measured (P1.8c)
- **What:** Code guarantees the wording (every kept suggestion quotes the resume) but no test measures recall. Measured
  once by hand: a prompt draft kept 10 of the 27 entries in one resume's skills section; the final r1-v1 line ("list
  every entry of a skills section") brought both tested resumes to every entry (27/27, 25/25). Recall of skills that
  appear only in experience bullets is not measured. Missing items cost a manual add; E3 then reads the profile as
  thinner than it is (more REVIEW). Eligibility facts in a resume (clearance, citizenship)
  are deliberately not suggested; the user enters them in the typed form.
- **Why deferred:** measuring recall needs a labeled item list per resume; a reviewed `profile_draft.json` (46
  items) could serve for several resumes but is one person's data.
- **Trigger:** a user reports missing items, or the golden-set work in P1.9 adds profile labels.
