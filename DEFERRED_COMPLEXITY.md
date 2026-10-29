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
- **Trigger:** Kevin updates LM Studio, or wants that model measured; rerun `spikes/p1_0/eval_spike.py qwen3.5-9b-tuned:fast` (spike code is in the git history before 2026-10-24, and in Kevin's local `spikes/` folder).

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
- **What:** The recall scan (D15) stores an `unextracted_eligibility` row for each eligibility-cue sentence the model did not cover, including good news ("We do sponsor visas!", Company B) and negations ("No clearance required"). Each row carries the code's reading (`cue_polarity`, class), but E2 does not decide which ones should make eligibility UNKNOWN.
- **Why deferred:** That is an assessment decision (P1.7, 6.5): e.g. a sponsorship sentence matters only if the profile says sponsorship is needed. Dropping rows in E2 would hide model misses, the failure D15 exists for.
- **Trigger:** P1.7 eligibility rules; the golden-set REVIEW share (Q16) shows how many REVIEWs come from recall rows.
- **Resolved in P1.7:** E5 decides (ARCHITECTURE 6.5, "Points the rules above leave open"): negated rows are ignored, rows met by a verified fact pass, everything else is UNKNOWN, never a block. The rows stay in E2 unchanged.

## A type-only interpretation conflict makes a job REVIEW whatever the profile says (P1.7)
- **What:** 6.5 says any conflicting hard requirement makes eligibility UNKNOWN. In the P1.7 live run, every Company U posting's state-list residency rule is a `conflict` only because Gemma typed it `work_authorization` (code: `residency`); the verified residency fact meets it, yet the job is REVIEW with a question no profile answer can resolve. `scripts/verify_evaluation.py` prints how many REVIEWs have no other cause.
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
  every entry of a skills section") brought both tested resumes to every entry (27/27, 25/25). Since r1-v2
  (2026-10-25) a role carries its resume lines, so experience stated only in a role's lines reaches E3 through the role
  item (one real resume: 5 of 5 roles, every line found); recall of those lines is otherwise not measured. Missing items cost a manual add; E3 then reads the profile as
  thinner than it is (more REVIEW). Eligibility facts in a resume (clearance, citizenship)
  are deliberately not suggested; the user enters them in the typed form.
- **Why deferred:** measuring recall needs a labeled item list per resume; a reviewed `profile_draft.json` (46
  items) could serve for several resumes but is one person's data.
- **Trigger:** a user reports missing items, or the golden-set work in P1.9 adds profile labels.

## Built-in list goes stale between releases (P1.10)
- **What:** `discovery/directory.json` is a snapshot (version 2026-10-23: 5,855 boards from Common Crawl
  CC-MAIN-2026-43 and -39). Boards that close turn `dead` and stop being polled; companies that move to a board
  after the build are missing until someone names them.
- **Why deferred:** rebuilding took 1 h 53 min with cached index pages (one spaced request per candidate; Common Crawl 502/504s add more on a cold run) and belongs
  in the release routine, not in the agent; the agent never fetches a list from the network.
- **Trigger:** each release (run `scripts/build_directory.py --crawls 2` from `agent/`), or Status showing more
  than 10% of the built-in list dead.

## Built-in list: Common Crawl gaps (P1.10)
- **What:** (1) 4 of 5 index pages for `job-boards.greenhouse.io` in CC-MAIN-2026-39 failed after six tries (502/504)
  and are listed in the file's `index_queries_failed`; the same host's pages from CC-MAIN-2026-43 were read.
  (2) Lever is thin: 280 candidates, 210 kept (Common Crawl gets few Lever pages). (3) Lever and Ashby expose no
  company name, so names are the title-cased id ("Openai"), and one name can match two different companies on
  different boards ("Ramp": Ashby `ramp`, 171 jobs, and Greenhouse `ramp`, "RAMP", 1 job); naming it watches both.
- **Why deferred:** adding a name source or a disambiguation step is new scope; add-by-name already finds
  companies missing from the list through the board APIs (live: "Mistral" found on Lever).
- **Trigger:** a rebuild (retry the failed pages first), a user report of a wrong or missing company, or a
  second list source.

## Ashby budget headroom with the built-in list (P1.10)
- **What:** 2,460 Ashby boards at 24 h is about 103 requests/h against the 120/h budget (Greenhouse: 3,185 boards,
  about 133/h of 300; Lever: 210, about 9/h of 120). That leaves room for about 4 of the person's own Ashby
  companies at 15 min; beyond that the budget gate defers polls and every Ashby interval stretches evenly
  (`budget_deferred`), nothing fails.
- **Why deferred:** raising the 120/h budget is a terms-of-use judgment (ARCHITECTURE Appendix A), Kevin's call.
- **Trigger:** `budget_deferred` lines for ashby in the agent log, or Kevin deciding a higher budget.

## Storage at built-in-list scale (P1.10)
- **What:** measured on fresh evidence roots, 30 min each: 30.0 KB per posting with plain text; 11.5 KB compressed
  (Kevin: "lets do it"); 2.6 KB once rejected postings keep no text (Kevin: "no reason to keep posts that just
  rejected and don't meet filters"; SPEC D9 amendment). A kept (passing) posting's text is about 6 KB compressed.
  The list's 217,026 open jobs project to about 0.6 GB after the first full day, plus about 6 KB per pass; it grows
  with each material edit (a new 2.6 KB raw row), since posting records are kept.
- **Why deferred:** pruning records of closed rejected jobs would change retention again; not needed at this size.
- **Trigger:** a database over 2 GB.

## Evaluation calibration 1: next evaluation work (2026-10-24; before/after + Kevin's approval, labels optional)
Not an open-ended deferral: this is the next change to E3-E6, waiting only on two actions by Kevin.
- **What:** an audit of the 9 APPLY/STRETCH recommendations (the real data, prompt `m1-v1`, rules `v2`, Gemma
  4 12B) found the model marks a match `direct` when the cited item is broader than what the requirement names:
  7 of about 46 direct matches (cases D1-D7 in `docs/calibration/calibration_1_cases.json`: Azure Virtual
  Desktop from Azure VMs, Entra ID and Conditional Access from Active Directory, SaaS license management from
  Microsoft 365, JSON and stack traces from Linux, SCA/SBOM and NAC from certifications, named scripting
  languages from "scheduled scripting"). None changed a recommendation on its own. The same audit found
  requirement qualifiers dropped (Q1-Q2: "at a B2B SaaS company", "for a SaaS or developer-focused product"
  counted as years alone) and the model's stored gap explanation contradicting the code-computed years
  (Y1-Y2: "approximately 1.5 years" against `years_shown` 1.83; stored in `gap_size.detail` and returned
  by the job route, not rendered by the window today). Controls C1-C3 are correct matches that must not change.
- **Requirements (Kevin, 2026-10-24), binding on any proposed change:**
  1. Keep a requirement's full meaning, including qualifiers (B2B SaaS, developer-focused products, specific
     technologies, work environments). Years alone never satisfy a qualified requirement.
  2. Use the code-computed experience duration in both decisions and every displayed or stored explanation.
  3. Keep `MATCH_PROMPT_VERSION = m1-v1` and `VERDICT_RULE_VERSION = v2` until a change is approved under the
     evaluation rule below (it no longer waits for golden labels).
  4. The direct-versus-transferable issue is an explicit item of this calibration, with D1-D7 as regression
     cases (and C1-C3 as controls against over-correction).
  5. Never verify eligibility facts automatically or infer missing profile experience.
- **Evaluation rule (Kevin, 2026-10-24; replaces "gated on golden labels": "I don't think myself or a regular user
  would walk through skips and label them"):**
  1. Every change to recommendation behavior keeps a before/after on real job data, the regression cases in
     `docs/calibration/calibration_1_cases.json`, and Kevin's explicit approval before it is merged or deployed.
  2. Application status (applied, interviewing, offer) and "I'd skip it" are high-confidence preference signals;
     saved is a weaker interest signal; views and inactivity are not verdicts.
  3. Preference stays separate from qualification and eligibility accuracy: applying to a stretch does not mean the
     fit assessment was wrong, and skipping a qualified job does not mean the match was bad.
  4. Deterministic eligibility routing is validated by tests and actual posting text, not by subjective labels.
  5. Manual labels ("Your call") are optional, never required for calibration.
  Also: on-call participation is an availability fact, not a negative job attribute; pay and working conditions
  are separate considerations.
- **Then:** for each proposed change, a before/after on real data (recommendation changes per job, line changes,
  noise baseline beside it) and every case in the file, then stop for Kevin's approval. Not yet built: counting
  applications and skips per recommendation as an ongoing preference scorecard (kept apart from accuracy, rule 3).
- **Candidate changes, not decided:** the E3 prompt stating when `direct` applies; an E4 check that a `direct`
  citation names the requirement's technology; years from qualifying roles only; the years tolerance in
  `verdict_rules` (a shortfall is minor if at most 2 years *or* at most 40%, so 1.83 of "3-5 years" is minor).
- **Added 2026-10-24 (case F1, Company H):** a COMPETITIVE fit, so APPLY, with zero direct matches. Rule
  `assess.fit` asks only for no material gap and at most 2 unknowns, so 5 transferable bridges (Azure to AWS,
  "scheduled scripting" to Python/JavaScript, VPN troubleshooting to container and browser debugging) plus
  the Q2 gap are enough. It is the only such APPLY of 19 today. Candidate, not decided: a minimum direct share
  for COMPETITIVE (a `verdict_rules` change, so a new `VERDICT_RULE_VERSION`), compared alongside the Q2 fix,
  which on its own may turn this job's gap material and its fit STRETCH.
- **Added 2026-10-24 (second audit, pasted by Kevin; each claim checked against the live data):**
  - Q3 (Re:Build, APPLY): "3-5 years ... Microsoft Defender, Purview, and Sentinel" is a minor gap because 1.83
    years of general IT is within the tolerance; the products are the qualifier and none is shown (requirement 1).
    As material: STRETCH.
  - T1 (Company J, APPLY): about 4 months against "2 years or more" is minor. With `years_tolerance_abs: 2` no shortfall
    against a requirement of 2 years or less can be material; the comparison must include the tolerance alone.
  - Company D (D3, D4): the audit counted the two overclaims as `unknown`, which makes 3 unknowns and REVIEW. The
    prompt's own definitions make cited adjacent experience `transferable`, which leaves 1 unknown and APPLY.
    The comparison shows both readings; the expected value stays "not direct".
  - R1-R2 (Company F, Company V, REVIEW with exactly 3 unknowns): the resume states communication and client-facing work, but
    no profile item does, and the resume suggester proposed none (all 179 open inbox suggestions come from
    evaluations, none from the resume). Not a matcher change: the matcher reads snapshot items only (D2) and
    requirement 5 forbids inferring them. Measured as resume-suggestion recall (`RESUME_PROMPT_VERSION`).
- **Added 2026-10-24 (E2, cases E1-E2): eligibility lines the code does not type go to the matcher.** Across
  current recommendations, 61 lines in 43 jobs (34 of them REVIEW) were typed as eligibility by the model
  (work_authorization 43, travel 12, physical 4, sponsorship 1, license 1) and given no type by E2, so they were
  matched as experience: 56 unknown, 5 transferable. the confirmed facts never see them. Company N "Technical Support
  Specialist, US East Coast" (job 108580): "candidates must be located in the US in Eastern timezone" (1558) was
  typed residency and met by his Florida fact, while "Remote in Eastern US (required)" (1556) and "Located in US,
  eastern timezone (remote)" (1565) were not, and with "US Time (Eastern) ... 8A-5P" (1557, a schedule) made 3
  unknowns and REVIEW. Also "Candidates must have authorization to work in the United States" (job 16212,
  requirement 216) went unchecked against a confirmed citizenship fact. Candidate, not decided: E2 treats a
  model-only eligibility type as a conflict (as `type_only_conflict: check_both` already does for hard lines) or
  widens its location cues; an `INTERPRETER_VERSION` change, re-analysis without new model calls for E1. The
  window now says so on such a line instead of offering an experience answer (v0.3.7); that is display only.
- **Reading fix prepared, NOT active (branch `calibration/eligibility-routing`, 2026-10-24; Kevin: "write both"):**
  interp-v3 cues (availability; zone and remote-region residency, not when hours or alignment follow; "authorization
  to work"), the recall scan skipping availability, the E1 schema fixed with e1-v1 (no E1 re-run), rules v3 = v2
  thresholds with an availability check and residency time zones (the person's `time_zone` settles a split state),
  and type-only conflicts asking for the fact. Before/after: `scripts/compare_reading_fix.py`, same database copy,
  main's code vs the branch's, zero model calls, stored E3 answers reused; the before run reproduced all 17 stored
  recommendations. Result in `docs/calibration/reading_fix_before_after.txt`: 17 jobs re-read (95 not: text dropped
  by the 7-day filter). APPLY 4 kept, 2 APPLY -> REVIEW (Company D x2: an on-call duty joined to a SOC 2 line, now
  asked), STRETCH 3 kept, REVIEW 8 kept; Company N unknown 3 -> 0 (fit INSUFFICIENT -> COMPETITIVE), now waiting on time
  zone and schedule. Two earlier drafts were rejected by this comparison (round 1: benefit holidays and team prose
  "on-call" raised questions, 3 APPLY -> REVIEW; "Pacific Time Zone business hours" read as residency, a false fail).
  Not measured: golden labels (1 so far), E3 drift on rollout (11-14% baseline), jobs whose text was dropped.
  Rollout pieces now written on the branch: the analysis worker re-reads a posting's stored E1 answer when only the
  interpreter changed (no model call), and agent startup queues a current-version analysis for open passing jobs
  (before, a new version reached only re-filtered jobs). Final run on fresh data and a rollout dry run (branch
  code, a model server counting calls): 17 analyses from stored answers with 0 model calls, requirements equal to
  the before/after for all 17; E3: 12 stored answers reused, 5 jobs need the model; 1 job (128056) needs E1 under
  any version (its first analysis failed). On-call: an availability fact only; opportunity value never reads a
  posting's duties (test). Approved by Kevin ("proceed") and merged 2026-10-24, v0.3.9.
- **Noise baseline, required before any before/after comparison (Kevin, 2026-10-24):** E3 answers move when
  unrelated profile text changes. Measured on the real data when he added one skill (item 55, AI tools) and then
  reworded it, on the 17 jobs re-scored each time: among requirements that cited item 55 in neither run, 24 of 212
  (11%) changed match type between snapshots 111 and 115 and 27 of 190 (14%) between 113 and 115 (e.g. Company M's
  scripting line direct, transferable, direct; Company Q 1523 and 1530 direct to transferable; gaps to unknown on
  Company K 1689, 1690, 1693). Between 114 and 115, where only the item's confirmation changed, 0 of 212 changed,
  but that is answer reuse, not a determinism result: the confirmation does not change the E3 request, so those
  answers were reused (`reused_from`, no model call). Whether an identical request re-sent to the model gives the
  same answer is not measured; the comparison needs that too (re-run a sample with reuse bypassed), to separate
  sampling noise from sensitivity to the profile context. Consequence: a prompt or profile change that moves a match is not shown to
  have caused it unless the move exceeds this baseline. The comparison must (a) state the baseline flip rate on
  untouched requirements alongside every result, measured between two runs that differ only in unrelated profile
  text, and (b) count a case (D1-D7, Q1-Q3, T1, F1, C1-C3) as fixed only if it holds across at least two such
  context variations. Measured with a read-only script against the live database; no evaluation was re-run for it.
  - Not a defect: the work-mode factor is `unknown` on all 112 recommendations because the active profile has no
    work-mode preferences (the form's Remote/Hybrid/On-site are "no preference"), not because jobs lack a mode
    (50 of 112 state one).

## Host HTTP client: ureq 2 to 3 (2026-10-25)
- **What:** Dependabot PR #3 (ureq 2.12.1 to 3.5.0) was closed: ureq 3 changes the API and the host does not build
  with it. The host uses ureq only for its own agent on localhost (`desktop/src-tauri`), so 2.x is not a risk today.
- **Why deferred:** a port with no user-visible gain; it belongs in a dependency pass, not between features.
- **Trigger:** a security advisory for ureq 2, or Tauri or another dependency requiring ureq 3.
- **Also dismissed the same day:** Dependabot alert 1 (`glib` < 0.20, moderate), as not used: glib is Tauri's Linux
  (GTK) dependency and JobForge builds only for Windows. Revisit if a Linux build is ever added.

## Over-credit on practices the person lacks (2026-10-25) — partly fixed by E3 `m2-v1` and rules v4
- **Fixed 2026-10-25 (branch `over-credit`):** E3 prompt `m2-v1` narrows "transferable" to the same task with
  another tool or setting; rules v4 make many unknowns an `unshown` STRETCH (never SKIP) and tighten the years
  tolerance to 1 year / 25% (ARCHITECTURE 6.3-6.5). Five models over-credited the same requirements, so the cause
  was the prompt, not one model. What remains: Gemma still calls some named technologies "direct" (Python and SQL,
  AWS managed services), so with Gemma the APPLY list barely moves; GPT-5.6-Luna under `m2-v1` was 19 of 25 right and
  dropped the APPLYs on things the person lacks. Trigger for the rest: SPEC D18 (ChatGPT) or a larger local model.
- **Before/after on the branch (2026-10-25, 40 real jobs, Gemma, `m2-v1` + v4, app's EvaluationWorker on a copy):**
  APPLY 13 -> 8, STRETCH 11 -> 25 (19 `unshown`), REVIEW 12 -> 6, SKIP 4 -> 1. Not merged: the APPLY drop is the wrong
  jobs. The v4 years tolerance exposed a masked defect: E4 counts years only from the items the model cites, and
  models cite one role, so 0.92-1.0 years were shown where the person's IT roles add up to about 1.9 (four
  support jobs demoted). v3's 2-year tolerance hid it. Gemma keeps APPLY on three jobs asking for skills the person
  lacks (it calls "Python and SQL" direct) and lost evidenced SKIPs (now unknowns). Next: years across every item showing the
  experience, then ship `m2-v1` + v4 with SPEC D18 (GPT-5.6-Luna gave a clean APPLY list under `m2-v1`).
- **Years count, 2026-10-26 (E3 `m2-v2`, same branch):** the prompt now asks for every item that shows the
  experience on a years requirement (ARCHITECTURE 6.3). Same 40 jobs with Gemma: APPLY 13 -> 9 (m2-v1: 8); Gemma
  now cites all three IT roles on two jobs but still only two on three others ("technical support" requirements),
  so those stay STRETCH on a 0.92-year count. GPT-5.6-Luna on the 7 years jobs: all three roles cited, the three
  "2+ years" support jobs APPLY, the "3-5 years" operations job STRETCH (correct); it answered one "3+ years" line as
  unknown (APPLY), a model error. Trigger:
  Gemma's count is fixed only by a stronger model (SPEC D18 or the 64 GB machine); code adding up all roles was
  rejected (it cannot tell which roles count).
- **Rejected and parked: "Things you don't have"** (branch `absences`, commit 505c68e, not merged). A profile list
  of absences applied in E4 (a new migration): accurate when filled in (Gemma 12 -> 21 of 25, in-sample), but Kevin:
  "things you don't have is not a realistic fix for users" (nobody can list everything they lack). Could return as
  an answer to a REVIEW question ("Do you have SQL?") rather than a list to fill in.
- **What:** with roles now carrying their resume lines (r1-v2), both Gemma and gpt-oss still credit some requirements
  the person confirmed they do not meet: deep SQL, operating cloud managed services, sprint planning (Gemma), audit
  evidence preparation (Gemma). On six requirements the person answered, Gemma was right on 2, gpt-oss on 4, with the
  fuller profile as before it. An E3 prompt rewording (m1-v2: all parts and qualifiers, narrower "transferable") made
  both models stricter across the board (Gemma APPLY 12 to 4) without being more right on those six; dropped.
- **Options, not chosen:** (a) a profile list of confirmed absences ("I do not have: ..."), matched like the
  completeness flags, so an absent practice reads as a gap; (b) APPLY needing more direct matches (a verdict rules
  version, no model calls); (c) gpt-oss as the default model (more right on the six, about 3x slower than Gemma with
  the fuller profile: 70 s vs 24 s per job, median).
- **Why deferred:** each changes recommendations; six answers are too few to choose between them.
- **Trigger:** the person labels more requirements (Your call), or reports another job marked APPLY on something
  they lack.
- **Measured 2026-10-25 (six local models, 17 jobs, 25 labeled requirements):** Qwen3.6 35B-A3B with reasoning off
  was right on 17 of 25 with 2 over-credits, the same in two runs; Gemma 4 12B 11 with 13 over-credits. On a 12 GB GPU
  Qwen spills to system memory (about 20 tok/s): E3 about 67 s per job, the resume read 172 s (limit 300), and E1
  timed out on 4 of 20 postings at the 120 s limit. Not switched: see "Model server on another machine".

## Model server on another machine (2026-10-25)
- **What:** run LM Studio on a second machine on the home network with more memory (a 64 GB Mac is planned), so a
  larger model such as Qwen3.6 35B-A3B fits in memory and runs at full speed, and point the model config's `base_url`
  at it. The agent does not restrict `base_url` to loopback; the window has no field to change it, and the AI model
  card's setup steps and "nothing is sent" wording assume this PC.
- **Needs:** a way to set the server address (window or migration); SPEC/ARCHITECTURE note that prompts with profile
  items then cross the local network as plain HTTP; the same 17-job, 25-label comparison rerun against that server.
- **Rejected for now:** a free cloud API (Google AI Studio's unpaid tier may use and human-review prompts, and its terms
  say not to send personal information); a paid cloud tier remains an option for users without a strong machine.
- **Trigger:** the second machine is available.

## ChatGPT integration: what the first build leaves open (2026-10-26, SPEC D18)
- **Posting reading on ChatGPT is not ready (measured 2026-10-26):** E1 through GPT-5.6-Luna on 20 of the
  person's postings, against the stored Gemma readings, app's own prompt, parser and E2 (copy of the data): every
  quote found (477 of 477), and on the 248 lines both read, the class agreed on 239. But Luna listed 477
  requirements to Gemma's 313: it takes the posting's duties ("administer Azure Virtual Desktop", "take ownership
  of assigned support cases") and soft skills ("customer focus", "self-direction") as requirements, which Gemma
  leaves out. Each one becomes a line to match, so more unknowns and more REVIEW. About 40 s and 1,650 input /
  2,100 output tokens per posting. Keep posting reading on this PC. Trigger: an E1 prompt revision that tells
  duties from requirements, measured on the same 20 postings with both models (an E1 change: version bump, and
  the person's approval).
  **Addressed 2026-10-27 in code, not the prompt** (`interp-v4`, ARCHITECTURE 6.4): duties under a duties heading
  are context. With Luna's readings of the 20 postings, 91 lines set aside, all duties on review. Attitude lines
  that start with a desire, interest, passion or a trait list are context since `interp-v8` (ARCHITECTURE 6.4: 7
  rows on the person's data); still open are those worded otherwise ("you show more than you tell", "a commitment
  to providing excellent service" mid-line). Its split lines are merged since `interp-v5` (ARCHITECTURE 6.4: 300 ->
  269 lines on the 20). A duty that names its tools with a tool word
  ("investigate ... using Go, TypeScript and SQL", "administer Azure Virtual Desktop") keeps them as a nice-to-have
  since `interp-v6`; one that names them without one ("the Technical Support queue in Zendesk", "design and
  implement AWS network architectures", "asset inventory in DCIM") is still set aside with them. Trigger: the
  person moves posting reading to ChatGPT and finds REVIEW questions about attitude lines, or a job marked APPLY
  that asks for tools only in duties worded without a tool word.
- **A real cap was never observed:** the usage-limit codes and their shape come from the docs; the tests use a
  recorded event of that shape. Trigger: the first `inference_capped` line in agent.jsonl; check the stored error
  matches `CAP_CODES` and the wait (15 min doubling to 2 h) suits how fast the plan recovers.
- **Resume suggestions, worst case:** a cloud call that times out (300 s) and then the fallback can outlast the
  host's 330 s limit for that route; the window then shows a timeout though the agent finishes. Trigger: a report
  of it; fix by a shorter cloud wait for that route or a background job.
- **Usage by queue day:** `/v1/model-usage` dates calls by when the row was queued, not when the call ran (rows
  keep no call time). Close enough for a daily view; trigger: plan usage questions that need exact times.
- **Data controls unverified:** whether ChatGPT's data controls govern these requests is not in OpenAI's pages; the
  window says to check them. Trigger: OpenAI documents it.

## Cloud model with an API key (2026-10-25)
- **What:** an `openai_compatible` config pointing at a paid cloud endpoint (OpenAI, or Gemini's
  OpenAI-compatible endpoint on its paid tier) with an API key kept like the ChatGPT tokens (DPAPI, `auth\`).
  The current client already speaks the format; it needs key storage, a key field, and handling for
  rate-limit answers (429 as `capped`).
- **Why deferred:** SPEC D18 uses the person's ChatGPT plan instead: no key, no second bill. An API key
  matters only for people without a plan.
- **Trigger:** someone the app is shared with has no ChatGPT plan, or plan usage turns out too small for
  JobForge's volume.

## Answering a "Needs your input" question in place (2026-10-24; "Yes" part RESOLVED same day)
- **Resolved (Kevin: "We should be able to answer the questions directly", meaning on "Needs your input"):** each
  question on "Needs your input", shared or one job's own, has "Answer here": an empty item form
  (choose skill, role, achievement, certification or education) shown under the posting's requirement, written in
  your own words and never prefilled with the posting's sentence; an eligibility question opens that fact's typed
  form, or offers Confirm for a fact already declared. Saved items are not confirmed; Confirm is Kevin's click.
  `desktop/src/pages/Answer.tsx`.
- **Still open:** "No" and "Not sure" answers, and concept grouping. "No" would record an absence, which changes how
  matching treats the requirement (today only a complete cert list turns an unknown into a gap), so it is an
  evaluation change and waits for Evaluation calibration 1. Same for "Show resume evidence" (no resume search exists).
- **What:** a usability pass (pasted by Kevin) asked for each question to offer Yes / No / Not sure / Show resume
  evidence, with Yes opening a prefilled profile item, and for questions grouped by concept (ChromeOS, Kubernetes,
  communication, office attendance) so one answer covers more jobs. Today the page links to Profile only, and
  questions group by their exact text (1 of 45 groups covered more than one job).
- **Why deferred:** a prefilled item would copy the employer's sentence into the profile. That is how item 55 began
  as "experience with AI tools", which echoed posting language and was matched to requirements it did not show
  (SQL, "fast learner"). A design must have Kevin write the statement in his own words, with the requirement shown
  beside it, and No / Not sure must be stored somewhere (today an unknown simply stays unknown). Concept grouping
  needs a way to cluster requirements (a keyword list or a model call) and is its own decision.
- **Trigger:** Kevin asks for it, or the page still shows many single-job questions after a profile pass.
- **Done instead (v0.3.5):** the page counts jobs, lists shared questions first, then each job with its own
  questions, fewest first, and marks jobs with 6 or more open questions as maybe not worth completing.

## Exact copies are analyzed once per copy (2026-10-24)
- **What:** the list shows exact copies as one row (ARCHITECTURE "Noise control"), but each copy that passes the
  filter still gets its own E1-E3 model run. Live: Company D's two identical "System Administrator" postings were
  both analyzed.
- **Why deferred:** reusing an analysis across postings crosses the E1/E3 reuse rules (pinned prompt versions,
  a stored E3 answer reused only for a byte-identical request), so it needs its own design and tests; copies are
  rare among passes (1 of 48 on 2026-10-24).
- **Trigger:** the analysis queue backs up, or copies are a visible share of passes; then reuse a done analysis
  when the description text is byte-identical within one employer.

## Grouping makes the big job lists slower (2026-10-24; was "the All tab")
- **What:** collapsing copies groups every open job, so the "All" view of Kevin's 59,500 open jobs takes about
  260 ms per load instead of about 68 ms (Recommended about 5 ms and Rejected about 60 ms, unchanged). Measured:
  the bare grouping sort is about 160 ms of that, and it runs on the agent's event loop.
- **Measured again 2026-10-24 (v0.3.2 work):** about 600 ms with 119,393 open jobs (the database doubled after the
  re-filter); the skip filter changes it by nothing measurable (595-601 ms with it stubbed out). "For you" about 40 ms.
- **Measured again after removing the "Everything found" tab (same session):** the slowness did not leave the
  window. "Filtered out" groups nearly the same jobs: about 720 ms for 122,574 rows (old "Everything found" 680 ms,
  "For you" about 45 ms, "Skipped by you" about 37 ms; three loads each, a copy of the real data).
- **Why deferred:** "Filtered out" is not the default view, and a grouping key column or cache would add a schema change.
- **Trigger:** the window feels slow on "Filtered out", or polls visibly stall while it is open; then store a copy-group
  key per job (computed on ingest) with an index.

## Re-filter waits for a full fetch (P1.10) — RESOLVED 2026-10-24
- **Resolved:** `pipeline.request_full_fetch` now also makes those boards due at once. The reason given below for
  deferring was wrong: `claim_due` enforces each source's hourly budget whatever is due, so pulling boards forward
  cannot exceed it (correction, audit 2026-10-24: that held only once claimed-but-unfinished polls were counted, fixed
  on branch `audit-fixes`; before it a full fetch overshot the hour about 2x); it delays other polls on that source while the backlog drains (after a profile edit, roughly
  3 h for Greenhouse and 6 h for Ashby at Kevin's 2026-10-24 board counts), once. Polling early adds no requests.
- **What:** a job whose text was dropped (rejected, or polled before any search profile existed) is filtered under a
  new profile only when its board's next full fetch brings the text back (the ETag is cleared at once). For the
  built-in list that is up to 24 h after a profile change; live: 3,530 of 5,382 jobs were still waiting 15 min after
  saving profile v2. A new user who sets the profile after the first polls sees list jobs fill in over a day.
- **Why deferred:** filtering on missing text would record wrong verdicts that stick (unique per posting, profile,
  ruleset); pulling all boards' next poll forward would exceed the per-host budgets.
- **Trigger:** Kevin finds the delay after a profile change too long; then consider re-polling boards with
  waiting jobs in budget order, or keeping title-only filtering for a first pass.

## Audit 2026-10-24: found, not fixed on branch `audit-fixes`
The deep-dive audit's findings that need a design decision, a model-call rollout, or are hardening with no live
failure. Everything else it found is fixed on the branch with a regression test (SPEC change log, 2026-10-24).
- **Recommendation behavior (gated: before/after + Kevin's approval, like the reading fix):**
  - E3 prompt escaping: the stored `jd_quote` is the model's raw string (matched to the posting only after `norm()`),
    and the job title goes in raw, so injected newlines can look like prompt structure. Citations stay real (FK) and
    years are computed, but E4 cannot tell a relevant citation. Fix: JSON-encode each requirement line. Needs a
    `MATCH_PROMPT_VERSION` bump, so every passing job re-asks the model.
  - "US citizen required; green card holders not eligible" reads as `authorized_to_work_us` (a false met).
  - Years branches: "5 with a degree or 7 without" keeps the model's 7 when the text names both.
  - `golden --suite interpretation` reports a `finish_reason=length` answer only as invalid output.
  - Version pins: the E3 hash covers one item without attributes; the attributes branch, `messages()`,
    `response_format`, `store.split` and `validate.py` changes need a manual bump. `cues.py`, `interpret.py` and
    `segment.py` have no pin at all.
  - Resume suggestions: any single resume word passes as the quote (human review in the inbox contains it).
- **Discovery:** blocked boards keep their open jobs (a 403 may lift; a host-wide WAF hour would block every board
  polled in it, permanently); a 429 without Retry-After does not pause the host; an agent stop counts as a failure
  (INTERRUPTED backs off); `source_fetches` is never pruned; add-by-name probes ignore robots, backoff and budget;
  Ashby slug case: a pasted `jobs.ashbyhq.com/Acme` beside the list's `acme` makes two employers for one board
  (case-insensitivity inferred, not confirmed); a failure inside `poller._close` itself (database busy) still leaves
  the fetch row unfinished until restart.
- **Event loop blocking:** filtering runs inside the poll's write transaction (about 1.15 ms per job, so a 2,000-job
  first fetch holds the lock for seconds, near the user connection's 5 s busy timeout); the daily backup and resume
  parsing (pypdf/docx, inside BEGIN IMMEDIATE) run on the loop thread. Trigger: tray "agent not responding" during a
  backup or upload. `zipfile.read` of a .docx has no decompressed-size cap (your own file).
- **Agent/CLI:** `--shutdown` may kill a stale `ipc.json` pid after 30 s; `winutil.dir_acl_entries` reads only the
  English icacls line (non-English Windows refuses to start); deleting a missing resume still commits an event;
  `before-restore-*.db` files are never pruned; `golden` or a backup with the agent stopped leaves `-wal`/`-shm` beside
  the live database; `/v1/jobs?view=recommended` binds one SQL variable per recommendation (limit 32,766).
- **Database hardening:** `INSERT OR REPLACE` on an append-only table deletes without firing BEFORE DELETE triggers
  (recursive_triggers off) and without an authorizer DELETE callback; no code uses REPLACE today. requirements,
  requirement_matches, recommendations, filter_results and filter_overrides are insert-only by authorizer alone.
- **Host/window:** `JOBFORGE_AGENT_EXE`/`_ARGS`/`_DATA_DIR` work in release builds (same-user only); `restore_backup`
  has no timeout; resume upload sends a JSON number array (about 4x memory); capability `core:default` is broader than
  the UI needs; if the sanitizer ever failed, `open_url` and top-level navigation would be an exfiltration path CSP
  does not cover. The Profile import retry fix has no automated test: the window has no React component test setup
  (vitest covers pure modules only). Trigger for that: the next window bug in a component.

## UX review 2026-10-24: items not in this round
A read-only review of the window code (pasted by Kevin). Kevin chose items 1, 3 and 8 for this round (keep your place
returning from a job; one clear action on the job page; distinct loading, empty and failure states) and kept "I'd skip
it" as one stated action (item 4, below). Each claim was checked against the code first.
- **2. Filters and sorting over the whole result set:** the list loads 500 rows (`Jobs.tsx` `LIMIT`) and filters and
  sorts those; the footer and the empty state now say "among the N loaded". Doing it properly means filtering and
  sorting in the agent's `/v1/jobs` query plus Load more, and "Filtered out" (about 122k rows) already takes about
  720 ms to group (see "Grouping makes the big job lists slower"). Trigger: a filter that hides a job Kevin knew existed.
- **4. Split "I'd skip it" into Hide + separate feedback:** declined by Kevin 2026-10-24 ("Keep one action"), as in
  v0.3.5; skips stay a high-confidence preference signal. Only made quieter (item 3). Revisit if a skip ever needs to
  mean "hide" without "your call".
- **5. Guided resume onboarding:** PARTLY RESOLVED by the UX redesign (v0.3.13): Setup is a one-step guide and
  Profile opens with a summary and Confirm all. Still open: Setup completes once companies, search and model are
  done, whatever the experience step says; no "profile ready for reliable matching" state apart from "search is
  running".
- **6. "Needs your input" as a finishable task:** the menu count is RESOLVED (v0.3.12). Progress after answering is
  window-only and still open; "I don't have this" / "Not sure" answers change matching and wait for Evaluation
  calibration 1. Since 2026-10-29 each job there has "I'd skip it" (hides the job, no matching change). Next, agreed
  with Kevin the same day: "I don't have this" built on the parked `absences` branch, counting the same posting line
  as a gap on any job that has it (not a typed short name), on its own branch with a before/after for approval.
- **7. Applications:** status filters, search, sort by last activity, inline status changes (the empty state now
  links to Jobs). The job page's "Your progress" (v0.3.12) changes status in place there, not in the list.
- **9. Keyboard and labels:** chips and file buttons reachable by keyboard and a focus outline everywhere are
  RESOLVED (v0.3.12). Still open: application rows are click-only `<tr>`; saves are not announced to screen readers.
- **10. Visual hierarchy and narrow windows:** type sizes and the dark-mode button contrast are RESOLVED (v0.3.12,
  `styles.css` tokens). Still open: the evidence and Applications tables have no stacked layout for narrow windows
  or zoom; job rows wrap but were only checked at 1280 px wide.

## UX redesign 2026-10-24 (v0.3.12-0.3.13): parked
Five steps from Kevin's UX review and mockup (SPEC change log, "UX redesign step 1" to "step 5"). Left out on purpose:
- **Question wording on "Needs your input":** "Does your profile cover this? "..." Add an item if it does." is
  written by the agent with the recommendation (decisive reasons), so changing it is an evaluation change: branch,
  before/after on a copy of the real data, his approval. Trigger: the next evaluation round.
- **Accuracy tools switch lives in the window** (`viewPrefs.ts`, WebView2 storage), off by default, so a fresh
  install hides "Your call" and fewer golden labels may be recorded. "I'd skip it" still records SKIP labels. Trigger:
  golden labels stop arriving, or a second person uses a shared copy and the switch needs to be per person.
- **Pasting a list into a tag field** (one per line) was not tested live: the screenshot driver cannot paste.
  Trigger: first paste that adds one long tag instead of several.
- **"Turn down all" has no undo:** each guess is rejected through the same route as "Not right", and a turned-down
  guess is not suggested again. Trigger: a request to bring guesses back.
- **Search drafts end with the window:** `draft.ts` keeps unsaved search edits in the window's memory only. Closing
  the window destroys it (the host frees WebView2 and rebuilds the window on demand), so unsaved edits are lost on
  close, as before; moving between pages keeps them. Trigger: lost edits after closing the window.
- **A commute town matches the same name anywhere** (found 2026-10-24 building the README demo): a home town
  "Dublin" (Ohio) let Asana's "IT Support Specialist" in Dublin, Ireland through the filter, because a location
  with no state or country is matched on the town name alone. The same could happen to any home town that shares a
  name with a place abroad. A filtering rule change (re-filter of every job), so it goes through the evaluation
  before/after rule. Trigger: a foreign job in "For you", or the next filtering round.
- **"A fix waiting for your approval"** on Needs your input (`Answer.tsx`, `Misread`): addressed to Kevin about an
  evaluation change; a shared user can't act on it. Reword to say what happens ("JobForge can't use this answer for
  this job yet") when the wording is next touched; left out of the README screenshots meanwhile.
