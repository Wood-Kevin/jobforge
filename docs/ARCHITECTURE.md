# JobForge: Architecture (reasoning behind the AGREED SPEC.md)

Status: SPEC.md AGREED 2026-10-21. Nothing here is implemented yet. SPEC.md is the
contract once AGREED; this file is the reasoning behind it. If the two
disagree, SPEC.md wins and this file gets corrected.

- 2026-10-21: first draft (Docker Compose + Postgres + browser UI).
- 2026-10-21 rev 2: evaluation philosophy (four categories, three assessments,
  interpretation checks, rejected-job retention).
- 2026-10-21 rev 3: continuous incremental discovery.
- 2026-10-21 rev 4: **pivot to a Windows desktop app**: Tauri 2 shell + packaged
  Python background agent + SQLite (WAL). Docker, Postgres and the browser UI
  are removed. Section 4.3 reviews every Postgres guarantee the earlier drafts
  relied on and names its replacement. Section 6 (evaluation) is unchanged
  apart from storage details. Appendix A records the P1.0 spike results so far.
- 2026-10-21 rev 5: spike results. P1.0b model evaluation (Appendix A) and
  P1.0d packaging (Appendix B) run. Changes they forced: E2 derives
  eligibility type from code and adds an eligibility recall scan (6.4); schema
  lint, per-config reasoning control, budget-exhausted handling, load check
  (6.8); data root moved to `%LOCALAPPDATA%\<identifier>` because the
  installer uses `%LOCALAPPDATA%\<productName>` (4.1); console-subsystem agent,
  directory resource mapping, layout check (7.1); watchdog exit-code handling
  and no-respawn-while-alive, refuse elevated runs (3.2); async-only DB
  handlers and module-level FastAPI imports (3.4); hook backstop and rollback
  data-loss notice (7.2).
- 2026-10-21 rev 6: Kevin's answers (Q4, Q27, Q28 -> D16), lane and restatement
  gates waived, terms review recorded, all questions closed; SPEC.md AGREED.

---

## 0. What was inspected before writing this

Read on 2026-10-21:

| Existing component | Location | What it actually does | Reuse verdict |
|---|---|---|---|
| ATS Direct Scanner | `../job-scanner` (`ats_scanner/`, ~2,300 lines, SPEC AGREED 2026-10-13) | Python adapters for Greenhouse, Lever, Ashby, Workable, We Work Remotely, Workday. `PoliteClient`: bounded concurrency, RFC 9309 robots handling (401/403 = allowed, 5xx = disallow), retry on 429/5xx. Stable `source_job_id = ats:slug:native_id`. Rule-based filters with reason + category. Writes to Supabase `job_lean`; drops rejects before writing. | **Reuse adapters, HTTP client, robots logic, salary parser, text helpers** (they import only `models`, `errors`, `http`, `text`). Port `filters.py` as a configurable ruleset. Not reused: `db.py`, `runner.py`. Pure Python, so it packages into the agent unchanged. |
| Scanner change log | `../job-scanner/SPEC.md` section 8 | Real false positives: GSOC, SLED, "Jacksonville, NC", Veeva "Massachusetts - Boston", "Must reside in Northern California". Clearance deliberately not a rejection rule. | **Regression fixtures** for filter and eligibility interpretation. |
| Classification | Daily Claude task (not inspectable) | APPLY / STRETCH / SKIP from 1000-char summaries. | Not reused. Lessons: full original text; APPLY/STRETCH kept, REVIEW added. |
| Resume Matching Engine | `../resume-matching-engine` | Hard-coded skill points; "38/100 (100% of achievable)" alongside a /35 denominator. | **Do not reuse.** Its Ansible-vs-Python case becomes a transferable-experience fixture. |
| Local Model Bakeoff | `../local-model-bakeoff` | Ollama model benchmarks incl. `job_extraction`. | Input to model choice. LM Studio on this machine has gemma-4-12b-qat, qwen3.5-9b-tuned, gpt-oss-20b, qwen3-30b-a3b (Appendix A). |

---

## 1. Confirmed requirements vs assumptions

**Confirmed (brief):** React+TS UI, Python workers reusing the scanner, an
OpenAI-compatible inference adapter; the 14 architectural requirements;
single-user first; no auto-apply; no mandatory paid AI; original
descriptions and provenance preserved; evaluation versions, model ids and
evidence stored; works with inference down; inference and admin surfaces not
public.

**Confirmed (evaluation revision):** APPLY / STRETCH / REVIEW / SKIP;
eligibility, fit and opportunity value assessed separately; no single
score; deterministic verdicts; evidence validation; inferred vs verified
separation; hard / negotiable / direct / transferable / material gap /
uncertainty distinctions; interpretation, importance and negation tests;
rejected jobs preserved; surface plausible stretches without flooding.

**Confirmed (discovery revision):** continuous incremental polling;
per-source and per-employer intervals, default 15 min within limits;
persisted poll state; jitter; robots.txt, Retry-After, rate limits; new /
changed / unchanged detection; only new or materially changed qualifying
postings queued, history kept; discovery independent of inference; recovery
from restart, sleep, missed windows, interruptions; no overlapping polls; no
unbounded catch-up; dashboard freshness and live refresh.

**Confirmed (desktop pivot, rev 4):**
- Windows-first installable desktop app: Tauri 2 + React/TypeScript UI, a
  packaged Python background agent, SQLite in WAL mode.
- No Docker, PostgreSQL, or separate Python installation required.
- Discovery and evaluation continue with the main window closed; per-user
  background agent (not a Windows Service).
- Optional launch at sign-in; tray: open, pause discovery, status, full exit.
- Evaluations resume automatically when the inference server returns.
- SQLite transactions and durable work queues replace `SKIP LOCKED`.
- One agent per user; no overlapping employer polls.
- Secure local IPC between UI and agent.
- Database, resumes, logs, backups in per-user application-data directories.
- Installer and updates handle a running agent, migrations and rollback.
- Everything from the evaluation and discovery revisions is preserved.

**Assumptions (need confirming):**
- A1. JobForge never reads or writes Supabase `job_lean`; the old pipeline
  keeps running until Kevin retires it.
- A2. Windows 10/11 x64 only in Phase 1. No macOS/Linux builds.
- A3. LM Studio (or Ollama) runs on the same machine; the agent calls it at
  `http://127.0.0.1:<port>/v1` directly. Remote inference servers are
  configurable but untested.
- A4. Per-user install (no admin rights) into `%LOCALAPPDATA%\Programs\JobForge`.
- A5. Phase 1 sources: Greenhouse, Lever, Ashby (documented public APIs).
- A6. Resumes: Markdown, plain text, DOCX/PDF converted to text; original kept.
- A7. "Verified" = Kevin confirmed it in the UI; nothing else can verify.
- A8. Personal data never enters the repo; open-source release is later.
- A9. A false SKIP costs more than a false APPLY, up to a readable volume (Q16).
- A10. English postings only.
- A11. Discovery runs while Kevin is signed in and the PC is awake; it pauses
  during sleep and resumes on wake. Not 24/7 (Q20).
- A12. Each Phase 1 source returns an employer's whole job list in about one
  request (confirmed for the measured boards, Appendix A).
- A13. Multi-user means separate Windows accounts, each with its own agent and
  database. A shared or hosted multi-user JobForge would be a different
  deployment (server database) and is out of scope (DEFERRED_COMPLEXITY.md).

---

## 2. Repository structure

```
job-search-app/                     (rename to jobforge/ is Q1-dependent)
  SPEC.md  README.md  DEFERRED_COMPLEXITY.md  verify.sh   # verify.sh shells to PowerShell for Windows probes
  docs/ARCHITECTURE.md
  config/
    search_profile.example.yaml  watchlist.example.yaml
    verdict_rules/v1.yaml
  agent/                            # Python background agent (all business logic)
    pyproject.toml
    jobforge-agent.spec             # PyInstaller onedir build
    src/jobforge_agent/
      main.py                       # CLI: run | --shutdown | --status | --backup | --restore <file>
      lifecycle/   (paths.py, instance_lock.py, shutdown.py, power.py (sleep/resume gap detection))
      db/          (connect.py (pragmas, per-component authorizers), migrate.py,
                    migrations/0001_init.sql ..., backup.py, queue.py (claim/release/recover))
      discovery/   (sources/*.py, http.py, robots.py <- ported from ats_scanner;
                    scheduler.py, poller.py, changes.py, ratelimit.py)
      normalize/  filtering/  profile/  applications/
      inference/   (client.py: one OpenAI-compatible client; health.py)
      analysis/    (segment.py, extract.py, interpret.py, cues.py)        # E0-E2
      evaluation/  (match.py, validate.py, assess.py, recommend.py, golden.py)  # E3-E6
      ipc/         (server.py, auth.py, routes/, events.py)
      logging.py  settings.py
    tests/  unit/  integration/  lifecycle/  fixtures/ (ats/, interpretation/, verdict/, migrations/)
  desktop/                          # Tauri 2 shell: UI + tray + agent supervision, no business logic
    package.json  vite.config.ts  tsconfig.json
    src/                            # React app (pages/, components/, api/ generated types)
    src-tauri/
      Cargo.toml  tauri.conf.json  capabilities/default.json
      src/ (main.rs, tray.rs, agent.rs (attach/spawn/watchdog), ipc.rs (token, HTTP passthrough, event relay))
      nsis/hooks.nsh                # stop running agent before files are replaced
  scripts/
    build.ps1                       # pyinstaller -> copy agent dist into src-tauri resources -> tauri build
  spikes/p1_0/                      # measurement scripts and results (Appendix A)
```

The Rust layer is deliberately thin: tray, window, start/attach/restart the
agent, forward UI requests, relay events. Every rule about jobs, profiles and
evaluations lives in Python, where the scanner code and the tests already are.

---

## 3. Components, processes and data flow

### 3.1 Processes

```
                              Windows user session (Kevin)
 +--------------------------------------------------------------------------------+
 |                                                                                |
 |  JobForge.exe (Tauri host, Rust)               jobforge-agent.exe (Python)     |
 |  +------------------------------+   HTTP on    +-----------------------------+ |
 |  | tray icon + menu             |   127.0.0.1  | IPC server (token required) | |
 |  | window (WebView2, React UI)  |<-----------> | scheduler + pollers         |----> public ATS APIs
 |  |   invoke("agent", ...)       |  bearer      | analysis/evaluation worker  |----> LM Studio 127.0.0.1:1234
 |  | agent supervisor (attach /   |  token       | event broadcaster           | |
 |  |   spawn / watchdog)          |              | SQLite owner (only process  | |
 |  +------------------------------+              |   that opens the DB)        | |
 |                                                +--------------+--------------+ |
 |                                                               |                |
 |   %LOCALAPPDATA%\JobForge\                                    v                |
 |     data\jobforge.db (+ -wal, -shm)   logs\   backups\   run\agent.lock, run\ipc.json
 +--------------------------------------------------------------------------------+
```

- **The agent is the only process that opens the database.** The UI never
  touches SQLite; it asks the agent. This removes cross-process SQLite
  locking entirely and gives one place to enforce rules (4.3).
- **The window is disposable.** Closing it destroys the webview (freeing
  WebView2 memory); the host keeps running in the tray, and the agent keeps
  running regardless of the host.
- **The agent does not depend on the host staying alive.** It is started
  detached, holds its own single-instance lock, and keeps polling if the host
  crashes. The next host start attaches to it instead of starting another.

### 3.2 Process lifecycle

| Event | Host (JobForge.exe) | Agent (jobforge-agent.exe) |
|---|---|---|
| User opens JobForge | Single-instance plugin: if a host already runs, focus its window and exit. Else read `run\ipc.json`, `GET /v1/health` with token. If no agent answers, spawn it detached (`CREATE_NO_WINDOW`, `DETACHED_PROCESS`, own process group) and wait up to 15 s for health. Show window. | On start: take `run\agent.lock` (exclusive OS lock) or exit with code 3 "already running". Migrate DB (4.4), recover interrupted work (4.3), start IPC server on an ephemeral 127.0.0.1 port, write `ipc.json` (port, fresh 256-bit token, pid, protocol version) atomically. |
| Sign-in with autostart on | Launched by the HKCU `Run` entry with `--background`: no window, tray only, then the same attach-or-spawn. | Same as above. |
| Window closed | Window destroyed; tray stays. First time only, a toast: "JobForge keeps running in the tray." | Unaffected. |
| Tray: Pause discovery | `POST /v1/discovery/pause`. Tray label flips to Resume. | Persists `settings.discovery_paused = 1`; scheduler claims nothing. Survives restarts. Evaluation keeps draining. |
| Tray: Status | Submenu text from `/v1/status`: freshness, queue depth, inference, errors. | — |
| Tray: Exit JobForge | `POST /v1/shutdown`, wait up to 20 s for the agent's lock to release, then exit. Kill the agent only if it does not stop. | Stops claiming, lets in-flight polls finish or marks them INTERRUPTED, writes no half transactions (each commit is atomic), releases lock, exits 0. |
| Host crashes | — | Unaffected; keeps working. Tray gone until JobForge is reopened. |
| Agent crashes or is killed | Watchdog (health every 5 s) sees 2 failures; respawns with backoff; after 3 restarts in 5 minutes, tray turns red and shows the last log lines instead of looping. **Reads the child's exit code:** 3 (already running) -> attach, do not respawn; 4 (database newer than this version) -> stop respawning and show the restore screen (spike: the watchdog respawned a refusing agent); 5 (refused elevated) -> stop respawning and show the D16 message. | On restart: lock is free (OS released it), recovery resets interrupted work. Spike: respawn in 13.6 s, previous run recorded `crash_recovered`, persisted pause survived. |
| Agent hangs (alive, not answering) | **Never spawns a second agent while its last child is still alive** (spike: a hung agent plus respawn produced 8 hung agents). Tray shows "agent not responding" and offers Restart (kill + spawn). | An internal watchdog task logs `event_loop_stalled` if the loop misses ticks for > 30 s. |
| Launched elevated (Run as administrator) | **D16 (Kevin, Q28):** compare the token's user SID with the interactive shell user's (Explorer's process owner). Same user -> relaunch itself at medium integrity through the shell (Explorer's token), log `relaunched_unelevated`, exit; only when Explorer's own token is known to be unelevated (audit 2026-10-24: with UAC off Explorer is elevated, so every relaunch started elevated and relaunched again, forever). Different user, Explorer elevated or unknown, or relaunch fails -> message "JobForge must run as you, without administrator rights", exit, start nothing. | Run mode, `--backup` or `--restore` while elevated -> exit 5, log `refused_elevated` (an elevated agent would create Administrators-owned files in the user's data root). `--shutdown` and `--status` are allowed elevated, because installer hooks need them. Logs `elevated` on every start. Spike: the autostart plugin wrote machine-wide `HKLM\...\Run` when elevated, so JobForge writes `HKCU\...\Run` itself (7.2). |
| Sleep / resume | Nothing special. | Scheduler sees a monotonic-clock tick gap > 2 min and re-spreads overdue employers (3.3). HTTP calls that were in flight fail and are retried by normal backoff. |
| Sign-out / shutdown | Exits on session end. | **Implemented P1.2:** a hidden top-level window (`sessionend.py`) answers `WM_QUERYENDSESSION` and, on `WM_ENDSESSION`, requests a graceful stop and waits up to 4 s for the run row (`stop_reason = session_end_logoff` / `session_end_shutdown`). Console control events are not used: a console process that loads user32 does not receive logoff/shutdown events. Anything unfinished is recovered on next start. |
| Update / uninstall | See 7.2. | `--shutdown` from the installer hook. |

### 3.3 Continuous incremental discovery

Unchanged in behavior from rev 3; adapted to SQLite and the agent.

**Implemented in P1.3** (`agent/src/jobforge_agent/discovery/`). Points where the code adds to
or departs from the text below:
- Parsers, RFC 9309 robots semantics and the collision guard are ported from
  `ats_scanner` at `edd1528`. The scanner's in-request retries are **not** ported: a failed
  request ends the poll with an outcome, and the scheduler's backoff reschedules it, so
  every attempt is a `source_fetches` row and request volume is measurable.
- robots.txt is cached per host in `robots_cache` for 24 h; fetching it counts toward the
  host's budget. A SKIPPED employer is next polled when that cache entry expires. An unreadable
  robots.txt (network error, 5xx) still means "disallowed" but is cached for 15 minutes only
  (audit 2026-10-24: one failed fetch after a wake from sleep parked every board of the host for a day).
- The client follows redirects only on the same host over https (a redirect elsewhere, including
  127.0.0.1, is refused and recorded as a network failure), caps a decoded body at 128 MB, and limits
  a whole request to 300 s (per-step timeouts never fire on a server that trickles bytes). Any
  unexpected exception in a poll still finishes it as `ERROR` with an `internal:` reason, logged at
  error level: an unfinished `source_fetches` row blocks its employer until the next start.
- Slugs from links, names or a watchlist must be letters, digits, `.`, `_`, `-` (every built-in list
  slug fits); anything else is refused at import, since the slug is a path segment of a fixed API URL.
- `HostLimiter` serializes and spaces requests per host across all polls (spacing = max of
  `request_spacing_ms` and robots `Crawl-delay`, plus 0-1 s jitter).
- Budgets seeded in migration 0002: Greenhouse 300/h, Lever 120/h, Ashby 120/h. Greenhouse
  was raised from the review's 120/h because the imported watchlist has 58 Greenhouse
  employers (232 polls/h at 15 min); 120/h would have stretched them to ~30 min silently.
- `jobs.employer_id` (migration 0002) scopes missing-posting detection to the employer.
- A posting that reverts to an earlier version (A -> B -> A) points back at the existing
  raw row instead of violating `UNIQUE (source_job_id, material_sha256)`.
- Material hash ignores tracking query parameters (`utm_*`, `gh_src`, `source`, `ref`, ...).
- Employers are added by `jobforge-agent --import-watchlist FILE` (scanner YAML format),
  which posts to `POST /v1/admin/employers/import` on the running agent: the agent remains
  the only process that writes the database. Unsupported sources (Workday, Workable, WWR)
  are reported, not silently dropped. First polls of imported employers are spread over one
  interval.
- `GET /v1/discovery/status` serves the dashboard data; `POST /v1/admin/employers/{id}/poll-now`
  makes one employer due (still subject to lease, backoff and budget).
- Structured salary only (Lever `salaryRange`, Ashby compensation); the scanner's
  salary-from-text parser moves with filtering in P1.4.
- Evidence script: `scripts/verify_discovery.py` (becomes the discovery section of verify.sh).

**Model.** Every enabled employer has an `employer_poll_state` row with
`next_poll_at_ms`. A scheduler task wakes every 5 s, claims due employers,
hands each to a poll task, and reschedules it on completion.

