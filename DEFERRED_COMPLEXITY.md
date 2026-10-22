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
- **Why deferred:** ~100 MB at current volume; keeping everything enables re-filtering.
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