**Intervals.** `clamp(employer.poll_interval_s ?? source.default_interval_s ?? 900,
min = source.min_interval_s, max = 24 h)`. `min_interval_s` comes from the
recorded terms review and cannot be undercut by an override. Appendix A adds
one more floor from evidence: Ashby's CDN caches boards for 60 s, so polling
faster than that cannot see anything new.

**Atomic claim (replaces `SKIP LOCKED`).** Inside `BEGIN IMMEDIATE` (takes
SQLite's write lock up front, so no other writer can interleave):

```sql
UPDATE employer_poll_state
SET lease_until_ms = :now + 300000, last_attempt_at_ms = :now
WHERE employer_id IN (
  SELECT s.employer_id FROM employer_poll_state s
  JOIN employers e ON e.id = s.employer_id
  JOIN sources src ON src.id = e.source_id
  WHERE e.enabled = 1 AND src.enabled = 1 AND e.status = 'active'
    AND (SELECT value FROM settings WHERE key = 'discovery_paused') = '0'
    AND s.next_poll_at_ms <= :now
    AND (s.lease_until_ms IS NULL OR s.lease_until_ms < :now)
    AND (src.backoff_until_ms IS NULL OR src.backoff_until_ms < :now)
    AND s.employer_id NOT IN (SELECT employer_id FROM source_fetches WHERE finished_at_ms IS NULL)
  ORDER BY s.next_poll_at_ms
  LIMIT :claim_batch)
RETURNING employer_id;
```

Then, in the same transaction, one `source_fetches` row per claimed employer
is inserted with `finished_at_ms` NULL. **No overlapping polls** rests on
three layers: one agent per user (OS lock, 4.3), the lease, and a partial
unique index `UNIQUE (employer_id) WHERE finished_at_ms IS NULL` on
`source_fetches`, which makes a second in-flight poll for an employer a
constraint violation no matter what the scheduler does.

**Time.** SQLite has no server clock. The agent passes `now` (UTC epoch ms)
into every query. Gaps are detected with the monotonic clock, so a wall-clock
jump after sleep cannot hide or invent a gap.

**Reschedule.** `next_poll_at_ms = now + interval * U(0.9, 1.1)`, computed from
completion, never from the previous slot, so missed windows collapse into one
poll. New employers start at `now + U(0, interval)`.

**Per-host budget.** Ported `PoliteClient` (RFC 9309, robots cached up to
24 h) plus per-host concurrency (default 1), minimum spacing (default 2 s +
0-1 s jitter, and never below a robots `Crawl-delay`; Lever's is 1 s), and a
per-host hourly request budget from `source_fetches`. Over budget: the source
is skipped this tick and `BUDGET_DEFERRED` is logged. A claimed poll that has not finished counts as
one request, and one claim batch never claims past a source's remaining budget (audit 2026-10-24:
claims queued behind the host limiter counted as zero, so a full fetch overshot the hour about 2x).

**Conditional requests (from Appendix A).** All three Phase 1 sources return
an `ETag` and answer `If-None-Match` with `304 Not Modified` and an empty
body. Every poll sends the stored ETag; a 304 is recorded as `NOT_MODIFIED`
and costs headers only. This is what makes a 15-minute interval cheap: an
unchanged board costs ~0 bytes of body instead of 0.6-14 MB.

**Backoff and access restrictions.**

| Response | Scope | Action |
|---|---|---|
| 429 / 503 with `Retry-After` | host (`sources.backoff_until_ms`) | Pause the host until `max(Retry-After, 60 s)`; reason recorded |
| 429 / 503 without it, other 5xx, timeout, network error | employer | `consecutive_failures += 1`; next poll `now + min(interval * 2^failures, 6 h)` with jitter |
| 404 on the board | employer | After 3 consecutive: `status = 'dead'`, polling stops, its open jobs close (`job_closed`), shown on the dashboard |
| 401 / 403 on the board | employer | `status = 'blocked'`, polling stops (access restriction, not retried). Its jobs stay open: the block may lift |
| Naming a dead or blocked company again | employer | `status = 'active'`, next poll within one interval |
| robots.txt disallows | employer | `SKIPPED`; recheck when the robots cache expires (15 min when robots.txt was unreadable) |
| Collision (one listing, two postings under one id) | employer | `ERROR`; the stored ETag/Last-Modified are cleared so the next poll is a full 200, not a 304 that hides the error |
| Unexpected exception in the poll | employer | `ERROR`, `internal: ...` in Status and the error log; backoff as a failure |
| 200 or 304 | employer | `consecutive_failures = 0`, `last_success_at_ms = now` |

**Bounded catch-up.** On agent start and after a detected gap, every employer
overdue by more than one interval gets `next_poll_at_ms = now + U(0, interval)`.
With the claim batch and host budget, the first interval after an outage
costs about one normal interval of requests.

**Interrupted polls.** Because the instance lock guarantees no other agent is
running, any `source_fetches` row with `finished_at_ms` NULL at startup is an
orphan: it is closed as `INTERRUPTED`, its lease cleared, the employer left due.
Each employer's poll results commit in one transaction, so an interrupted
poll leaves all of its changes or none.

**Change detection.**

| Case | Test | Write | Queue |
|---|---|---|---|
| Board unchanged | 304 | `source_fetches` NOT_MODIFIED; `jobs.last_seen_at_ms` for that employer's open jobs the last listing contained; a job already missing from it counts one more missing listing (and closes at 2) | Nothing |
| New posting | `source_job_id` not in `jobs` | `raw_postings`, `jobs`, `change_events(posting_new)` | Filter; analysis if it passes |
| Materially changed | `material_sha256` differs | New `raw_postings` row; `jobs.current_raw_posting_id` moves; `change_events(posting_changed)` | Re-filter; analysis if it passes; old analyses/evaluations kept as history |
| Changed, not materially | only `payload_sha256` differs | `jobs.last_payload_sha256`, `last_seen_at_ms` | Nothing |
| Unchanged | same hashes | `last_seen_at_ms` | Nothing |
| Missing from a complete 200 listing | absent | `missing_polls += 1`; `closed_at_ms` after 2 consecutive successful complete listings without it | Nothing. Failed and partial polls never close jobs. A 304 repeats the last complete listing (validators are stored only for complete 200s), so it counts as one (audit 2026-10-24: a removed job stayed open while the board kept answering 304). Reappearance clears `closed_at_ms` |

`material_sha256` covers normalized title, company, location, work mode,
salary, apply URL without tracking parameters, and whitespace/entity-normalized
description text. Appendix A: repeated responses within seconds were
byte-identical on all six boards, so no volatile fields were seen yet; the
two-hash design stays because that observation covers seconds, not days.

**Supersession.** A new version of a posting marks any still-pending analysis
of an older version `superseded`: at most one pending analysis per job, so a
long inference outage cannot grow the queue beyond the number of qualifying jobs.

**Filter stage (implemented in P1.4,** `agent/src/jobforge_agent/filtering/`**).**
- Rules are `ats_scanner/filters.py` + `salary.py` at `edd1528`, ported rule for rule; the
  scanner's own regression suite runs against the port with a real profile
  (`agent/tests/test_filter_rules.py`, skipped with a printed reason when the scanner's
  `config.yaml` is absent, since the profile is personal data and not in the repo).
- What the scanner hard-coded becomes profile data: `jax_metro` -> `location.home_metro`,
  Florida/Georgia -> `home_state_codes` / `home_state_names`, `florida_*_markers` ->
  `home_*_markers`. Reason names follow: `REMOTE_EXCLUDES_FL` -> `REMOTE_EXCLUDES_HOME`,
  `FL_OK` -> `HOME_OK`. Work modes are lower case.
- The search form (`filtering/simple.py`) writes two kinds of state. "State you live in" sets
  `home_markers` and `home_excluded_markers` ("Remote - Florida" is home, "cannot hire in
  Florida" excludes) and is the first of `home_state_codes`. "Other states your towns are in"
  only joins `home_state_codes` / `home_state_names`, which `_names_other_state` uses so a home
  town across a state line ("Valdosta, GA") is not read as another state. One list of home
  states was rejected: it would make "Remote - Georgia" count as home for a Florida resident.
- One evaluation returns the deciding reason (title, then location, then staleness, then
  salary, as the scanner's pass1/pass2 order) **and every other rule the posting fails**, so
  the rejected view can show "also below the salary floor" without re-running anything.
- The scanner's `CLOSED` rule is not ported: closure is `jobs.closed_at_ms` (3.3).
- Clearance wording is recorded (`clearance_mention`) and never rejects.
- `RULESET_VERSION` (`rules-1`) names the code's behavior; a rule change bumps it, so old
  verdicts stay distinguishable. Results are keyed (raw posting, profile version, ruleset).
- **In the poll transaction:** new, materially changed and reopened jobs are filtered inside
  the same transaction that stores them, so once a profile exists a posting is never stored
  without a verdict. With no profile yet, postings are stored unfiltered and the first
  profile save filters them.
- **Queueing:** a pass (or an override) on an open job queues one pending analysis of its
  current posting with the default model (`settings.default_model_config_id`, migration
  0003), superseding an older pending one. An analysis already present for the same posting
  version and analysis versions is not queued again (D10). Closed jobs get a verdict for the
  record and are never queued. No default model -> counted as `skipped_no_model`, not an error.
- **Profile save** (`POST /v1/search-profile`, CLI `--import-search-profile FILE`, which also
  converts a scanner `config.yaml`) validates by compiling (bad regex, missing `(?P<place>)`
  group, wrong types -> 422 naming the field, nothing saved), inserts the next version and
  starts a re-filter.
- **Re-filter** (`POST /v1/admin/refilter`, and after every profile save) walks **every**
  job's current posting, open or closed, on the discovery connection in transactions of 300
  jobs with a yield between them, so polls and user actions interleave (P1.4 live: 13,204
  postings in 13.4 s, about 0.3 s per transaction). One run at a time: requests that arrive
  before a run reads its job list share it; a request after that gets exactly one follow-up
  run. It ends with a `refilter_done` change event carrying counts; a failure logs
  `refilter_failed` (error level) and emits the same-named event.
- **Evaluate anyway** (`POST /v1/jobs/{id}/evaluate-anyway`, optional `note`) records a
  `filter_overrides` row and queues analysis of the current posting on the user-action
  connection. The reject verdict stays as it was; later re-filters keep queueing the job
  while the override exists.
- `GET /v1/filter/summary`: verdicts and rejection categories for the active profile.
  Evidence script: `scripts/verify_filter.py` (with `--scanner DIR`, a differential run of the
  scanner's own `pass2` on the same stored postings).

#### Company directory (P1.10, SPEC D17)

The three boards answer only per company, so discovery needs a list of companies. Instead of
asking the person for board links, the agent ships `discovery/directory.json`:
- **Built by** `scripts/build_directory.py` (maintainer tool, run from `agent/`): Common Crawl's
  URL index for `boards.greenhouse.io`, `job-boards.greenhouse.io`, `jobs.lever.co` and
  `jobs.ashbyhq.com` over the latest crawls gives candidate board ids (first path segment); each
  candidate is fetched once from its board's list endpoint under the agent's User-Agent, one
  request per host every 1.2 s, and kept only with at least one open job. Greenhouse names come
  from the job list's `company_name`; Lever and Ashby expose no company name, so the id is title-cased.
  Index queries that fail after six tries are listed in the file (`index_queries_failed`), never
  dropped silently. Rejected: YC's company list (start-ups only), any list that guesses ids.
- **Startup sync** (`directory.sync`, discovery connection; the authorizer allows it to insert
  employers, not to update `enabled` or `origin`): rows missing from `employers` are added with
  `origin = 'directory'`, `poll_interval_s = 86400` and a first poll spread uniformly over a day;
  existing rows are never changed, so a company the person stopped watching stays stopped. The
  `directory_synced` log line carries `added` and `listed`. A broken file is logged
  (`directory_sync_failed`) and shown on Status; the person's own companies keep working.
  `JOBFORGE_BUILT_IN_LIST=0` skips it (tests and drills, so they never poll real boards).
- **Why 24 h:** the per-host budgets (300/h Greenhouse, 120/h Lever and Ashby, 0002) are shared
  with the person's companies at 15 min; 24 h is also `MAX_INTERVAL_S`. A host at its hourly
  budget is skipped (`budget_deferred`) until the count drops, then the longest-overdue polls go
  first, so everything stretches evenly rather than failing.
- **The switch** (`POST /v1/employers/built-in {watch}`): stores `settings.directory_watch` and
  sets `enabled` on every `origin = 'directory'` row; rows added later follow it.
- **Add by name** (`POST /v1/employers/add`): each line is a board link, `greenhouse:acme`, or a
  name. Names (at most 10 per request) are matched in the list (case, punctuation and legal
  suffixes ignored), else probed on each enabled board under two spellings (`redhat`,
  `red-hat`) through the shared `HttpClient`, so the per-host spacing holds. 404 means "not on
  this board"; any other answer or a network error is returned as `check_failed`, never as
  `not_found`. A named company that already exists becomes `origin = 'user'` and is watched
  again (`watched_again`); a list row named this way drops the list's 24 h interval for the source
  default, and its next poll comes no later than a new company's would.
- **Status** lists only `origin = 'user'` rows and summarizes the list (`built_in`: total,
  enabled, dead, blocked, failing, checked once), so the window's 15 s refresh stays small.
  Dead or failing list boards are counted under Details, not raised as "Needs attention".

### 3.4 Inside the agent: tasks and database access

One process, one asyncio event loop, these tasks:

| Task | Does | Never does |
|---|---|---|
| scheduler | claim, recover, re-spread, reschedule | call inference |
| pollers (per-host semaphores) | HTTP fetch, change detection, filter, enqueue | call inference |
| inference health probe | `GET /v1/models` every 30 s (60 s -> 15 min backoff while down) | — |
| analysis/evaluation worker | claim one item, call the model, persist results | run while health says down; hold a DB transaction during a model call |
| recompute | rule/profile version changes -> new recommendations, no model calls (P1.7: the worker loop's `sync` step, run every iteration whether or not inference is up, and `POST /v1/recompute`) | call inference |
| IPC server | serve the UI | write outside the user-action connection's allow-list |
| event broadcaster | after each commit, read new `change_events` rows and push them to subscribers | send uncommitted data |
| backup | daily online backup + pre-migration backup | — |

**Evaluation resumes by itself.** When the health probe flips back to up, it
wakes the worker, which drains `pending` items oldest first. Nothing needs a
restart.

**Database access rules.**
- One **writer** connection per component (scheduler/pollers, evaluation,
  user actions). Writes are serialized by the event loop itself, not by a lock: every
  connection is used only on the loop thread and no transaction contains an `await`, so
  one transaction always finishes before another starts (corrected in P1.4; the earlier
  text named an asyncio write lock that the code never needed). Transactions use
  `BEGIN IMMEDIATE` and are short (milliseconds; a re-filter chunk is the longest, ~0.3 s). Several read-only
  connections (`mode=ro`) for queries; WAL lets them read during a write.
- **No transaction spans a network call or model call.** The worker claims in
  one transaction, releases, calls the model, then persists in a second
  transaction. Pinned by a test (4.3) that holds a fake model call open for 30 s
  and checks a user action commits within 1 s meanwhile.
- **Close order.** On stop, the read-only connection closes first and a writer last: only
  a read-write connection checkpoints and removes the WAL on close (P1.2 found committed rows
  left in `-wal` when the read-only connection closed last). Tested.
- **Thread affinity.** FastAPI runs plain `def` handlers in a worker thread
  pool; a connection created on the event-loop thread then raises
  `sqlite3.ProgrammingError` (spike: every `/v1/health` returned 500 while the
  auth middleware still worked). Rule: IPC handlers that touch the database are
  `async def` and use the loop-owned connections. Pinned by a test that calls
  every route through the real app.
- **Postponed annotations.** With `from __future__ import annotations`, FastAPI
  resolves parameter types from module globals. A `Request` imported inside a
  factory function was treated as a query parameter, so `/v1/shutdown` returned
  422 and graceful shutdown silently degraded to a kill after 30 s (spike).
  Rule: FastAPI types are imported at module level; the shutdown route has a
  test.
- Pragmas on every connection: `journal_mode=WAL`, `synchronous=FULL`
  (survives power loss with committed data intact; write volume is small),
  `foreign_keys=ON` (SQLite defaults it OFF, a classic silent failure),
  `busy_timeout=5000`. A startup assertion reads them back.

---

## 4. Persistence model

### 4.1 Locations (per user, never the install directory)

| What | Where | Notes |
|---|---|---|
| Program files | `%LOCALAPPDATA%\JobForge\` | **Corrected by spike P1.0d:** Tauri's per-user NSIS mode installs to `%LOCALAPPDATA%\<productName>`, not `Programs\`. Written only by the installer; the app never writes here (tested: no file changed after install). |
| Data root | `%LOCALAPPDATA%\<identifier>\` (e.g. `%LOCALAPPDATA%\dev.kevin.jobforge\`) | **Must differ from the install directory.** With the original plan (`%LOCALAPPDATA%\JobForge\data`) the data would have lived inside the program folder. The identifier-named folder is also Tauri's app-local-data convention, which its uninstaller deletes only when the user ticks "delete app data". |
| Database | `<data root>\data\jobforge.db` (+ `-wal`, `-shm`) | Local, not Roaming: a live SQLite file must not be synced by roaming profiles or OneDrive. |
| Resumes | Inside the database (BLOB) | One file to back up and restore. |
| Logs | `<data root>\logs\agent.jsonl`, `host.log`, `agent-console.log` | Rotated 10 MB x 5. Redacted (8). `agent-console.log` captures the agent's stdout/stderr, where bootloader failures land (7.1). |
| Backups | `<data root>\backups\` | Daily, keep 7; pre-migration, keep 5. User-visible "Open backups folder" in settings. |
| Runtime | `<data root>\run\agent.lock`, `ipc.json` | `ipc.json` holds port + token; recreated each agent start. `run\` DACL set to the user's SID only (tested: `icacls` showed only `KEVINSPC\kevin`). |

Paths come from `SHGetKnownFolderPath(FOLDERID_LocalAppData)`, not from
environment strings, and are overridable with `JOBFORGE_DATA_DIR` for tests.

### 4.2 Schema (SQLite, STRICT tables)

Implemented as `agent/src/jobforge_agent/db/migrations/0001_init.sql` (P1.1), which is the
column-level source of truth; the tables below summarize it. P1.1 added columns the design
implied: `requirements.origin` (`model` / `unextracted_eligibility`, 6.4 check 7) and
`requirements.model_eligibility_type` beside the code-derived `eligibility_type` (check 6);
`model_configs.tested_at_ms` with `CHECK (enabled = 0 OR tested_at_ms IS NOT NULL)` (6.8 load
test); `sources` `CHECK (enabled = 0 OR terms_reviewed_on IS NOT NULL)` (8); `agent_runs.elevated`;
`created_at_ms` on `posting_analyses`, `filter_results`, `evaluations`, `inferred_claims`.
Migration 0003 (P1.4) seeds the default model config (Gemma 4 12B on LM Studio, reasoning
off, `enabled = 0` until its load test runs) and `settings.default_model_config_id`.
Migration 0004 (P1.7) rebuilds `evaluations` with `job_id` and a `superseded` status plus a
partial unique index (one pending evaluation per job, like analyses; startup recovery
supersedes a running one that has a newer pending sibling), and adds `years`, `start_date`,
`end_date` to `profile_snapshot_items` (insert-only triggers unaffected, checked after migrating).
Migration 0005 (P1.7 follow-up) adds `evaluations.request_sha256` and a partial index on done rows (reuse, 6.4).
Migration 0006 (P1.8) makes `applications.job_id` unique (one application per job); every status change appends an
`application_events` row (append-only) in the same user-action transaction.
Migration 0008 (P1.9, v0.2.0) adds `golden_labels` (append-only, triggers `golden_labels_no_update/_no_delete`): job,
labeled posting version, state (active/withdrawn), recommendation, optional eligibility, `obvious`, misread requirement ids,
note, `labeled_by`, `reviewed_by`, `shown_recommendation_id` (what JobForge showed at the time). The latest row per job is
the label. The append-only trigger check (`migrate.check_append_only_triggers`) now checks a table only once the database
has reached the migration that creates it (`TABLE_SINCE_VERSION`, pinned by a test against the migration files).
Migration 0009 (P1.10) adds `employers.origin` (`user` | `directory`, default `user`, CHECK): directory rows come from the
built-in company list (3.3 "Company directory") and follow its switch; existing rows are the person's.
Migration 0010 (P1.10, SPEC D9 amendment) moves posting text to `posting_texts` (zlib, `db/textpack.py`), kept only
while wanted. New `raw_postings` rows carry no description and `payload` JSON `null` (never read; `payload_sha256`
still drives change detection). The poll stores the text, the filter (same transaction) drops it on a reject without
an override, and supersedes that job's pending analysis; with no search profile everything is dropped. A job whose
text is gone is never re-filtered (the rules read the description; a verdict on missing text would stick): the
re-filter clears the ETag of its board and makes it due now (`pipeline.request_full_fetch`; `claim_due` still drains
due boards within each source's hourly budget and concurrency, and polling a board early adds no requests, it only
moves that board's daily slot), and the next full fetch stores the text again
when `pipeline.WANTS_TEXT` holds (profile exists and no reject verdict under it, or an override) and filters it.
"Evaluate anyway" on such a job does the same for its board at once. E5/E6 and the analysis claim never run on a
job without text (`evaluation/store.py` `_TEXT_KEPT`; a pending analysis without text is marked failed, not run).
Rows from before 0010 keep plain text and are never rewritten. Every reader uses
`coalesce(rp.description_text, unpack_text(pt.description_text_z))` over `LEFT JOIN posting_texts`; `open_connection`
registers `unpack_text`, and scripts that open the file directly register it themselves (`verify_filter.py`).
Why: at built-in-list scale the text cost 30 KB per posting plain, 11.5 KB compressed (DEFERRED_COMPLEXITY.md).
Migration 0007 (P1.8c) adds `inferred_claims.proposed_by_resume_id` (no foreign key: deleting a resume must neither fail
nor take accepted suggestions with it) and `inferred_claims.proposed_fields` (JSON: dates or `dates_as_written`, applied on
accept).
`filter_results.reasons` shape: `{primary, failed: [{rule, category, detail}], work_mode,
remote_eligibility, salary: {min, max, period, annual_max, text} | null, clearance_mention}`;
categories `title_family`, `seniority`, `location`, `compensation`, `other`.

Conventions: `INTEGER PRIMARY KEY`; times as UTC epoch milliseconds
(`*_ms` INTEGER); JSON as TEXT with `CHECK (json_valid(col))`; enums as TEXT
with `CHECK (col IN (...))`; every table `STRICT`.

`owner_id` columns are **dropped** (rev 4): each Windows user has their own
database file, so ownership is the file. The global-facts vs personal-data
split below is kept as documentation for any future server port (A13).

**Global facts (no personal data):**

| Table | Key columns | Notes |
|---|---|---|
| `sources` | id, kind, host, enabled, terms_reviewed_on, terms_note, default_interval_s (900), min_interval_s, max_requests_per_hour, max_concurrency (1), request_spacing_ms (2000), crawl_delay_ms, backoff_until_ms, backoff_reason | Disabled until a terms review is recorded. |
| `employers` | id, source_id FK, slug, display_name, enabled, status (active/dead/blocked), poll_interval_s | Unique (source_id, slug). |
| `employer_poll_state` | employer_id PK FK, next_poll_at_ms, last_attempt_at_ms, last_success_at_ms, last_outcome, last_error, consecutive_failures, lease_until_ms, etag, last_modified | Index on next_poll_at_ms. |
| `robots_cache` | host PK, fetched_at_ms, expires_at_ms, http_status, body | |
| `source_fetches` | id, employer_id FK, started_at_ms, finished_at_ms, outcome (OK/EMPTY/NOT_MODIFIED/ERROR/SKIPPED/INTERRUPTED/BUDGET_DEFERRED), http_status, retry_after_s, requests_made, bytes_received, duration_ms, postings_seen, new_count, changed_count, closed_count, complete, error | **Partial unique index** `(employer_id) WHERE finished_at_ms IS NULL`. |
| `raw_postings` | id, employer_id FK, source_job_id, fetched_at_ms, first_fetch_id FK, listing_url, apply_url, payload (JSON; `null` since 0010), description_raw, description_text (NULL since 0010), payload_sha256, material_sha256, adapter_version | **Append-only by trigger.** Unique (source_job_id, material_sha256). |
| `posting_texts` (0010) | raw_posting_id PK/FK, description_raw_z, description_text_z (zlib BLOB) | Kept only while wanted (pass or override); discovery inserts and deletes. |
| `jobs` | id, source_job_id UNIQUE, employer_id FK (0002), current_raw_posting_id FK, last_payload_sha256, title, company, location_text, work_mode, salary_min, salary_max, salary_period, posted_at, first_seen_at_ms, last_seen_at_ms, missing_polls, closed_at_ms | Current view. |
| `posting_analyses` | id, raw_posting_id FK, model_config_id FK, model_id_reported, extraction_prompt_version, segmenter_version, interpreter_version, sections (JSON), status (pending/running/done/failed/superseded), attempts, lease_until_ms, last_error, raw_response (JSON) | Unique on the version tuple. Partial unique index `(job_id) WHERE status = 'pending'` (job_id denormalized) enforces supersession. |
| `requirements` | id, posting_analysis_id FK, text, jd_quote, quote_found, quote_occurrences, section_label, sentence, model_importance, model_polarity, model_subject, eligibility_type, min_years, cue_importance, cue_polarity, cue_subject, final_class, interpretation_status, flags (JSON) | |
| `change_events` | id, type, job_id, employer_id, ref (JSON), created_at_ms | Same transaction as the change; pruned after 7 days (trigger allows only that delete). |

**Personal data:**

| Table | Key columns | Notes |
|---|---|---|
| `settings` | key PK, value | `discovery_paused`, `autostart`, active model config, etc. |
| `search_profiles` | id, version, rules (JSON), created_at_ms | **Insert-only by trigger.** |
| `filter_results` | id, job_id, raw_posting_id, search_profile_id, ruleset_version, verdict, reasons (JSON) | Unique (raw_posting_id, search_profile_id, ruleset_version). |
| `filter_overrides` | id, job_id, created_at_ms, note | "Evaluate anyway". |
| `resumes` | id, label, original_filename, original_bytes BLOB, mime, extracted_text, sha256, uploaded_at_ms | |
| `experience_items` | id, kind, statement, attributes (JSON), start_date, end_date, years, status (verified/self_reported), source, verified_at_ms | `CHECK (status <> 'verified' OR verified_at_ms IS NOT NULL)`; writable only by the user-action connection (4.3). |
| `profile_completeness` | id = 1, cert_list_complete, eligibility_facts_complete, updated_at_ms | Same write rule. |
| `inferred_claims` | id, statement, kind, rationale, evidence (JSON), proposed_by_evaluation_id, proposed_by_resume_id, proposed_fields (JSON), state, accepted_as_item_id | Evaluation inserts E3 guesses; the user connection inserts resume suggestions (a user command, P1.8c); only user actions change `state`. |
| `profile_snapshots` | id, sha256, created_at_ms | **Insert-only by trigger.** |
| `profile_snapshot_items` | snapshot_id FK, item_id, kind, statement, status, attributes (JSON) | PK (snapshot_id, item_id). **Insert-only.** New in rev 4: lets the DB check citations (below). |
| `model_configs` | id, name, provider_label, base_url, model_id, params (JSON), structured_output_mode, enabled | |
| `evaluations` | id, posting_analysis_id FK, profile_snapshot_id FK, model_config_id FK, model_id_reported, match_prompt_version, status, attempts, lease_until_ms, last_error, raw_response (JSON), requested_by | Unique on the version tuple. |
| `requirement_matches` | id, evaluation_id FK, requirement_id FK, matched_by, match_type, basis, bridge, transfer_limits, gap_size (JSON), gap_materiality, validation_flags (JSON) | |
| `match_citations` | requirement_match_id FK, snapshot_id, item_id | **FK (snapshot_id, item_id) -> profile_snapshot_items.** A citation of an item that is not in the evaluated snapshot is a constraint error, not just a validation flag. Inferred claims are never in a snapshot, so they cannot be cited. Replaces the Postgres `int[]` column. |
| `recommendations` | id, evaluation_id FK, search_profile_id FK, verdict_rule_version, eligibility, eligibility_reasons (JSON), fit, fit_counts (JSON), opportunity, opportunity_factors (JSON), recommendation, decisive_reasons (JSON), rank_key, computed_at_ms | Unique (evaluation_id, search_profile_id, verdict_rule_version). |
| `applications` | id, job_id, status, notes, created_at_ms | User actions only. |
| `application_events` | id, application_id, from_status, to_status, at_ms, note | **Append-only by trigger.** |
| `agent_runs` | id, pid, version, started_at_ms, stopped_at_ms, stop_reason, heartbeat_at_ms | Restart/crash history for the dashboard and verify.sh. |

**Profile (implemented in P1.5,** `agent/src/jobforge_agent/profile/`**).**
- **Resumes:** `POST /v1/resumes?filename=&label=` takes the file as the raw request body (no
  multipart, no base64), so the stored BLOB is exactly what was sent; `GET .../{id}/original`
  returns it with an `X-Sha256` header. Markdown/text (UTF-8, falling back to cp1252), DOCX
  (standard library: `word/document.xml`) and PDF (`pypdf`) are extracted to text. A file whose
  text cannot be extracted is still stored, with `extracted_text` NULL and the reason in the
  response and a `resume_text_extraction_failed` warning. Limit 10 MB (413); other types 422.
  P1.5 live: 14 of a real PDF resumes round-tripped byte-identical, all with text.
- **Experience items:** `GET/POST /v1/experience`, `PATCH/DELETE /v1/experience/{id}`. Created
  `self_reported`; `status` and `verified_at_ms` are not accepted fields. Editing kind,
  statement, attributes, dates or years of a verified item clears the verification (Kevin
  confirmed the old text, not the new one); changing only `source` does not. An item accepted
  from an inferred claim cannot be deleted while the claim links to it (409).
- **Verify:** `POST /v1/experience/{id}/verify` (optional `note`, kept as
  `attributes.verify_note`) and `.../unverify`. `profile/items.py: verify_item` is the only code
  that writes `'verified'`, pinned by an AST scan of every SQL string that writes
  `experience_items` (a mutation that makes `create_item` insert `'verified'` fails it).
- **Eligibility facts** are `kind = 'eligibility'` items whose `attributes.type` is one of the
  E2 lexicon's eligibility types, each with typed fields, so P1.7 matches hard requirements by
  code (6.4 E3) instead of reading free text:

  | type | fields (required in bold) |
  |---|---|
  | work_authorization | **us_citizen**, **authorized_to_work_us** (bool) |
  | sponsorship | **needs_sponsorship** (bool) |
  | clearance | **level** (none/confidential/secret/top_secret/ts_sci), **status** (active/current/inactive/expired/none), polygraph (none/ci/full_scope), last_active_year |
  | residency | **country**, state, city, willing_to_relocate, time_zone (eastern/central/mountain/pacific/alaska/hawaii; 2026-10-24: a state can span zones, so "Eastern timezone" needs the person's answer) |
  | physical | max_lift_lbs, can_stand_hours (at least one) |
  | travel | **max_travel_percent** (0-100) |
  | license | **name**, **active**; several allowed |
  | program_status | active_duty, veteran, student (at least one) |
  | availability | on_call, weekends, holidays, night_shifts, rotating_shifts, other_time_zone_hours (bool, at least one) |

  `availability` (2026-10-24) is matched from interp-v3 / rules v3 (approved and merged 2026-10-24, v0.3.9): E2 types hours, shifts, weekends, on-call and zone-hours lines `availability`
  (not in the recall scan), and `facts._availability` checks them against the fact, hours in the person's
  own time zone needing nothing more. On-call is an availability fact, never a negative job attribute:
  opportunity value does not read duties. Residency reads the person's `time_zone` when a state spans zones.
  The E1 schema's type list is fixed with e1-v1 (`extract.E1_ELIGIBILITY_TYPES`). A new interpreter version
  re-reads each posting's stored E1 answer (`analysis/store.stored_extraction`, no model call), and startup
  queues current-version analyses for passing jobs (`pipeline.queue_current_analyses`). Before merging, on main,
  `availability` was stored but not matched (`items.AWAITING_CUES`). The E1 schema's type list is the lexicon's, so
  it is unchanged. Two tests hold that state (`test_eligibility_types_match_the_profile_fact_types`,
  `test_fact_types_cover_every_e2_eligibility_type`).

  One fact per type except licenses (409). Unknown fields are refused, so a typo cannot
  silently drop a fact. Rejected alternative: free-form attributes, which P1.7 code could only
  match by guessing.
- **Completeness:** `GET/PUT /v1/profile/completeness` (`cert_list_complete`,
  `eligibility_facts_complete`, both default off). A flag on means "not in my profile = I do not
  have it", which lets P1.7 read a missing cert or fact as a gap instead of unknown.
- **Inferred-claims inbox:** `GET /v1/inferred-claims?state=open|accepted|rejected`,
  `POST .../{id}/accept` (optional rewording; becomes a self-reported item with
  `source = inferred_claim:<id>`, never verified, never an eligibility fact) and `.../reject`.
  Evaluation inserts claims (P1.7, E3 guesses).
- **Suggestions from a resume (P1.8c):** `POST /v1/resumes/{id}/suggest-items` (`profile/suggest.py`, prompt
  `RESUME_PROMPT_VERSION`, pinned by a hash). The default model, if turned on, reads the resume's extracted text (limit
  30,000 characters; 300 s and 8,000 output tokens for this call; the host allows the path 330 s). Code keeps a suggestion
  only if its quote is found in the resume text word for word (case, punctuation and line breaks ignored), takes dates
  only as YYYY-MM (a year-only date is kept as written in `attributes.dates_as_written`, never padded to a month, because
  an empty end date means "present"), and skips statements already in the profile or already suggested (open, accepted
  or rejected). What stands goes to the inbox with `evidence = {resume_id, resume_sha256, quote, prompt_version}`;
  accepting applies `proposed_fields` and sets `source = resume:<resume id>:claim:<claim id>`. No eligibility facts are
  suggested (they are typed facts with their own form). The model call runs with no transaction open; the inbox write
  is one user-action transaction (`profile_changed`, ids only). Rejected alternative: writing suggestions straight into
  `experience_items`, which would put unread model output into the profile E3 matches against.
- Every change is one user-action transaction with a `profile_changed` event whose ref holds
  the action name and ids only; the same goes for the log line. Profile snapshots are created
  by evaluation in P1.7.

### 4.3 Postgres guarantees and their replacements

The earlier drafts leaned on Postgres for several guarantees. Each one needs
a named replacement and a test, or it silently disappears.

| Guarantee (rev 1-3) | Postgres mechanism | Rev 4 replacement | Test that pins it |
|---|---|---|---|
| **Worker cannot write or verify experience** | `jobforge_worker` role without UPDATE/INSERT on `experience_items` | **Per-connection SQLite authorizer** (`Connection.set_authorizer`): each component opens its own connection with an allow-list of tables it may write; anything else returns `SQLITE_DENY`. The discovery and evaluation connections deny writes to `experience_items`, `profile_completeness`, `applications`, `application_events`, `settings`. Only the user-action connection (used solely by IPC handlers for explicit user commands) may write them, and `verify` is the only handler that sets `status='verified'`. Plus the CHECK constraint, plus `match_citations` FK. | Evaluation connection `UPDATE experience_items SET status='verified'` -> `sqlite3.DatabaseError: not authorized`; same for INSERT. Discovery connection write to `applications` -> denied. Source scans (P1.5): only `agent.py` (the runtime that serves IPC) opens the user-action connection, and only `profile/items.py: verify_item` has a SQL string writing `'verified'` to `experience_items` (corrected from the planned `ipc/routes/` layout, which the code never used). |
| | | **Honest limit:** roles defended against a compromised or buggy *process with a different login*. Here everything runs as Kevin's Windows account, so the authorizer defends against bugs and against model output steering code paths, not against malware running as Kevin (which could edit the file directly). That threat was equally out of reach in the Docker design, since the DB volume was Kevin's too. | |
| **Append-only posting history** (and insert-only profiles, snapshots, events) | No UPDATE/DELETE grants | **Triggers** `BEFORE UPDATE` / `BEFORE DELETE ... RAISE(ABORT, '<table> is append-only')` on `raw_postings`, `search_profiles`, `profile_snapshots`, `profile_snapshot_items`, `application_events`; `change_events` allows only DELETE of rows older than 7 days. Triggers apply to every connection, including future code. Migrations that must rebuild a table drop and recreate its trigger explicitly in the same migration. | UPDATE and DELETE on each table -> `IntegrityError` with the message; a migration test asserts every append-only table still has both triggers after the latest migration. |
| **Atomic queue claims** | `FOR UPDATE SKIP LOCKED` | `BEGIN IMMEDIATE` + `UPDATE ... WHERE id IN (SELECT ... LIMIT n) RETURNING id`. SQLite allows one writer at a time, so a claim cannot interleave with another claim. Leases (`lease_until_ms`) remain for in-process timeouts. | Two writer connections claiming from separate threads 1,000 times: claimed id sets are disjoint and complete. |
| **One worker per queue / no overlapping polls** | Multiple workers coordinated by row locks | **One agent per user**: exclusive OS lock on `run\agent.lock` (`LockFileEx`, released by Windows when the process dies, so a crash never leaves a stale lock). Plus the lease and the partial unique index on in-flight `source_fetches`. | Start a second agent -> exits 3, log `already_running`; `kill -9` the first -> a new agent acquires the lock within 1 s; forced second in-flight fetch row -> constraint error. |
| **Crash recovery** | Lease expiry + reclaim | Startup recovery: since the lock proves no other agent exists, every `running` analysis/evaluation and every unfinished fetch is an orphan. Reset to `pending` with `attempts += 1` (`failed` after 3 attempts, error kept) and close fetches as `INTERRUPTED`. SQLite rolls back any uncommitted transaction from the WAL automatically. | Lifecycle test: kill the agent mid-poll and mid-evaluation (subprocess, `TerminateProcess`), restart: orphans recovered, no duplicate rows, `PRAGMA integrity_check` = ok, work completes. |
| **Concurrent UI and agent access** | Multi-client server | **Only the agent opens the DB.** The UI goes through IPC. Inside the agent: WAL + one serialized writer + read-only readers + the no-transaction-across-await rule (3.4). | Fake model call held open; a `verify` user action commits in < 1 s during it (`tests/test_evaluation.py::test_long_model_call_does_not_block_a_user_action`, P1.7: the call is held > 1.2 s, then released; 30 s added nothing the assertion needs). Readers see committed data while a write transaction is open. |
| **Transactional event delivery** | `change_events` row in the same transaction + SSE polling the table | Same table, same transaction. The broadcaster is woken by the agent's own commit (in-process signal), then reads rows with `id > last_sent`, so it can never send an event whose data was rolled back. Delivery to the UI is over the IPC event stream; the host and UI resume from `Last-Event-ID` after reconnect or a host restart. | Rolled-back transaction -> no event delivered. Committed change -> event received by a test subscriber within 1 s. Disconnect, make 3 changes, reconnect with cursor -> exactly those 3 replayed in order. |
| **Foreign keys** | Always on | `PRAGMA foreign_keys=ON` per connection (off by default) | Startup assertion; insert an orphan FK row -> error. |
| **Types and enums** | Column types | `STRICT` tables + CHECK constraints | Insert a string into an INTEGER column -> error; invalid enum -> error. |
| **Clock** | `now()` on the server | Agent passes UTC `now`; monotonic clock for gap detection | Scheduler tests with an injected clock that jumps forward 3 h (sleep) and backward 1 h (clock correction): no burst, no double poll. |

### 4.4 Migrations, backups and rollback

- **Migrations:** numbered SQL files in `db/migrations/`, applied in order, with
  the version kept in `PRAGMA user_version`. Each migration runs in one
  transaction (SQLite DDL is transactional), so a migration killed halfway
  leaves the previous version intact. Forward-only.
- **Before any migration:** online backup with SQLite's backup API (consistent under WAL)
  to `backups\pre-migrate-v<old>-<timestamp>.db`, then an integrity check of the copy. No
  backup, no migration. Implemented in `agent/src/jobforge_agent/db/backup.py`.
- **Newer database than the agent** (user rolled back the app): the agent
  refuses to start, exits 4, and the window shows the restore screen (section 5). It never runs
  on a schema it does not know. The schema the agent knows is `migrate.SCHEMA_VERSION`, a
  constant in code; migration files above it are ignored. Before P1.9 it was the highest file
  on disk, and the release drill showed why that fails: a downgrade installer replaces files
  but leaves newer ones, so v0.1 found v0.2's `0008` file and ran on the schema-8 database
  (`docs/evidence/p1_9_release_drill.txt`). The installer now also clears `agent\` before
  copying (7.2).
- **Backup files** are written in rollback-journal mode (`journal_mode = DELETE`), so checking
  or restoring one read-only leaves no `-wal`/`-shm` files (the drill found two per backup).
- **Rollback procedure:** install the previous version's installer, then
  `jobforge-agent --restore <pre-migrate backup>` (also a button in the host's
  error screen). Restore takes the agent lock first, so it cannot run under a
  live agent. It copies the backup into `jobforge.db.restore-tmp`, integrity-checks that copy, and only
  then replaces the live file (audit 2026-10-24: the live file used to be deleted before the backup was
  read, so a failed copy left no database and the next start silently created an empty one).
- **Daily backup:** backup API, keep 7, integrity-checked; manual `--backup` keeps `manual-*.db`.
  Pre-migrate backups (keep 5) are pruned oldest-first by schema version, then name: plain name order
  put `v10` before `v9` and would have deleted the newest one.
- **Read-only opens** use `Path.as_uri()` (`db/connect.readonly_uri`): an f-string `file:{path}` URI cut
  the path at a `#` in the data root and opened another file.
- Rejected: Alembic. Good on Postgres, but on SQLite most real changes are
  hand-written table rebuilds anyway, and the autogenerate value is small;
  plain SQL files are easier to review and package.

---

## 5. IPC design

**Transport: HTTP/JSON on an ephemeral `127.0.0.1` port, bearer token, called
only by the Rust host.**

- Agent binds `127.0.0.1:0` (OS picks the port), generates a fresh 256-bit
  token each start, and writes `run\ipc.json` = `{port, token, pid,
  protocol_version}` with an atomic replace. The file sits in the user's
  `%LOCALAPPDATA%`, whose default ACL grants only the user, SYSTEM and
  Administrators; the agent additionally sets an explicit DACL on `run\`
  (user SID only) and verifies it at startup.
- Every request needs `Authorization: Bearer <token>` (constant-time compare)
  and `Host: 127.0.0.1:<port>`; otherwise 401/400. No CORS headers at all.
- **The token never reaches JavaScript.** The React UI calls Tauri commands
  (`invoke("agent_call", {method, path, body})`); the Rust host adds the token
  and forwards. Tauri capabilities allow only that command plus window/tray
  basics; no shell, fs or http plugins exposed to the webview.
- **Events:** the host keeps one streaming connection to `GET /v1/events`
  (Server-Sent Events over the same authenticated channel, resumable with
  `Last-Event-ID`) and re-emits each event to the webview as a Tauri event.
  The UI invalidates the affected queries.
- **Handshake and version skew:** host calls `GET /v1/health`, which returns
  agent version and `protocol_version`. Mismatch (old agent still running
  after an update) -> host requests `/v1/shutdown` and spawns the bundled agent.
- **Posting analysis (P1.6):** model-config routes (6.8), `GET /v1/analysis/status` (counts by
  status, pending work waiting on a disabled config, gate state per config),
  `GET /v1/analyses/{id}` and `GET /v1/jobs/{id}/analysis` (sections plus every requirement
  with both readings and flags). Profile routes: 4.2; filter routes: 3.3.
- **Evaluation (P1.7):** `GET /v1/evaluation/status`, `GET /v1/jobs/{id}/recommendation` (the
  three assessments, decisive reasons, and the evidence table: every requirement with its match,
  citations, basis, bridge, gap and flags; `newer_evaluation_pending` while a re-evaluation
  waits), `GET /v1/recommendations?category=APPLY|STRETCH|REVIEW|SKIP` (ordered by category,
  then opportunity `rank_key`), `GET /v1/review-questions` (REVIEW questions grouped across jobs:
  one eligibility type, one group), `POST /v1/recompute` (snapshot, queue and recompute now; code
  only).
- **Implemented so far (P1.1-P1.2):** `GET /v1/health`, `POST /v1/shutdown`,
  `POST /v1/discovery/pause|resume`, `GET /v1/events` (SSE; resumes after `Last-Event-ID`;
  new subscribers get only future events; woken by the committing code, 2 s fallback poll,
  15 s keepalive; ends when the agent stops so it never blocks shutdown; a `Last-Event-ID` above the
  newest row, after a restored backup or an emptied log, resumes from now). The host's
  `agent_call` refuses `/v1/shutdown` and `/v1/events` from the webview: stopping is a host
  action and the stream is relayed by the host. The route part of a window path must be plain (no
  `.`/`..` segments, `%` escapes, `\` or `//`; audit 2026-10-24: the URL parser collapsed
  `/v1/../v1/shutdown`, so a prefix check alone did not hold). The host also treats the agent as running
  only when `/v1/health` reports the pid written in `ipc.json` (readable only by the user): a stale
  `ipc.json` after a crash points at a port another process could bind.
- **Agent IPC errors:** malformed JSON answers 400; any unhandled exception answers a JSON 500 and is
  written to `agent.jsonl` as `ipc_unhandled_error` (it went only to the console log before). A
  background task (heartbeat, maintenance, discovery, analysis) that ends before a stop is logged as
  `background_task_died` and listed in `/v1/health` `task_errors`.
- **Reading problems in "Needs your input" (2026-10-24):** `views.review_questions` marks a question `reading_problem`
  when its eligibility reason has basis `verified` and effect `unknown` (the confirmed fact was checked; the line is a
  model/code disagreement or a recall line, which never decides a job). The window lists those under "JobForge
  couldn't settle these" with the check's outcome and "I'd skip it", and does not count them as needing input
  (`desktop/src/reviewGroups.ts`). Display only.
- **Window navigation (UX round 1, 2026-10-24):** opening a job hides, but keeps mounted, the page it came from
  (`App.tsx`), so Back returns to the same tab, search, filters, sort, scroll position and focused row; the job page
  leads with Open job posting and Save job, "I'd skip it" last and quieter.
- **Window (P1.8):** `desktop/src` (React): Jobs (APPLY + STRETCH by default, ranked by opportunity value; Filtered out and
  Skipped by you views; search; column headers sort and a filter row (fit, pay/place/level or filter reason, found within) narrows the loaded rows, `desktop/src/sortJobs.ts` and `filterJobs.ts`; the agent's `view=all` stays for tests and evidence, not in the window), job detail (three assessments, decisive reasons, eligibility checks, opportunity factors, the evidence
  table with the cited items **as the evaluated snapshot recorded them**, application status, the sanitized posting),
  REVIEW questions grouped across jobs, Profile (items by kind, typed eligibility-fact forms built from
  `GET /v1/profile/schema`, verify/unverify, completeness, resumes, inferred claims, import of a reviewed JSON file, all
  self-reported), Applications, Dashboard, Preferences (search profile form + full rules JSON, model configs; P1.8b replaced
  the JSON editor with the plain form below and renamed Dashboard to Status). Views reload
  on the agent events that concern them and when the agent becomes reachable; no view polls the database.
  Agent routes added: `GET /v1/jobs?view=recommended|rejected|all&q=&limit=&offset=`, `GET /v1/jobs/{id}`,
  `PUT /v1/jobs/{id}/application`, `GET /v1/applications`, `GET /v1/profile/schema`. Recommendation rows carry `current`
  and `newer_evaluation_pending`; list rows also carry the posting's `salary` and the recommendation's `fit_counts`
  (UX redesign step 2, display only: a row says its pay and why it is listed). Every route the window calls is in `desktop/src/api.ts`; a test fails if one is not served.
  UI types are written by hand (the routes return plain JSON objects with no response models, so the D14 plan of
  generating them from OpenAPI had nothing to generate; corrected in P1.8).
- **First-run setup and plain settings (P1.8b):** `GET /v1/setup/status` (companies, search profile, model, profile:
  each `done` plus counts; the window opens Setup until the first three are done), `GET|POST /v1/search-profile/form`
  (the search profile as plain fields; `filtering/simple.py` maps them to rules over the current version and keeps every
  field the form does not own; a pattern with regex logic is kept exactly and returned in `kept_patterns`, never dropped;
  rejected alternative: rebuilding rules from the form, which would silently lose tuned patterns),
  `POST /v1/search-profile/import-scanner {text}` (job-scanner config.yaml), `POST /v1/employers/add {text}` (careers
  links or company names, one per line; P1.10, host timeout 150 s), `POST /v1/employers/import-watchlist {text}`,
  `POST /v1/employers/{id}/enable|disable`, `POST /v1/employers/built-in {watch}` (the built-in list switch). The
  window shows no JSON or regex; internal values are translated in `desktop/src/labels.ts`.
- **Golden set (P1.9):** `POST /v1/jobs/{id}/golden-label` (`{recommendation, eligibility?, obvious?,
  misread_requirement_ids?, note?}`; requirement ids must belong to the job), `POST /v1/jobs/{id}/golden-label/withdraw`,
  `GET /v1/golden/report`; job detail carries `golden_label`. `keep_details: true` keeps the current label's
  eligibility, obvious, misread ids and note (the one-click Skip); `GET /v1/jobs?view=skipped` lists jobs labeled SKIP
  and their copies, and each row carries `skipped` (`you`, `copy` or null). The window's "Your call" card on a job writes them; Status
  shows the count against the 20-30 target. `jobforge-agent golden [--json]` prints the report from a read-only
  connection (safe beside a running agent); `jobforge-agent golden --suite interpretation [--config ID]` runs 6.6 C.
- **Restore screen (P1.9, host):** commands `list_backups` (backup files with the schema version read from each SQLite
  header, plus the live database's) and `restore_backup(name)` (only in state `stopped_db_newer`; plain file names in
  `<data root>\backups` only; runs the agent's `--restore`, then starts the agent). The window shows it in place of the
  pages when the agent stopped with exit 4.
- **Host bridge (P1.8):** `agent_call(method, path, body?)` (GET/POST/PUT/PATCH/DELETE, JSON; 4xx/5xx come back with the
  agent's own message), `agent_upload(path, bytes)` (resumes only, raw bytes), `open_url(url)` (system browser; http(s)
  only, no credentials, no control characters, <= 2048 chars; `javascript:`, `file:`, `ms-settings:`, UNC refused; space,
`"`, `<`, `>` and backtick reach ShellExecute percent-encoded, so a browser registered as `"x.exe" "%1"` cannot be handed
extra arguments). The
  event relay keeps its cursor in `<data root>\host\event_cursor`, so a restarted host resumes from it (before P1.8 a
  restarted host started from "now" and events committed while it was down were never delivered).
- **Host command line:** `JobForge.exe [--background] [--exit] [--autostart on|off]`; with a
  host already running, the single-instance plugin forwards the arguments to it.
- **API surface** (same routes as rev 3, now under `/v1`): health, status
  (freshness, queues, inference, errors), discovery pause/resume/poll-now,
  jobs, evaluations, evaluate-anyway, search profile versions, resumes,
  experience items + verify, profile completeness, inferred claims,
  review questions, model configs + test, employers/sources admin, refilter,
  recompute, applications, backups, shutdown, events.

**Why this over the alternatives:**

| Option | Verdict |
|---|---|
| **Localhost HTTP + per-start token via host (chosen)** | Plain, testable with `curl` (verify.sh uses `ipc.json`), reuses FastAPI and OpenAPI-generated types, works when the host restarts. Exposure: any local process can connect to the port, but without the token it gets 401; browsers cannot read the token file, so the rev 1-3 CSRF and DNS-rebinding concerns reduce to the Host and token checks. |
| Windows named pipe with a user-only DACL | Strongest isolation (no TCP socket at all). Rejected for Phase 1 because the Python side needs either an undocumented asyncio API (`ProactorEventLoop.start_serving_pipe`) or pywin32 overlapped I/O for a server, which is more custom code than the threat warrants. Logged in DEFERRED_COMPLEXITY.md as the hardening path. |
| Tauri sidecar stdin/stdout | Simplest wiring, but the pipes die with the host, so the agent could not outlive a host crash or run before the host exists. Conflicts with the requirement. |
| Webview calls the agent directly | Needs CORS and puts the token in JavaScript, where job-description XSS could read it. |

---

## 6. Evaluation design and local inference

Unchanged from rev 2 except storage: queues are SQLite tables claimed as in
4.3, citations are FK-checked through `match_citations`, and the agent calls
the inference server on `127.0.0.1` directly (no container networking).

### 6.1 Philosophy and categories

JobForge is a middle ground between a conservative qualification screen and
an optimistic matcher. Applying through JobForge costs no credits, so
plausible stretch roles should surface. The guard against noise is that every
surfaced job carries specific evidence, and SKIP and APPLY each require
positive evidence; uncertainty goes to REVIEW, which is designed to be
resolved, not browsed.

| Recommendation | Meaning | Requires |
|---|---|---|
| **APPLY** | Strong or realistically competitive match, incl. credible transferable experience | Eligibility ELIGIBLE; fit STRONG or COMPETITIVE; enough validated requirements to judge |
| **STRETCH** | Meaningful gaps, still plausible | Eligibility ELIGIBLE; fit STRETCH; opportunity not LOW |
| **REVIEW** | Evidence insufficient or conflicting for a confident call | Any unresolved uncertainty that could change the outcome (6.5) |
| **SKIP** | Genuine hard blocker or substantial mismatch | A confirmed blocker, or a WEAK fit judged on sufficient evidence, or a STRETCH with LOW opportunity value |

Invariant (pinned by a test): **SKIP never results from missing evidence
alone.** A model-claimed blocker whose quote was not found, whose
interpretation conflicts, or which the profile cannot answer produces REVIEW,
never SKIP.

### 6.2 Three assessments, never combined into a number

| Assessment | Question | Values | Computed from |
|---|---|---|---|
| **Eligibility** | Can Kevin be hired for this at all? | ELIGIBLE / BLOCKED / UNKNOWN | Requirements with `final_class = hard`, matched against declared eligibility facts by code where a fact exists |
| **Qualification fit** | How well does demonstrated experience cover what the job needs? | STRONG / COMPETITIVE / STRETCH / WEAK / INSUFFICIENT | Negotiable and preferred requirements and their match types |
| **Opportunity value** | Is this job worth Kevin's time if he qualifies? | HIGH / MEDIUM / LOW / UNKNOWN | Deterministic factors vs the search profile: pay vs floor and target, work mode, location, seniority vs target, role family vs preferred directions, posting age. No LLM in Phase 1. |

The UI shows all three side by side plus the recommendation. Opportunity value
also orders the list within a category.

### 6.3 Requirement taxonomy

**Requirement class** (`final_class`, decided by code in E2):

| Class | Meaning | Examples |
|---|---|---|
| hard | Eligibility-type condition stated as mandatory with no equivalence clause | "Must be a US citizen", "Active TS/SCI required", "Must reside in Northern California", "No visa sponsorship" (hard only if the profile says sponsorship is needed) |
| negotiable | A required skill, years, degree or certification that employers routinely trade off | "5+ years Linux administration", "Bachelor's in CS or equivalent experience", "CCNA required" |
| preferred | Nice-to-have | "AWS experience a plus", anything in a Preferred section with no "must" language |
| context | Text about the company, team, stack, benefits or EEO, not the candidate | "We use Terraform", "We pay for certifications", "regardless of veteran status" |
| negated | Explicitly not required | "No prior cloud experience required", "Degree not required" |

Only eligibility types can be hard: work authorization, sponsorship,
clearance held (not "ability to obtain"), legally required license,
residency/location, travel or physical requirements. Skills, years and
degrees are at most negotiable, however strongly worded. This is decision D6
(SPEC); the rejected alternative is letting the model label any requirement
"hard", which is how one strongly worded skill line turns a stretch job into
a SKIP.

**Match type** (`requirement_matches.match_type`, from E3 and validated in E4):

| Match | Meaning | Counts as covered? |
|---|---|---|
| direct | Cited profile items show this experience | Yes. Basis verified or self_reported, shown separately |
| transferable | Cited items show adjacent experience; `bridge` states what transfers and `transfer_limits` what does not | Yes for negotiable/preferred, as transferable. Never satisfies a hard requirement |
| gap | Profile shows the experience is absent or short (e.g. 2 years vs 5) | No. Materiality computed by code |
| unknown | Profile is silent and absence cannot be concluded | No, and counts toward uncertainty |

Transferable vs inferred, the distinction that matters: a transferable match
cites **real** profile items (verified or self-reported) and argues they apply.
An inferred claim asserts experience **not stated anywhere** and lives in
`inferred_claims`; it is never in a profile snapshot, so `match_citations`
cannot reference it and it can never cover a requirement.

**Gap materiality** (code, thresholds in `verdict_rules/vN.yaml`):
- Gap on a preferred requirement: minor.
- Gap on a negotiable requirement: material, except a years shortfall within
  tolerance (proposed v1: short by at most 2 years or 40% of the ask), which is minor.

### 6.4 Pipeline E0-E6

| Stage | Who | Input -> output | Guards |
|---|---|---|---|
| **E0 Segment** | Code | Raw description -> sections with labels (requirements / preferred / responsibilities / benefits / about / eeo / privacy / unknown) from headings via a lexicon, and sentences | Postings without headings get one `unknown` section; that lowers confidence, it does not fail |
| **E1 Extract** | LLM (full original text) | Requirements: `{text, jd_quote, importance (hard/required/preferred), polarity (affirmative/negated), subject (candidate/company/team/benefit), eligibility_type (incl. program_status, or none), min_years?}` | Schema-validated; one retry, then `failed` with raw response kept |
| **E2 Interpret** | Code | Each requirement -> quote location, section, sentence, cue reading, `final_class`, `interpretation_status` | See "interpretation checks" below |
| **E3 Match** | LLM | Requirements with class != context/negated, profile snapshot items with ids and status -> per requirement `{match_type, cited_item_ids, bridge?, transfer_limits?, gap_detail?}`; optional inferred claims | Every requirement with a code-assigned eligibility type (hard or not, fact declared or not) is matched by code against the typed facts, never by the model (corrected in P1.7; the text said only those "that map to a declared fact", which would have sent an unanswered citizenship requirement to the model to "cover" from a résumé line) |
| **E4 Validate** | Code | Model matches -> validated matches | Citations inserted through `match_citations` (FK to the snapshot); `basis` from item status; transferable needs >= 1 citation and a non-empty bridge, else downgraded to unknown; a "direct" citing only items unrelated by kind is flagged; gap size and materiality computed |
| **E5 Assess** | Code | Requirements + matches + search profile -> eligibility, fit, opportunity value | Rules 6.5 |
| **E6 Recommend** | Code | Three assessments -> APPLY / STRETCH / REVIEW / SKIP + decisive reasons | Rules 6.5 |

**Interpretation checks (E2): a found quote is not a correct reading.**
E2 computes an independent reading from the quote's surroundings and
compares it to the model's:

1. **Quote location.** Normalized substring match. Not found -> requirement
   kept for display, `interpretation_status=unverifiable`, excluded from all
   assessments except as an uncertainty count. Found more than once in
   different sections -> flag `ambiguous_location`, use the most
   requirement-like section only if all occurrences agree, else unverifiable.
2. **Negation.** Cue scan of the containing sentence: "not required", "no
   ... required/necessary", "don't need", "without", "not a requirement",
   "we will train". Model says required but cues say negated -> conflict.
3. **Importance.** Section label plus modality cues: "must", "required",
   "minimum" vs "preferred", "nice to have", "a plus", "bonus", "ideally",
   and equivalence clauses ("or equivalent", "or related experience",
   "or X years in lieu of"). An equivalence clause caps the class at negotiable.
   A "must" inside a Preferred section is a conflict. Downgrading wording inside
   a Requirements section ("X is a plus" under "Qualifications") is not: the
   wording wins and is flagged `wording_overrides_section` (corrected in P1.6;
   the spike reference labels the Vultr line preferred, and calling every such
   line a conflict would push routine postings toward INSUFFICIENT fit).
4. **Subject.** Requirement text found in about / benefits / eeo sections, or
   a sentence whose subject is the company or team ("our customers", "we use",
   "you will mentor engineers with 10+ years") -> context. Model said candidate
   requirement -> conflict.
5. **Eligibility nuance.** "Ability to obtain" a clearance is not "clearance
   held"; it is negotiable at most and matched against the declared fact.
   (Spike: every model labeled "must be able to obtain a Secret clearance" hard.)
6. **Eligibility type comes from code** (added after spike P1.0b, where the
   model's `eligibility_type` was right on 1-2 of 4 blockers). A lexicon over
   the requirement sentence assigns the type: citizen/citizenship/authorized to
   work -> work_authorization; sponsor/visa -> sponsorship;
   clearance/Secret/TS/SCI/polygraph -> clearance; reside/residing/located
   in/commuting distance/must live -> residency; lift/pounds/lbs/stand ->
   physical; travel up to -> travel; licensed/license required -> license;
   active-duty/SkillBridge/enrolled student/veteran-only program ->
   program_status. Model and lexicon disagree -> `conflict` (never a blocker).
   No lexicon match -> the model's label is kept only as a hint and the
   requirement cannot be `hard`.
7. **Eligibility recall scan** (added after spike P1.0b, where the best model
   missed the SkillBridge active-duty condition). The same lexicon runs over
   every sentence of the posting, excluding EEO and privacy sections
   identified by E0 and EEO/privacy sentences anywhere (corrected in P1.6:
   benefits sections are scanned; see "Posting analysis E0-E2" below). Any eligibility-cue sentence that no extracted
   requirement overlaps becomes an `unextracted_eligibility` row
   (`interpretation_status = unverifiable`, quote = the sentence). It makes
   eligibility UNKNOWN and produces a REVIEW question; it can never produce
   SKIP. This turns a silent model miss into a visible one.

**Posting analysis E0-E2 (implemented in P1.6,** `agent/src/jobforge_agent/analysis/`**).**
- **E0** (`segment.py`): input is `raw_postings.description_text`, the same text E1 sees. A
  heading is a short standalone line (<= 70 chars, <= 8 words, starts with a letter, no
  terminal punctuation, no `field: value` colon). A lexicon heading gets its label; any other
  heading-shaped line after a blank line starts an `unknown` section (except a list lead-in: a
  line ending in ':' with more than 4 words or ending in "of", "with", "in", ...), so text under a heading the
  lexicon does not know ("Logistics", which in Anthropic's postings holds the visa and office
  rules) is never swallowed by the benefits section before it. Inside `eeo` and `privacy` only a
  lexicon heading ends the section (legal notices have their own sub-headings). Labels add
  `privacy` to the list above, because check 7 excludes privacy text. Context labels (about,
  benefits, eeo, privacy) are given only for unambiguous headings ("Join Vultr" is that
  posting's role description, so it stays `unknown`). Stored in `posting_analyses.sections` as
  `{version, headings, sections: [{label, heading, sentences}]}`; `headings = 0` is the
  low-confidence case. P1.6 dry run on the 42 queued real postings: 8 labels, 0-11 headings
  each, every posting with a requirements or preferred section except one.
- **E1** (`extract.py`, prompt `e1-v1`): the spike prompt e1-draft-2 plus `program_status` and
  the "quote the bullet, never its heading" rule. Polarity values are
  `affirmative`/`negated` (the table above said `required/negated`, the exact shared-enum
  defect spike P1.0b found; corrected here). Output is validated with Pydantic whatever the
  server's structured-output mode claims.
- **E2** (`interpret.py`, lexicons in `cues.py`): quotes are matched after `norm()` (lowercase,
  straight quotes, plain hyphens, bullets and whitespace collapsed) with trailing punctuation
  dropped. Negation and modality are read on the quote's **clause** (split at `;`, "but",
  "however", ...), so "Clearance not required, but must be able to obtain a Secret clearance"
  reads the second clause as affirmative; subject is read on the whole sentence. The lexicon
  for check 6 runs on the **quote**, not the sentence, so "5+ years of Linux; must be a US
  citizen" does not give the Linux requirement a work-authorization type. Parenthesised text is
  ignored for modality ("(preferably Ruby or Bash)" qualifies a choice). "Secret" counts as a
  clearance cue only next to clearance wording (P1.6 dry run: Supabase's "our secret weapon").
  `requirements.flags` is a JSON list of `{flag, ...}`: the `conflict` flag carries
  `fields: [{field, model, cue}]`, i.e. both readings of every disagreement; other flags:
  `equivalence`, `years` (`values` = every branch), `years_disagree`, `ability_to_obtain`,
  `clearance_held`, `wording_overrides_section`, `required_wording_in_preferred_section`,
  `modality_mixed`, `context_section`, `eeo_text`, `privacy_text`, `company_statement`,
  `years_about_others`, `model_eligibility_hint_only`, `model_hard_without_eligibility_type`,
  `subject_conflict_kept_for_eligibility`, `eligibility_in_context_section`,
  `disagreement_ignored_for_context`, `heading_joined_quote`, `ambiguous_location`,
  `quote_not_found`, `unextracted_eligibility`.
- **`interp-v2` (P1.7, from the real profile run):** the residency lexicon also reads "be local to" / "local to the ... area,
  office, region, metro, city, campus, site" and on-site / in-office attendance with a days-per-week count. Keeper's "be local
  to the El Dorado Hills area" and "on-site a minimum of 3 days per week" had reached Kevin as experience questions instead of
  eligibility. Not "data local to each node" or "provide on-site support" (layer-A controls). Precision check over all 13,493
  stored postings: 542 matching sentences, 127 distinct, a sample of 25 all genuine location or attendance conditions.
- **Benefits and about text can hold eligibility conditions** (P1.6 live, 5 of the first 8
  real postings: Vultr's state-residency list under "Compensation", Lyft's "must be based in the
  US" under "Benefits:"). A quote with an eligibility cue in a benefits or about section keeps the
  candidate subject (flag `eligibility_in_context_section`), and the recall scan reads those
  sections. Only EEO and privacy text is exempt. The first P1.6 design (benefits = context,
  not scanned, as D15 said) turned those blockers into `context`: a silent ELIGIBLE.
- **List lead-in joined to a bullet** (flag `heading_joined_quote`): Gemma quoted
  "Demonstrated understanding of:" + a later bullet on 7 of 15 items of Vultr's Linux
  Systems Administrator posting despite the prompt rule. Located when the lead-in is its own
  line and the nearest preceding ':' line in the same section; the lead-in's wording is read
  for modality. A bullet under a different lead-in stays unverifiable. Every bullet also
  inherits its lead-in's wording (E0 records `Sentence.lead`): Bitwarden lists 18 tools under
  "Familiarity with several of the following ... is preferred:" inside its requirements section,
  which otherwise read as 18 importance conflicts.
- **Context rows ignore other disagreements:** when both readings say a line is not about the
  candidate, an importance or polarity disagreement is recorded
  (`disagreement_ignored_for_context`) but is not a conflict.
- **Two rules added in P1.6** (not in the checks above, each pinned by a layer-A fixture):
  (a) a requirement whose quote has an eligibility cue and whose subject is disputed (model says
  candidate, cue says company, outside EEO/privacy text) stays a requirement with status
  `conflict` instead of becoming `context`: "more permissive" would otherwise make a misplaced
  residency rule vanish, a silent ELIGIBLE. (b) A recall-scan sentence counts as covered only
  if an overlapping requirement carries that sentence's code-assigned type, so "Bachelor's
  degree and US citizenship required" with only the degree extracted still yields a
  work-authorization row. Recall rows store the code's own reading (`cue_polarity`, class)
  so P1.7 can tell "We do not offer visa sponsorship" from "No clearance required".
- **Worker** (`worker.py`, `store.py`): claim (one transaction) -> model call (none open) ->
  persist (one transaction), pinned by a fake server that records `in_transaction` on every
  agent connection during each call. Outcomes and attempt accounting are in the module
  docstring; `analysis_done` / `analysis_failed` change events carry ids and counts.
  `inference_available` / `inference_unavailable` are logged on gate transitions.

**Evaluation E3-E6 (implemented in P1.7,** `agent/src/jobforge_agent/evaluation/`**).**
- **Snapshots** (`snapshot.py`): content-addressed (sha256 of every item's id, kind, statement,
  status, attributes, years and dates, plus the completeness flags). An unchanged profile maps to
  the same snapshot and queues nothing; any change, including a verify, is a new snapshot and new
  evaluations for every open job (D10). Migration 0004 adds `years`, `start_date`, `end_date` to
  `profile_snapshot_items` because E4 computes years from the snapshot.
- **Queue** (`store.sync`, code only, runs while inference is down): an evaluation is queued for
  each open job whose current posting has a done analysis and that passes the active search
  profile or has an override, once per (analysis, snapshot, model config, `m1-v1`). An older
  pending evaluation of the same job is superseded (migration 0004: `evaluations.job_id`,
  status `superseded`, one pending per job). The same pass recomputes missing recommendations.
- **Split:** requirements with an eligibility type go to `facts.py` (code); negotiable and
  preferred requirements without one, confirmed or conflict, go to the model; unverifiable,
  context and negated rows are not matched. With no non-eligibility profile items, or nothing to
  match, no model call is made (rows recorded `unknown`, flag `no_profile_items_model_not_called`).
- **Eligibility facts by code** (`facts.py`): outcome met / fails / unanswered per type. Residency
  reads US states (names, and "City, ST" in the original-case quote; lower-case two-letter codes
  that are English words such as "in" or "or" are not states), US time zones (a state only partly
  in the zone is unanswered), country phrases (the pronoun "us" does not count). Clearance: held =
  status active/current and level >= required, polygraph checked when named; "ability to obtain"
  is met by having held that level; an inactive clearance with an "ability to obtain" sibling in
  the same posting is unanswered, not fails (6.5). Sponsorship: not needed -> met; needed ->
  fails on "no/not/unable", unanswered on hedges ("may", "existing transfers", "generally").
  Travel: a percentage is compared; "occasional" is met at >= 10% and fails at 0%. Program status
  (SkillBridge -> active_duty). With `eligibility_facts_complete`, a missing clearance, license or
  program fact fails with basis `none`, which never blocks. Fixtures: `tests/fixtures/verdict/facts.yaml`
  (17 of them are the real P1.6 hard and recall rows).
- **Independent review (P1.7, before commit)** found nine defects, each now a fixture or test.
  Fact matching: a country written "United States of America" or "U.S" read as non-US (a false
  block); exclusion wording ("US, excluding Alaska and Hawaii", "not open to residents of ...")
  read as an allowed list (now unanswered: asked, not parsed); a time zone counted as a place even
  in "work Eastern Time hours" (now only "located/live/based in the ... time zone"); a bare "TS"
  read as any clearance; the first "N%" in the sentence read as travel ("100% remote") and a range
  read at one end (now compared at both, unanswered in between); any "not" read as refusing
  sponsorship ("candidates who are not US citizens"); a missing clearance fact with completeness
  on failed "ability to obtain" (a material gap, a route to SKIP through fit; now unanswered).
  Store: after a reverted profile edit the newest evaluation, not the one for the current
  snapshot, was shown, and a pending evaluation for the abandoned snapshot still ran (the
  recommendation now comes from the current snapshot's evaluation when one is done; pending work
  for another snapshot or model config is superseded); jobs the active search profile rejects
  kept a recommendation and were listed (now the same pass-or-override rule as queueing).
  Validation: an eligibility fact cited for a skill counted as a citation, and an id too large for
  SQLite failed the whole evaluation (both are now dropped with a flag).
- **Adversarial review by a second model (Codex, P1.7)** found five more, each now a fixture or
  test: uncited model gaps could make a rule-2 SKIP (6.5, "A gap must be evidenced"); "Must
  reside outside California" read as met for a California resident (bare "outside" and "not
  located in" are exclusion wording); sponsorship wording that is neither an offer nor a
  refusal ("applicants who need sponsorship are ineligible", "please indicate whether you will
  require sponsorship") defaulted to met (now: "ineligible" / "must not need" fail, an offer needs
  the employer as subject, anything else is unanswered); one shared word ("public") matched an
  unrelated license (now one name must contain all the other's distinctive words); listings
  showed an older snapshot's recommendation with no label (rows now carry `current` and
  `newer_evaluation_pending`; the last result stays visible while a re-evaluation runs).
- **E3 prompt `m1-v1`** (`match.py`): ids are prefixed (R1.., P<item id>) so the model cannot cite a
  requirement as an item; the posting's located quote is sent, not the E1 paraphrase; the
  profile list excludes eligibility facts. Inferred claims (at most 5, deduplicated by statement)
  go to the inbox with the requirement ids they were proposed for. Pinned by a prompt hash test;
  schema lint covers this schema too.
- **E4** (`validate.py`): citations are inserted through `match_citations` inside a savepoint; an
  id outside the evaluated snapshot fails the FK, the match is validated again without it (flag
  `citation_rejected`), so an inferred claim or an invented id can never cover a requirement.
  direct needs a citation; transferable a citation and a bridge; a gap needs a reason, and a
  certification gap without a citation needs `cert_list_complete`. Years: dated items are merged
  as month intervals (start and end month both count; overlapping roles are not double-counted),
  undated items count their `years`; fewer than the minimum turns a model "direct" into a gap
  (`years_short_override`). basis: verified if any cited item is verified.
- **Reuse of a stored answer** (migration 0005, `store.prepare` / `stored_answer` / `claim_reusable`): each evaluation
  records `request_sha256`, the sha256 of the full E3 request body (system prompt, schema, user message, model id,
  parameters). A pending evaluation whose exact request was already answered by the same config is processed with
  that answer and no model call (`raw_response.reused_from`), also while inference is down. The E3 prompt carries no
  item status, eligibility fact or completeness flag, so a verify, a fact edit, a completeness change or an E4/E5
  version bump costs no GPU; editing an item's text, kind, years or dates changes the request and calls the model.
  Rejected: a manual replay command (must be remembered, does not help a verify). Measured on the 44 drill postings:
  one verify re-evaluated all 44 in 0.2 s with 0 model calls (about 17 min of Gemma time before).
- **E5/E6** (`assess.py`, thresholds `verdict_rules/v1.yaml`, recorded as `verdict_rule_version`):
  see 6.5, including the points 6.5 left open (listed there). Recommendation rows store every
  reason; `rank_key` is opportunity value only (ordinal x 10 + favorable - unfavorable), never a
  blend of the three assessments.
- **Worker** (`worker.py`): a step in the analysis worker's loop, sharing its health gate, so one
  model call runs at a time; evaluations are tried before analyses (a job half way through the
  pipeline finishes first). Outcomes and attempt accounting as for analysis; `evaluation_done`,
  `evaluation_failed`, `evaluations_queued`, `recommendation_computed` change events carry ids
  and counts. A `sync` failure is an error log (`evaluation_sync_failed`) and does not stop
  posting analysis.

Reconciliation: where model and cues agree, `confirmed`. Where they disagree,
the more permissive reading is used for fit (a possible preferred is not
treated as required) and the requirement is `conflict`. **A conflicting hard
requirement never blocks; it makes eligibility UNKNOWN.** Confirmed hard
requirements need: quote found, not negated, subject candidate, eligibility
type, required modality, no equivalence clause.

### 6.5 Assessment and recommendation rules (proposed v1, thresholds in config)

Counts use only requirements with `interpretation_status` confirmed or
conflict (unverifiable ones count only as uncertainty).

**Eligibility**
- BLOCKED: at least one confirmed hard requirement that a declared,
  verified eligibility fact definitively fails (e.g. requires US citizenship,
  profile says not a citizen; requires active clearance, profile says inactive
  with no "ability to obtain" alternative in the posting).
- UNKNOWN: any hard requirement (confirmed or conflict) the profile cannot
  answer, or that is answered only by a self-reported fact, or any
  conflicting hard requirement.
- ELIGIBLE: otherwise.

**Qualification fit** over negotiable requirements (preferred ones only
upgrade or break ties):
- INSUFFICIENT: fewer than 3 judgeable negotiable requirements, or unknown +
  unverifiable > 30% of extracted requirements, or conflicts > 3.
- STRONG: 0 material gaps, every covered requirement direct, unknown <= 1.
- COMPETITIVE: 0 material gaps, transferable allowed, unknown <= 2.
- STRETCH: 1-2 material gaps (`max_stretch_gaps`), the rest covered.
- WEAK: more than `max_stretch_gaps` material gaps on sufficient evidence.

**Opportunity value**: each factor scores favorable / neutral / unfavorable /
unknown (pay vs floor and target, work mode, location, seniority, role family,
age). LOW if 2+ unfavorable; HIGH if 3+ favorable and 0 unfavorable; UNKNOWN
if 4+ unknown; else MEDIUM. Factor list is stored and shown, not just the label.
An unknown factor has two causes, kept apart on screen: the posting does not state it (salary,
work mode, where it hires, date: "Not stated") or the search has no preference for it ("No
preference set"). The factor's `detail` carries the cause; `desktop/src/labels.ts` `factorText`
words it, and a test pins those strings to `assess.py`.

**Recommendation** (first matching rule wins; each recorded as the decisive reason):
1. Eligibility BLOCKED -> SKIP ("blocker: <quote>").
2. Fit WEAK -> SKIP ("N material gaps: ...").
3. Eligibility UNKNOWN or fit INSUFFICIENT -> REVIEW, with the specific
   questions that would resolve it.
4. Fit STRONG or COMPETITIVE -> APPLY.
5. Fit STRETCH and opportunity LOW -> SKIP ("stretch, low value: <factors>").
6. Fit STRETCH -> STRETCH.

Self-reported basis is never hidden: an APPLY carried by self-reported items
shows that, and the REVIEW-questions view suggests verifying them. Whether
LOW opportunity should also demote APPLY is Q15.

Noise control: the list view defaults to APPLY + STRETCH, ranked by
opportunity value; REVIEW appears as questions, not as a job list: counted in jobs ("8 jobs need your
input"), with questions several jobs share first and then each job's own questions, fewest first, each
answered in place (`pages/Answer.tsx`: an empty item form in your own words, or the fact's typed form)
(usability pass 2026-10-24: 45 question groups were 8 jobs, and only 1 group covered more than one job).
Exact copies (one employer posting the same job under several ids) show as one row,
"posted N times", and the job's detail links each open copy (`views.py` `_COPY_KEY`: employer,
title, location, and the description where it is kept; rejected postings keep no text and group on
title and location). The shown copy is the one with an application, else the oldest. Nothing is
merged in the database: each posting keeps its own link, closure and analysis. Rejected: merging at
ingestion (loses a real posting's link and closure) and matching similar text (Voxel's "Tier 2" and
"Tier 3 Support Engineer" share an intro but are different jobs).
A job you skip leaves the default views (Kevin, 2026-10-24: skipped jobs "sit in the list taking up
space and will probably get looked at more than once"). Skip, on a list row or the job's header, saves
your call SKIP (6.7) and keeps any details already given (`keep_details`). A job whose current label is
SKIP, with its open exact copies, is left out of "For you" and "Needs your input" and listed under
"Skipped by you"; Undo withdraws the label. JobForge's own
recommendation is unchanged, so the report still measures it. Rejected: a "not interested" application
status (a table rebuild for its CHECK, a non-application in the application history, and no label).
The golden-set report (6.7) tracks REVIEW share; if it exceeds the target
(Q16), thresholds get tuned in a new rule version, measured against labels,
not intuition.

**Points the rules above leave open, decided in P1.7** (each pinned by a layer-B case in
`tests/fixtures/verdict/layer_b.yaml`):
- *Recall rows* (D15, deferred from P1.6): a row whose sentence the code reads as negated ("No
  clearance required") is ignored; one met by a verified fact ("We do sponsor visas!" with
  sponsorship not needed) is fine; any other outcome makes eligibility UNKNOWN. A recall row never
  blocks, even when a verified fact fails it.
- *Hard requirement failed only by a self-reported fact*: UNKNOWN (the text names only "met by a
  self-reported fact"; a block needs a verified fact, so a self-reported fail cannot be stronger).
- *Fit, "unknown" and "extracted"*: unknown counts negotiable requirements only;
  `uncertain_share = (unknown + unverifiable) / (negotiable + unverifiable)`; preferred rows count
  neither way (they "only upgrade or break ties"). Conflicts count over negotiable and preferred
  rows (hard conflicts already make eligibility UNKNOWN).
- *Fit ladder order*: the three INSUFFICIENT checks first, then WEAK (> `max_stretch_gaps`
  material gaps), then STRETCH (1-2 material gaps and unknown <= `stretch_max_unknown` = 2, "the rest
  covered"), then STRONG, then COMPETITIVE. Not in the list: no material gaps but more unknowns
  than COMPETITIVE allows, or a stretch with more than 2 unknowns -> INSUFFICIENT (not enough to
  call), never a guess.
- *A gap must be evidenced to count* (D7, found by the Codex adversarial review): a model gap
  counts toward STRETCH/WEAK only if it cites profile items (the shortfall or the adjacent
  experience; years shortfalls always do), comes from a failed fact check, or is a certification
  absent from a cert list Kevin marked complete (`absence_confirmed`). A model that says
  "candidate lacks it" and cites nothing is missing evidence: counted as unknown for fit, listed
  as `gap_unevidenced` in `fit_counts`. Before this, three such lines made a rule-2 SKIP.
- *Preferred upgrade*: no upgrade rule is defined, so v1 applies none; preferred counts are
  stored in `fit_counts` and shown.
- *Opportunity factors* read the job through the filter's own code under the active profile
  (`rules.evaluate`), so they never wait for a re-filter: pay (no salary -> unknown; top under
  the floor -> unfavorable; from at or above `opportunity.pay_target_annual` -> favorable; else
  neutral), work mode (`opportunity.work_modes` map), location (a failed location rule ->
  unfavorable; HOME_OK or US_WIDE -> favorable; else unknown), seniority and role family
  (`opportunity.seniority` / `role_families` favorable and unfavorable title patterns; none
  configured -> unknown), age (<= 7 days favorable, > 30 unfavorable, no date unknown). The
  `opportunity` section of the search profile is optional and validated at save (Q8 data).

**Measured in the P1.7 live run** (`docs/evidence/p1_7_evaluation_live.txt`): see the evidence
file for the distribution. Every Vultr posting's state-list residency rule is an E2 `conflict`
only because Gemma typed it `work_authorization`; under the rule "any conflicting hard
requirement -> UNKNOWN" such a job is REVIEW whatever the profile says, a question no profile
answer resolves. Left as written (it is Kevin's rule); the evidence file counts how many
REVIEWs come only from such type-only conflicts (DEFERRED_COMPLEXITY.md).
Rules **v2** (`verdict_rules/v2.yaml`, **active since 2026-10-23**, Kevin's decision after the measurement; v1 kept for comparison) checks a type-only
conflict under both readings, the code's type and the model's, through the evaluated snapshot's facts
(`store.write_recommendation` passes `alt_check`); it is answered only when verified facts meet both (a
model type of `none` adds no condition), and still never blocks. Compare with
`scripts/compare_verdict_rules.py`.

### 6.6 Tests for interpretation and verdicts

Three layers, so the code logic is pinned even when no model is running.

**A. Interpreter unit tests (pytest, no LLM).** Table-driven fixtures in
`agent/src/jobforge_agent/analysis/fixtures/layer_a.yaml` (in `tests/fixtures/interpretation/` until P1.9), each a posting snippet with a heading, the
model reading E1 would plausibly produce, and the expected E2 output.
Minimum Phase 1 set:

| Area | Snippet | Expected |
|---|---|---|
| Negation | "No prior cloud experience required." | negated |
| Negation | "A degree is not required." | negated |
| Negation | "You don't need Kubernetes on day one; we'll train you." | negated |
| Negation (mixed) | "Clearance not required, but must be able to obtain a Secret clearance." | not hard; negotiable eligibility, matched to declared clearance fact |
| Importance | "Required: CCNA" vs "CCNA a plus" vs "CCNA or equivalent experience" | negotiable / preferred / negotiable with equivalence |
| Importance (conflict) | "Must have AWS" inside a "Nice to have" section | conflict, treated as preferred for fit |
| Importance | "Bachelor's degree or 4 additional years of experience" | negotiable, equivalence |
| Years | "5 years with a degree or 7 without" | negotiable, both branches recorded |
| Subject | "Our customers include Fortune 500 banks." | context |
| Subject | "Our stack: Terraform, Ansible, Datadog" (About the team) | context |
| Subject | "You'll mentor engineers with 10+ years of experience." | context (years not about the candidate) |
| Subject | "We pay for AWS certifications." (Benefits) | context |
| EEO | "...regardless of race, religion, veteran status..." | context, never a requirement |
| Eligibility | "Must be a US citizen." | hard, work_authorization |
| Eligibility | "Active TS/SCI required" vs "ability to obtain Secret" | hard clearance_held vs negotiable |
| Eligibility | "Must reside in Northern California" (scanner log) | hard, residency |
| Eligibility | "We are unable to sponsor visas." | hard only when profile needs sponsorship, else irrelevant |
| Quote | Quote not present in text | unverifiable, excluded |
| Quote | Quote appears in Requirements and Benefits with different meaning | ambiguous_location |

**B. Verdict unit tests (pytest, no LLM).** Table-driven assessments in
`tests/fixtures/verdict/`: synthetic requirement + match sets with expected
eligibility, fit, opportunity and recommendation. Must include:
- Confirmed hard blocker failed by a verified fact -> SKIP.
- Same blocker, quote not found -> not SKIP (REVIEW or other).
- Same blocker, interpretation conflict -> REVIEW.
- Hard requirement, profile silent -> REVIEW, with a review question.
- Hard requirement met only by a self-reported fact -> REVIEW.
- All required covered by transferable -> APPLY (COMPETITIVE).
- Requirement "covered" only by an inferred claim -> citation insert fails the
  `match_citations` FK; requirement stays unknown.
- Transferable with empty bridge -> unknown.
- 1 material gap + MEDIUM opportunity -> STRETCH; + LOW -> SKIP.
- Years short by 1 of 5 -> minor gap; short by 4 of 5 -> material.
- 3 material gaps on sufficient evidence -> SKIP; same with 40% unknown -> REVIEW.
- Property test: for randomly generated inputs, SKIP only occurs via rules 1, 2 or 5.
- Recompute with a new rule version issues zero inference calls (fake client
  asserts not called).

**C. Model interpretation report (needs inference, not a pass/fail test).**
`jobforge-agent golden --suite interpretation` runs E1 on the layer-A snippets
with a chosen model config and reports, per area, how often the model's raw
reading agreed with the expected reading and how often E2 caught a
disagreement. Used to choose models and prompt versions.

### 6.7 Golden set

20-30 real postings Kevin labels: recommendation, eligibility, and any
requirement he thinks is misread (Q12). Report per model/prompt/rule version:
- **False SKIP**: labeled APPLY/STRETCH, system SKIP (the costly error).
- **False APPLY**: labeled SKIP, system APPLY/STRETCH (the noise error).
- **REVIEW share** and how many REVIEWs were labeled as obvious.
- Confusion table across the four categories.

**Provenance rule (Kevin, Q27, 2026-10-21).** Labels written by anyone other
than Kevin, including the P1.0b spike reference in `spikes/p1_0/reference.py`,
are provisional: usable for testing and model comparison, never golden.
A label of SKIP also hides the job from the default views (Noise control, 6.5); the
label is still the measurement and the recommendation is not changed by it.
Before any of those items enters the golden set, every item a model got
"wrong" against them gets an independent review: the reviewer sees the
posting text, the reference label and the model's output side by side, did
not author the label, and records one of *label correct / model correct /
ambiguous*. Kevin confirms each item before it is promoted. Each golden row
stores who labeled it, who reviewed it and when.

A new default (model, prompt, or rule version) must not raise false SKIP on
the set. Small-set caveat: thresholds tuned on 30 postings can overfit; the
set grows as Kevin labels real results in the UI. Labels live in the user's
database (exportable), never in the repo.

### 6.8 Inference integration

- **One client, many configs.** One OpenAI-compatible client
  (`/v1/chat/completions`, `/v1/models`); providers are `model_configs` rows.
  LM Studio default `http://127.0.0.1:1234/v1`, Ollama `http://127.0.0.1:11434/v1`.
  No plugin system, no LangChain.
- **Structured output.** JSON schema mode where supported (LM Studio
  `response_format: json_schema`); per-config `structured_output_mode`
  fallback; Pydantic validation always. Temperature 0 and a fixed seed where
  the server honors it; raw responses stored regardless.
- **Schema lint (from spike P1.0b).** No enum value may appear in two fields
  of an output schema: sharing `"required"` between `importance` and
  `polarity` made three different models label ~95% of requirements
  `negated`. A unit test walks every output schema and fails on shared enum
  values. Prompt rule from the same spike: quote the bullet text only, never a
  list heading joined to it.
- **Reasoning control is per model config** (`reasoning_args` in
  `model_configs.params`): Phase 1 default is reasoning off
  (`reasoning_effort: none`, or `low` where `none` is not supported). Spike:
  Gemma's thinking exhausted a 12k budget with an empty answer; Qwen's was
  2.5x slower and not more accurate; gpt-oss's did not engage under a JSON
  schema. Every call sets `max_tokens`, and `finish_reason = length` is
  recorded as `failed: budget_exhausted`, never parsed.
- **Implemented in P1.6** (`inference/`): `GET /v1/model-configs`, `POST
  /v1/model-configs/{id}/test` (lists `/v1/models`, runs E1 + E2 on a fixed synthetic snippet;
  passes only if the model is listed, `finish_reason = stop`, output schema-valid and every
  quote found; only a pass sets `tested_at_ms`), `POST .../enable` (409 before a passing test)
  and `.../disable`. The model call runs with no transaction open. Context overflow measured
  on LM Studio with Gemma loaded at 16,384: an 18,216-token prompt got HTTP 400
  `exceed_context_size_error` in 0.3 s, no silent truncation; the client maps it to
  `failed: context_overflow`. The agent does not check which context LM Studio actually loaded
  a model with (DEFERRED_COMPLEXITY.md).
- **Model availability is checked, not assumed.** A model listed by
  `/v1/models` may still fail to load (spike: qwen3.5-9b-tuned, runtime
  incompatibility). The "test this model config" action loads it and runs one
  snippet before a config can be enabled.
- **Context length is part of the config.** LM Studio's loaded context can be
  shorter than a posting plus prompt (P1.0b loads models at 16k explicitly).
  The agent sends `max_tokens`, records `usage`, and marks an evaluation
  `failed: context_overflow` instead of storing a truncated reading.
- **Two model calls per job** (E1 once per posting version, E3 once per
  profile snapshot). Rejected: one combined call (harder for 7-14B models,
  and every profile edit would redo extraction).
- **Hardware fit.** 12 GB VRAM holds ~12-14B at 4-bit with moderate context
  (gemma-4-12b-qat loaded at 16k used 6.66 GiB, Appendix A). Pick from
  bakeoff and P1.0b evidence.
- **Unavailable server.** Health probe gates claims; UI and tray show
  "evaluations paused"; recommendations still recompute (code only); per-call
  timeout 120 s; resumption is automatic (3.4).

---

## 7. Packaging, installation and updates

### 7.1 Is Tauri + a Python sidecar the simplest reliable option?

| Option | What you build | For | Against |
|---|---|---|---|
| **A. Tauri 2 host + PyInstaller onedir agent (recommended)** | Rust host (tray, window, supervisor, ~400-600 lines), React UI, Python agent | Tray, autostart, single-instance, NSIS per-user installer, signed updater, WebView2 bootstrapper all come as maintained official plugins/bundler features. Agent is a separate process, so its lifetime is independent of the UI. Business logic stays in Python next to the scanner code. | Two toolchains (Rust + MSVC build tools + Node, and Python). Rust is not installed on this machine today. Two build steps glued by `build.ps1`. |
| B. Python only: pywebview window + pystray tray + PyInstaller + Inno Setup | One language | No Rust. In-process UI calls (no IPC). | pywebview wants the main thread for its GUI loop and pystray wants a loop too; reopening windows after the last one closes is awkward; UI and agent share one process (a webview crash kills discovery); installer, autostart, single-instance, updater all hand-rolled. More custom glue in exactly the areas the requirements stress. |
| C. Electron + Python | Node host | Mature tray/updater | ~150 MB runtime per install, a second JS runtime for no benefit over Tauri. |
| D. Python agent with a localhost web UI opened in the browser (rev 3 minus Docker) | Python only | Simplest build, no desktop shell | Not a desktop app; browser-tab UX; the CSRF/DNS-rebinding surface returns. Rejected by the product decision. |

**Within A, how to package the agent:**

| Choice | Verdict |
|---|---|
| **PyInstaller `--onedir`, console subsystem** | Chosen, proven in spike P1.0d. Starts fast, no temp extraction, works with FastAPI/uvicorn. Shipped as a Tauri bundle resource folder and launched by path from Rust (not via `externalBin`, which expects a single file). Three spike-proven rules: (1) build with `--console` and start the agent with `CREATE_NO_WINDOW`, stdout/stderr to `agent-console.log`, because the windowed bootloader reports startup failures in a **modal dialog** that blocks a background agent forever; (2) map the dist **directory** in `bundle.resources` (`"…/jobforge-agent/": "agent/"`), because a `**/*` glob flattened `_internal\` and the agent could not find `python314.dll`; (3) the build script checks the bundled layout (`agent\_internal\python3*.dll` present) before producing an installer. |
| PyInstaller `--onefile` | Rejected: unpacks to `%TEMP%\_MEIxxxx` on every start, leaves those folders behind when killed, and onefile executables trip antivirus heuristics more often. |
| Nuitka `--standalone` | Fallback. Compiled C, sometimes fewer AV false positives and faster startup, but long builds and a C compiler in the toolchain. Switch only if P1.0d shows a PyInstaller problem. |
| Embedded Python distribution + pip-installed wheels | Viable and transparent, but needs its own launcher and path setup; PyInstaller already solves that. |

Recommendation: A with PyInstaller onedir, Rust kept to the four jobs in
section 2. Spike P1.0d proves it before the decision is final (9).

### 7.2 Installation and updates

- **Installer:** Tauri NSIS bundle, `installMode: currentUser` (no admin),
  installs to `%LOCALAPPDATA%\JobForge\` (P1.2 build: 16.7 MB installer, 43.3 MB
  installed, 98 agent files, 2.2-2.5 s silent install; spike: 16.4 MiB), WebView2 via the
  embedded bootstrapper if missing. Start-menu shortcut; autostart is a
  setting in the app (default off, Q24). **JobForge writes and deletes its own
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value** (`JobForge` =
  `"<install dir>\JobForge.exe" --background`), about 20 lines of Rust with
  the `winreg` crate. The autostart plugin is not used: in spike P1.0d it
  chose `HKLM` when the process was elevated (D16). The uninstaller removes
  the value.
- **Running agent during install/update/uninstall:** NSIS `PREINSTALL` /
  `PREUNINSTALL` hooks run `jobforge-agent.exe --shutdown` from the currently
  installed copy: it reads `ipc.json`, calls `/v1/shutdown`, and waits up to
  30 s for `agent.lock` to be released. If the agent does not stop, the hook
  terminates `jobforge-agent.exe` processes owned by the current user, and a
  `taskkill` backstop runs regardless, because NSIS `nsExec` has no timeout and
  the hook must not depend on the old agent being healthy. Spike: update with a
  running agent took 3.8 s with a graceful stop (`stop_reason = update`). The `PREINSTALL`
  hook then deletes `$INSTDIR\agent` (program files only) so no file from another version stays
  behind (P1.9 drill finding, see 4.4). Files
  are replaced only after the lock is free (Windows will not overwrite a
  running exe anyway; this makes that deterministic instead of a failed install).
  The host is closed by the installer's normal app-running check.
- **First start after update:** host spawns the new agent; agent backs up the
  DB, migrates (4.4), starts. Protocol-version check covers a stale agent (5).
- **Uninstall:** removes program files and the `Run` entry; leaves
  `%LOCALAPPDATA%\JobForge\` (data) unless the user ticks "also delete my data".
- **Updates in Phase 1:** manual (download and run the new installer).
  Tauri's updater plugin (signed manifests, needs a signing key and a release
  host such as GitHub Releases) is deferred, as is code signing (SmartScreen
  will warn on unsigned installers; Q22).
- **Rollback:** previous installer + restore pre-migration backup (4.4).
  Proven in spike P1.0d (older agent refused the newer DB; restore brought it
  back). Since P1.9 (v0.2.0) `--restore` first copies the live database to
  `backups\before-restore-v<N>-<time>.db` and refuses a backup newer than the agent (exit 4),
  so a rollback discards nothing for good; the restore screen (section 5) offers the backups
  older than the live data. The restore screen must say plainly that **everything recorded after
  the upgrade is discarded** (the spike lost the v0.2 run, as expected).
  Exporting new data before rollback is deferred (DEFERRED_COMPLEXITY.md).

---

## 8. Security and privacy

- **No network listener beyond loopback.** The agent's IPC socket binds
  `127.0.0.1` only, needs the per-start token (compared as bytes, constant time), and checks the Host header.
- **Model calls ignore proxy settings** (`trust_env=False`): an `HTTP_PROXY` or Windows system proxy would
  otherwise receive prompts carrying profile items and resume text, since httpx does not exempt loopback
  (audit 2026-10-24). Discovery keeps the system proxy: board requests go to the internet.
  LM Studio stays on localhost with LAN serving off (Appendix A: not reachable
  on Ethernet, Tailscale or VMware adapters).
- **Token handling.** Fresh per agent start, stored only in `run\ipc.json`
  (user-only DACL, verified at startup), never logged, never sent to the
  webview.
- **Webview hardening.** Strict CSP (no remote scripts, no `unsafe-eval`),
  Tauri capabilities limited to core window/event permissions and the app's own commands (`agent_call`, `agent_upload`,
  `open_url`, `supervisor_status`; no shell, fs, http or dialog plugins). Job
  descriptions are untrusted HTML from employers: rendered as sanitized HTML
  (DOMPurify, no scripts, no event handlers, links open in the system browser
  via an explicit allow-list) or as plain text. Implemented in P1.8 (`desktop/src/sanitize.ts`): allowed tags are
  formatting only; attributes `href`/`title`; no `style`, images, SVG/MathML, iframes or forms; hrefs must be http(s);
  Greenhouse's entity-escaped HTML is decoded once **before** sanitizing. Link clicks are intercepted and go to the host's
  `open_url`; the webview never navigates. Destructive actions use an inline two-step button (the window has no dialog
  plugin). A test posting containing
  `<script>` and `onerror=` must render inert.
- **Verified-experience protection** via authorizer, CHECK, FK and append-only
  triggers (4.3), with the honest limit stated there.
- **Personal data out of logs.** Ids, counts, hashes, durations, status codes
  only. Never resume text, experience statements, eligibility facts, prompts,
  model responses or the IPC token. Redaction covered by a test.
- **Eligibility facts** (citizenship, clearance) live in `experience_items`, their
  `profile_snapshot_items` copies and the evaluation rows that check them
  (`requirement_matches.validation_flags` records the values compared; corrected in P1.7, the
  text said `experience_items` only). Never in posting analyses, change events or logs, and
  `scripts/verify_evaluation.py` prints outcomes, never the values.
- **Data at rest.** The database is a plain file in the user profile,
  protected by Windows account permissions, not encrypted. BitLocker or
  equivalent is Kevin's disk-level control; app-level encryption (SQLCipher)
  is deferred with a trigger.
- **Personal data out of git.** No data directories in the repo; examples
  only; golden labels in the user DB.
- **Source terms.** Sources disabled until a terms/robots review is recorded;
  RFC 9309; Crawl-delay honored; 401/403 stops polling; Retry-After at host
  level; hourly budgets.
- **Baseline deployment floor.** This is no longer a web deployment. The
  web-specific items (edge 404 probes, Cloudflare, robots.txt for the app)
  have no surface to apply to; the agent's API returns 404 for unknown paths
  regardless. `.env*` ignore and `git ls-files` secret checks still apply.
  Recorded in DEFERRED_COMPLEXITY.md.

---

## 9. Phase 1 plan with acceptance criteria

Goal: an installed JobForge discovers continuously within source limits with
its window closed, and a newly posted job reaches a recommendation with three
assessments and an evidence table that appears in the open window without a
reload, with the inference server optional.

| Step | Scope | Acceptance criteria (each names its raw signal) |
|---|---|---|
| **P1.0 Spikes** (no app code) | (a) done, moot; (b) **done** (Appendix A); (c) polling cost and terms review **done** (Appendix A); (d) packaging **done** (Appendix B). Spike reference labels provisional (6.7) | (b) per posting and model: valid JSON, quotes found x/y, latency, first manual read of polarity/subject errors. (c) per source: `terms_note`, `min_interval_s` recorded from the terms text. (d) installer runs without admin; closing the window leaves `jobforge-agent.exe` running (Task Manager / `Get-Process`); host crash (`Stop-Process` host) leaves agent running and a relaunch attaches (no second agent); tray Exit stops both; reinstall over a running agent succeeds (hook stopped it); Windows Defender scan of the install folder: result recorded; SmartScreen behavior on the unsigned installer: recorded; installed size recorded. |
| **P1.1 Agent core** | Paths, instance lock, SQLite connection factory (pragmas, authorizers), migration runner + 0001, backups, recovery, IPC server with token, logging | Second agent exits 3. Agent started elevated exits 5 (run mode and `--restore`); `--shutdown` works elevated. Kill -9 then restart acquires lock < 1 s. Pragmas read back WAL/FULL/foreign_keys=1. Authorizer tests (4.3) pass. Append-only trigger tests pass. Migration killed halfway -> old `user_version`, DB intact; pre-migration backup exists and passes integrity check; DB newer than agent -> exit 4. IPC: no token -> 401, wrong Host -> 400, `ipc.json` DACL lists only Kevin's SID (`icacls`). Nothing written under the install dir. |
| **P1.2 Desktop shell** | Tray (open, pause/resume, status, autostart toggle, exit), close-to-tray, attach/spawn, watchdog, event relay, single-instance | Close window -> agent PID unchanged, `source_fetches` rows keep arriving. Pause -> no new fetches for 15 min, survives agent restart; Resume -> fetches resume. Host started elevated -> `relaunched_unelevated` and the agent logs `"elevated": false`; enabling autostart from an elevated or a normal launch writes HKCU only, never HKLM. Autostart on -> HKCU `Run` value present; off -> absent; after sign-in, `agent_runs` row with heartbeat. Kill agent -> watchdog respawns, `agent_runs` shows crash + restart. Second app launch focuses the existing window. Exit -> both processes gone, lock released, `agent_runs.stop_reason = 'user_exit'`. |
| **P1.3 Discovery** | Ported adapters, ETag polling, change detection, scheduler (3.3) | Change-detection tests (rev 3 list) pass. Scheduler tests with fake server + injected clock: no overlap (two claimers, forced second in-flight row), Retry-After honored, backoff caps, dead/blocked, 100 overdue employers spread over one interval within budget, clock jumps (sleep, correction). Live: after 1 h, every enabled employer has `last_success_at_ms` within 2 intervals; `NOT_MODIFIED` share and bytes/hour per host printed. Sleep the PC 30 min -> on wake, no burst beyond budget (verify.sh histogram). |
| **P1.4 Filter + retention** | Ruleset, re-filter, evaluate-anyway | Scanner regression fixtures pass. Re-filter writes new results for all stored postings incl. past rejects; old rows intact. |
| **P1.5 Profile** | Resumes, experience items incl. eligibility facts, verify, completeness, inferred inbox | Resume round-trip sha256 identical. Only the verify route sets verified (authorizer + grep test). |
| **P1.6 Posting analysis (E0-E2)** | Segmenter, E1 prompt v1, interpreter | Layer A interpretation suite passes. Conflict and unverifiable cases stored with both readings. Analysis not rerun on profile edit. |
| **P1.7 Evaluation + recommendation (E3-E6)** | Match, validate, assess, recommend, recompute, resume-on-return | Layer B suite passes incl. SKIP property test and FK-rejected inferred citation. LM Studio stopped for 1 h: discovery freshness unchanged, evaluations pending; LM Studio started: queue drains with no restart (log `inference_available` then `evaluation_done`). Rule change -> recompute with zero inference calls. Long fake model call does not block a user action (> 1 s). |
| **P1.8 UI** | Discovery dashboard, live refresh, job list/detail, rejected view, REVIEW questions, profile, preferences, applications | Screenshots of dashboard and a real job detail. New posting appears in the open window without reload; host restart replays missed events from cursor. `<script>`/`onerror` posting renders inert. Application status change -> `application_events` row. No code path submits to an employer (grep: outbound HTTP only in `discovery/` and `inference/`). |
| **P1.8b Usability pass** (added 2026-10-23, Kevin) | Installed current build, first-run Setup, plain search-profile form, plain wording, less on screen | Fresh data dir: Setup opens by itself and shows no regex or JSON (screenshot). Form round trip keeps imported regex patterns exactly (`test_setup.py`). Installer layout check requires `v2.yaml` and `defaults.yaml`. |
| **P1.8c Profile from a resume** (added 2026-10-23, Kevin) | "Suggest items" on an uploaded resume; suggestions in the inbox; Setup step 4 uses it | Quotes not in the resume are dropped; year-only dates are not padded; accepting gives self-reported items with the resume's dates (`test_suggest.py`). Live on two real resumes with the default model: counts, timing, window run (`docs/evidence/p1_8c_resume_suggestions_live.txt`). |
| **P1.9 Release drill + evidence** (drill passed on run 2, 2026-10-23; interpretation and golden numbers wait for LM Studio and the hand labels) | Installer upgrade with migration, rollback, verify.sh, golden + interpretation reports | Install v0.1, use it, install v0.2 (with a migration) while the agent runs -> hook stops agent, pre-migration backup created, v0.2 agent runs. Reinstall v0.1 -> agent refuses (exit 4, tray message); restore backup -> v0.1 runs with pre-upgrade data. verify.sh prints every SPEC section 6 row. Golden report prints false SKIP, false APPLY, REVIEW share, confusion table. |
| **P1.10 Built-in company list** (2026-10-23) | No board links needed: `directory.json` watched by default, add by name, list switch | Fresh install: Setup step 1 done with no input; Status counts the list; a restart adds 0 rows; a stopped company stays stopped; typing a name finds its board or says why not; requests per host stay within budget |

Out of Phase 1: auto-updater, code signing, Windows Service mode, named-pipe
IPC, macOS/Linux, Workday and other sources, shared multi-user, notifications
or digest, LLM opportunity value, embeddings, resume tailoring, non-English
postings, DB encryption, retention pruning.

---

## 10. Unnecessary complexity and recommended simplifications

| Tempting | Recommendation | Why |
|---|---|---|
| Windows Service for the agent | Per-user process started by the host / sign-in | Services run in session 0 as another account, complicating per-user paths, tray interaction and installs (admin). The requirement is per-user. |
| UI reads SQLite directly for speed | Only the agent opens the DB | Removes cross-process locking and keeps enforcement in one place. Local IPC latency is negligible. |
| Business logic in Rust | Rust does tray, window, supervision, forwarding only | One place for rules, one test suite, reuse of the scanner. |
| Alembic | Numbered SQL files + `user_version` | See 4.4. |
| Separate DB files per component | One DB | Cross-table transactions (posting + event) need one file. |
| Named pipes now | Localhost HTTP + token, pipes deferred | 5. |
| Auto-updater now | Manual installer in Phase 1 | Needs signing keys and a release host; the update *safety* (stop agent, migrate, rollback) is built and tested regardless. |
| Redis / Celery / schedulers / brokers | SQLite tables + one asyncio scheduler | State in rows survives restarts. |
| Provider plugin framework / LangChain | One OpenAI-compatible client | Every local server speaks it. |
| LLM score or composite number | Three categorical assessments | Unchanged from rev 2. |
| Second LLM interpretation pass | Code cue checks | Unchanged from rev 2. |
| Adaptive intervals | Fixed interval + override + ETag | 304s already make frequent polling cheap. |

---

## 11. Key architectural risks and alternatives

| Risk | Impact | Mitigation | Alternative considered |
|---|---|---|---|
| PyInstaller agent flagged by antivirus / SmartScreen on unsigned installer | Install friction, quarantined agent | P1.0d measures it; onedir (not onefile); code signing when distributing (Q22) | Nuitka; signing from day one |
| Two toolchains (Rust + Python) raise build friction | Slower iteration | Rust kept thin; one `build.ps1`; UI developed against the agent in a browser dev mode | Python-only shell (7.1 B) |
| Agent orphaned and invisible after host crash | Discovery runs with no tray to stop it | Relaunch attaches; `--shutdown` CLI; installer hook; `agent_runs` history | Kill agent with host (loses the "survives UI" property) |
| SQLite single-writer contention | UI actions slowed by long writes | Short transactions; no transaction across awaits (tested); WAL readers | Postgres (rev 3) |
| Power loss mid-write | Lost recent commits | `synchronous=FULL`; WAL; daily and pre-migration backups | `NORMAL` (faster, can lose last commits) |
| Authorizer misconfigured (wrong allow-list) | Silent loss of the verified-experience guarantee | Allow-lists in one module; tests try every forbidden write per component; grep test on factory imports | Separate DB files per trust level (breaks cross-table transactions) |
| A migration rebuilds a table and drops its append-only triggers | Silent loss of history protection | Post-migration test asserts every append-only table has both triggers | — |
| Version skew between host and agent after update | Broken UI calls | Protocol version handshake; host replaces stale agent | — |
| Sleep/resume and clock jumps | Missed or doubled polls | Monotonic gap detection, completion-based rescheduling, injected-clock tests | — |
| Job-description HTML is hostile | XSS inside the app | Sanitizer, CSP, token never in JS | Plain text only (loses formatting) |
| 15-minute polling vs source terms | Blocked or a violation | Terms review per source before enabling; budgets; 403 stops | Daily batch |
| Rejects-included storage growth | Disk | ETag + material hash mean polling frequency does not grow rows | Drop rejects |
| Local model misreads requirements | Wrong verdicts | Quote, interpretation and citation checks; golden set | Larger model in RAM |
| Two pipelines hit the same hosts | Politeness, confusion | Staggered, budgets, explicit cutover (Q2, Q21) | — |

---

## 12. Questions requiring product-owner decisions

- ~~Q1 Lane and location~~ Waived 2026-10-21 (Kevin): lane not tracked; project stays at the top level.
- ~~Q2 Existing pipeline~~ 2026-10-21 default: run in parallel, explicit cutover later; JobForge never touches `job_lean`.
- ~~Q3 Code reuse~~ Resolved 2026-10-21: copy adapters, record origin commit `edd1528`; `job-scanner` untouched.
- ~~Q4 Inference server and model~~ Resolved 2026-10-21 (Kevin): Gemma 4 12B
  (gemma-4-12b-qat in LM Studio), reasoning disabled.
- ~~Q27 Spike reference labels~~ Resolved 2026-10-21 (Kevin): provisionally
  accepted for testing; the misses are reviewed independently before any
  golden-set use (6.7). Still open within that review: is "Location: Remote,
  US (West Coast)" (Huntress) a residency requirement or a shift statement?
- ~~Q5 Docker setup~~ Obsolete (rev 4: no Docker).
- ~~Q6 What counts as verified~~ 2026-10-21 default: Kevin's click in the UI only; optional evidence note, not required.
- ~~Q7 Resume formats~~ 2026-10-21 default: Markdown, plain text, DOCX and PDF (text extracted, original bytes kept), per A6.
- ~~Q8 Opportunity value factors~~ 2026-10-21 default: factors as listed in 6.2. Role families and seniority targets are entered as search-profile data in the UI; needed before P1.7 produces meaningful opportunity values, not a design question.
- ~~Q9 Verdict labels~~ Resolved: APPLY / STRETCH / REVIEW / SKIP.
- ~~Q10 Notifications~~ Resolved 2026-10-21: none in Phase 1.
- ~~Q11 Open source~~ 2026-10-21 default: no license chosen yet; personal data stays out of the repo regardless.
- ~~Q12 Golden set~~ 2026-10-21 default: 20-30 postings labeled by Kevin through the UI, under the 6.7 provenance rule; needed before the P1.9 report, does not block P1.1-P1.8.
- ~~Q13 RLS deferral~~ Obsolete (no Postgres; per-user DB file, 4.3).
- ~~Q14 Access from other devices~~ 2026-10-21 default: none (loopback IPC only).
- ~~Q15 Low-value APPLY~~ 2026-10-21 default: rank lower only; no demotion.
- ~~Q16 Volume budget~~ 2026-10-21 default: v1 thresholds as in 6.5; tuned from golden-set results and Kevin's feedback once real volume is visible.
- ~~Q17 Stretch threshold~~ 2026-10-21 default: up to 2 material gaps; years short by <= 2 or <= 40% is minor (verdict rules v1).
- ~~Q18 Hard-blocker list~~ Resolved 2026-10-21: contractual certifications (DoD 8570/8140) are negotiable, not hard.
- ~~Q19 Poll budget~~ 2026-10-21 default: 15 minutes for all three Phase 1 sources (terms review and 304 measurements support it).
- ~~Q20 Always-on~~ 2026-10-21 default: discovery runs while Kevin is signed in and the PC is awake.
- ~~Q21 Overlap with the existing scanner~~ 2026-10-21 default: JobForge's watchlist starts with the scanner's Greenhouse, Lever and Ashby employers; per-host budgets apply.
- ~~Q22 Code signing~~ Resolved 2026-10-21: unsigned while Kevin is the only user; signing before any distribution.
- ~~Q23 IPC transport~~ Resolved 2026-10-21: localhost HTTP + per-start token via the host.
- ~~Q24 Autostart default~~ 2026-10-21 default: off until Kevin turns it on.
- ~~Q25 Rust toolchain~~ Resolved 2026-10-21: installed (VS 2022 Build Tools
  C++, Rust 1.99.1); Tauri + PyInstaller proven in P1.0d.
- ~~Q28 Elevated launches~~ Resolved 2026-10-21 (Kevin): "Prefer explicit
  per-user behavior; refuse elevation if safe behavior cannot be guaranteed."
  Implemented as D16 (3.2, 7.2).
- ~~Q26 Window close behavior~~ 2026-10-21 default: close hides to tray, with a one-time notice.

---

## Appendix A. P1.0 spike results (2026-10-21)

Raw outputs: `spikes/p1_0/results/`. Scripts: `spikes/p1_0/*.py`.

**(a) Container -> LM Studio (rev 3 question, now moot).** Docker Desktop
29.7.2 (WSL2). `curl` from a container to `host.docker.internal:1234/v1/models`
returned HTTP 200 with LM Studio bound only to `127.0.0.1`. LM Studio was
not reachable on 192.168.4.20 (Ethernet), 100.105.197.67 (Tailscale), or the
two VMware adapters. Rev 4 has no containers; the agent calls 127.0.0.1 directly.

**(b) Model evaluation.** Run 2026-10-21 after Kevin asked for it. Code:
`spikes/p1_0/eval_spike.py`, `reference.py`, `report.py`; full report with
every miss: `spikes/p1_0/results/eval/REPORT.md`; raw outputs per model in
`spikes/p1_0/results/eval/<model>__<mode>/`.

*Material.* 6 representative postings the existing scanner kept on
2026-10-21 (GitLab U.S. Government Support Engineer, Axonius SkillBridge
Technical Support Engineer, Vultr Linux Systems Administrator, Huntress SOC
Analyst, Keeper Security IT Support Tier 2, Tailscale Windows Engineer), plus
the 22 interpretation snippets of 6.6 layer A. **Reference labels were written
by Claude from the posting text before any model output existed; they are not
the golden set** and should be reviewed by him. 32 probes (expected
requirements with expected labels), 26 traps (EEO, benefits, company facts,
privacy-notice text that must not become candidate requirements).

*Setup.* LM Studio, each model loaded at 16k context, temperature 0, seed 7,
JSON-schema structured output, prompt `e1-draft-2`. "fast" =
`reasoning_effort: none` (`low` for gpt-oss, which has no off switch);
"think" = model default with a 12k output budget. One run per configuration.

| Model | Mode | Valid JSON | Quotes verbatim | Probes found | Probes fully correct | importance | polarity | eligibility_type | Traps violated | Snippets ok | s / posting (median, max) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gemma-4-12b-qat (6.66 GiB, fits VRAM) | fast | 6/6 | 97/97 | 31/32 | **29/32** | 30/30 | 30/30 | 2/4 | 0/26 | **18/21** | 38, 51 |
| gpt-oss-20b | fast | 6/6 | 84/91 | 30/32 | 27/32 | 29/30 | 30/30 | 1/4 | 0/26 | 14/21 | **24, 40** |
| gpt-oss-20b | think | identical to fast: median 4 reasoning tokens, so reasoning did not engage under the JSON schema | | | | | | | | | 24, 39 |
| qwen3-30b-a3b (spills to RAM) | fast | 6/6 | 98/98 | 30/32 | 28/32 | 29/30 | 30/30 | 2/4 | 0/26 | 17/21 | 66, 193 |
| qwen3-30b-a3b | think | 6/6 | 94/94 | 30/32 | 26/32 | 28/30 | 30/30 | 2/4 | 0/26 | 16/21 | 163, 174 |
| gemma-4-12b-qat | think | 0/2 tried: all ~12k tokens spent in reasoning, empty answer, ~257 s each; stopped | | | | | | | | | 257 |
| qwen3.5-9b-tuned | both | does not load in the installed LM Studio runtime ("qwen35.rope.dimension_sections has wrong array length; expected 4, got 3") | | | | | | | | | — |

Decision-critical probes (hard blockers and the one negation):

| Probe | gemma fast | gpt-oss fast | qwen3-30b fast | qwen3-30b think |
|---|---|---|---|---|
| GitLab "must be a United States Citizen" | hard / work_authorization | hard / **clearance** | hard / work_authorization | hard / **clearance** |
| Axonius "Active-duty ... DoD SkillBridge" | **MISSED** | hard / work_authorization | hard / work_authorization | hard / none |
| Vultr "residing in the following states" | hard / **work_authorization** | hard / residency | hard / residency | hard / residency |
| Keeper "reside within reasonable commuting distance" | hard / **work_authorization** | hard / **work_authorization** | hard / **none** | hard / **work_authorization** |
| Keeper "lift up to 50 pounds" | hard / physical | **required** / none | **required** / none | hard / physical |
| Tailscale "Prior Go expertise is not a requirement" | negated | **MISSED** | **MISSED** | **MISSED** |

What the evidence says (n = 6 postings, one run each; differences of one to
three probes between models are not meaningful):

1. **Importance and polarity are reliable once a requirement is extracted**
   (28-30 of 30 everywhere). **`eligibility_type` is not** (1-2 of 4 on
   postings): residency read as work authorization, citizenship as clearance.
   The model's eligibility category cannot decide which declared fact a
   blocker is matched against. Design change: E2 derives the eligibility type
   from a code lexicon; the model's label is a hint (6.4).
2. **Recall of blockers is the main risk.** Gemma, otherwise best, missed the
   SkillBridge active-duty condition entirely, which would yield a wrong
   ELIGIBLE. Design change: a deterministic eligibility scan over every
   sentence of the posting; any eligibility-cue sentence not covered by an
   extracted requirement becomes an "unextracted eligibility statement" that
   makes eligibility UNKNOWN (REVIEW, never SKIP) (6.4). Also: no enum value
   fits "active-duty program participant"; add `program_status`.
3. **Snippet failures are the cases E2 was designed for:** every model read
   "must be able to obtain a Secret clearance" as hard, every model read "Must
   have AWS" in a Nice-to-have section as required, Gemma read "Our stack:
   Terraform, Ansible, Datadog" as three required skills. Rules 3-5 of the E2
   interpretation checks cover exactly these; the spike confirms they are
   needed, not optional.
4. **Zero trap violations** (EEO text naming citizenship and veteran status,
   benefits, E-Verify notices, a privacy notice listing "work eligibility").
5. **Quote fidelity:** Gemma and Qwen quoted verbatim 100%; gpt-oss joined a
   list heading to each bullet ("Hands-on experience configuring and
   supporting: Firewalls") on 7 of 91 items, which quote verification flags as
   unverifiable. Prompt rule added: quote the bullet, never its heading.
6. **Thinking modes do not pay here:** Gemma's exhausts any reasonable budget,
   Qwen's is 2.5x slower and not more accurate, gpt-oss's does not engage
   under a JSON schema. Default: reasoning off, set per model config (6.8).
7. **Schema design defect found and fixed during the spike (prompt draft 1).**
   The first schema used `"required"` both as an `importance` value and a
   `polarity` value. All three models then labeled ~95% of items `negated`
   ("Strong proficiency administering Linux systems" -> negated). Renaming the
   polarity values to `affirmative`/`negated` fixed it (gpt-oss: 3/3 correct
   on the probe snippet; also correct with no schema at all). Rule: no enum
   value may appear in two fields of a structured-output schema; pinned by a
   schema lint test. Draft-1 results are kept in `results/eval_draft1/` as
   the record.
8. **Latency is fine for the workload.** Even the slowest usable
   configuration (qwen3-30b fast, ~66 s median) handles tens of postings per
   day; Gemma at ~38 s and gpt-oss at ~24 s per posting leave headroom.

Phase 1 default, **accepted by Kevin 2026-10-21 (Q4)**: **gemma-4-12b-qat, reasoning off** (best
probe and snippet accuracy, 100% verbatim quotes, the only model that kept the
negation, fits in VRAM), with gpt-oss-20b as the faster alternative. Both
behind the E2 lexicon and eligibility scan, because neither model's
eligibility labels or blocker recall can be trusted alone. Re-measure on
the golden set before relying on it.

**(c) Polling cost, measured with 2 s spacing, one run.**

| Source / board | Jobs | Body (decompressed) | On the wire | Time | ETag | Repeat with `If-None-Match` |
|---|---|---|---|---|---|---|
| Greenhouse / tailscale | 59 | 794 KB | 267 KB (gzip) | 235 ms | yes | 304, 0 bytes |
| Greenhouse / databricks | 897 | 9.8 MB | 3.3 MB (gzip) | 315 ms | yes | 304, 0 bytes |
| Greenhouse / databricks, no `content=true` | 897 | 771 KB | 63 KB | 245 ms | yes | 304, 0 bytes |
| Lever / jumpcloud | 35 | 610 KB | 610 KB (no gzip despite request) | 1228 ms | yes | 304, 0 bytes |
| Lever / palantir | 304 | 5.9 MB | 5.9 MB (no gzip) | 840 ms | yes | 304, 0 bytes |
| Ashby / render | 37 | 738 KB | 64 KB (gzip) | 132 ms | yes | 304, 0 bytes |
| Ashby / openai | 812 | 14.2 MB | 1.5 MB (gzip) | 370 ms | yes | 304, 0 bytes |

Other observations:
- robots.txt: Greenhouse 200, disallows only `/embed/`; Lever 200, `Allow: /`
  with `Crawl-delay: 1`; Ashby returns 401 (allowed under RFC 9309, same as
  the scanner observed).
- Ashby: `Cache-Control: public, max-age=60`, served from Cloudflare cache.
  Greenhouse: `max-age=0, must-revalidate`.
- No rate-limit headers (`RateLimit-*`, `X-RateLimit-*`) on any response.
- Two requests seconds apart returned byte-identical bodies on all boards.
- Per-job timestamps: Greenhouse `updated_at` and `first_published`; Lever
  only `createdAt`; Ashby `publishedAt`.
- Not yet measured: how often ETags change over hours (which sets the real
  bytes/day).

**(c, cont.) Terms review, 2026-10-21.** Read: Greenhouse Job Board API docs
(docs.greenhouse.io/job-board.html), Lever postings API README
(github.com/lever/postings-api), Ashby public job posting API docs
(developers.ashbyhq.com/docs/public-job-posting-api).

| Source | What the docs say about the public read endpoint | Recorded config (seeded into `sources`) |
|---|---|---|
| Greenhouse | "Job Board data is publicly available, so authentication is not required for any GET endpoints." No rate limit, caching or polling guidance stated. robots.txt disallows only `/embed/`. | enabled; `default_interval_s` 900; `min_interval_s` 300; `max_requests_per_hour` 120; spacing 2 s; `conditional_requests` etag |
| Lever | Public, no key for GET; only POSTs are rate limited (2/s); docs note postings "may be scraped by third parties". robots.txt `Crawl-delay: 1`. No gzip. | enabled; 900; 300; 120; spacing 2 s (>= crawl delay); etag |
| Ashby | Published postings via a plain GET; intended for populating a careers page; no rate limit, caching or polling text. robots.txt 401 (allowed under RFC 9309). CDN `max-age=60`. | enabled; 900; 300 (well above the 60 s cache); 120; spacing 2 s; etag |

Residual risk, accepted: all three describe the API as powering an
employer's own careers site and none explicitly addresses third-party
readers; their general website terms of service were not reviewed. JobForge's
posture: single user, identifying User-Agent, robots.txt honored, conditional
requests (an unchanged board costs headers only), self-imposed per-host
budgets, polling stops on 401/403. Revisit before any distribution beyond
Kevin (DEFERRED_COMPLEXITY.md).

---

## Appendix B. P1.0d packaging spike results (2026-10-21)

Code: `spikes/p1_0d_packaging/` (minimal agent: FastAPI/uvicorn, file lock,
SQLite WAL, migration with backup and refuse-newer, token IPC; minimal Tauri
host: tray, close-to-tray, attach-or-spawn, watchdog, autostart,
single-instance; NSIS per-user installer with stop-agent hooks). Raw output:
`spikes/p1_0d_packaging/results_*.log`. Toolchain installed for this spike:
VS 2022 Build Tools (C++), Rust 1.99.1 (MSVC), Tauri CLI 2.12.4, PyInstaller
6.22.3, Python 3.14.5.

**Test conditions.** The Claude Code shell on this machine runs elevated (High
integrity). Every process under test was therefore launched through
`explorer.exe`, which starts it at Medium integrity, the way a user would. The
agent logs its own elevation; all accepted runs show `"elevated": false`.

### Behavior evidence (final run, unelevated)

| Requirement | Test | Raw signal |
|---|---|---|
| Per-user install, no admin | Silent `/S` install | exit 0 in 2.6-3.8 s; `%LOCALAPPDATA%\JobForgeSpike\`; 43 MB; 86 agent files |
| Agent survives closing the UI | `CloseMainWindow()` on the host | host still running; agent PID unchanged (44064); ticks 1 -> 4 after close |
| Agent survives host crash | `Stop-Process -Force` host | agent 44064 still answering; relaunched host logged `attached to running agent` (no second agent) |
| One agent per user | Start a second agent | exit code 3, log `already_running` |
| Restart recovery | `Stop-Process -Force` agent | watchdog respawn in 13.6 s; `agent_runs` run 1 `crash_recovered`; pause flag persisted across the restart |
| Pause discovery | IPC pause (same route as the tray item) | `paused: true`; ticks stayed at 4 over 12 s; resume -> ticks continue |
| Startup at sign-in | Plugin enable, then run the registered command line unelevated | `HKCU\...\Run\JobForgeSpike = ...\jobforge-spike-host.exe --background`; launch attached, no window; disable removed the value |
| Full exit | Second launch `--exit` (same function as tray Exit) | both processes gone in 6.8 s; `stop_reason = user_exit`; lock free |
| Update while agent runs | v0.2 installer over running v0.1 | PREINSTALL hook: graceful stop (`agent_stop reason=update`); installer 3.8 s; v0.2 agent: `pre_migrate_backup ... integrity ok`, `migrated to 2` |
| Rollback | v0.1 installer over v0.2, then `--restore` | v0.1 agent logged `db_newer_than_agent (2 > 1)` and refused; restore exit 0; v0.1 running on schema 1 with pre-upgrade history |
| Uninstall with agent running | Silent uninstall | PREUNINSTALL hook: graceful stop; program folder removed; data folder kept; no Run entries left |
| IPC security | No token / foreign Host / sockets / ACL | 401 / 400 / only `127.0.0.1:<port>` / `run\` ACL `KEVINSPC\kevin:(OI)(CI)(F)` only |
| Install dir untouched at runtime | Files changed after install | none |
| Antivirus | Defender custom scan of install dir and installers | "found no threats" (local build without Mark-of-the-Web; reputation checks and SmartScreen not exercised) |

### Defects the spike found (each fixed in the spike, each now a rule above)

1. **Windowed PyInstaller agent hung forever** on a modal "Failed to load Python DLL" dialog: invisible for a background process, so it never took its lock or wrote a log. Rule: console build + `CREATE_NO_WINDOW` + stdout/stderr to a log (7.1).
2. **Tauri resource glob flattened the PyInstaller folder** (`python314.dll` landed in `agent\` instead of `agent\_internal\`). Rule: map the directory; build script checks the layout (7.1).
3. **Watchdog piled up hung agents** (8 processes) because it respawned while the previous child was still alive. Rule: never spawn while the last child lives; read exit codes 3 and 4 (3.2).
4. **Every IPC data route returned 500**: FastAPI sync handlers run on a thread pool; the SQLite connection is loop-bound. Rule: `async def` handlers + route test (3.4).
5. **Graceful shutdown silently degraded to kill**: `Request` imported inside a function plus postponed annotations made FastAPI treat it as a query parameter (422). The installer's fallback killed the agent after 30 s and history recorded a crash. Rule: module-level imports + shutdown route test (3.4).
6. **Autostart wrote machine-wide (`HKLM`) when the host ran elevated.** Resolved by D16 (Kevin, Q28): JobForge writes `HKCU\...\Run` itself and drops the plugin; an elevated host relaunches unelevated or refuses; an elevated agent exits 5 (3.2, 7.2).
7. **Install directory = `%LOCALAPPDATA%\<productName>`**, colliding with the planned data root. Rule: data under `%LOCALAPPDATA%\<identifier>\` (4.1).
8. **Installer hooks depended on a healthy agent** (`nsExec` has no timeout). Rule: console agent + `taskkill` backstop (7.2).
9. **Rollback discards post-upgrade data.** Expected, but must be said in the UI (7.2).
10. **Spike build script:** Windows PowerShell 5.1 `Set-Content -Encoding utf8` writes a BOM that Tauri's JSON parser rejects. Rule: write config files as UTF-8 without BOM.

### Not tested by the spike

- Clicking the tray menu itself (its actions share code with the tested IPC routes and `--exit`, but the menu wiring was not clicked).
- A real sign-out/sign-in (the registered command line was executed directly instead).
- Sleep/resume, and graceful handling of `WM_QUERYENDSESSION` / logoff (not implemented in the spike agent).
- SmartScreen on a downloaded installer, code signing, Windows 10, other machines.
