#!/usr/bin/env bash
#
# verify.sh — evidence gatherer, not a judge
#
# PHILOSOPHY (read this before editing):
#   This script exists to make "the trace, not the claim" cheap enough to run
#   after every change, not just before launch. It gathers RAW evidence from
#   the live system and prints it for a human to read.
#
#   It deliberately does NOT print PASS/FAIL, green checkmarks, or a summary
#   line. A script that says "ALL CHECKS PASSED" is just another status
#   report, and the whole point of this workflow is that status reports are
#   not evidence. The operator reads the actual output. If reading the output
#   ever feels too slow, the fix is fewer/better probes, never a summary.
#
# USAGE (Git Bash on Windows, from the repo root):
#   bash verify.sh                       # runs every section
#   bash verify.sh ipc db golden         # runs only named sections
#   Sections: ipc process data db discovery filter analysis evaluation profile
#             window golden tests repo logs
#
# SETUP per project:
#   1. Fill in the CONFIG block from SPEC.md's verification plan.
#   2. Every row in the spec's verification table becomes a probe here.
#   3. Claude Code maintains this file; any change that alters behavior
#      updates the matching probe in the same session (docs move with code).
#
# JobForge is a Windows desktop app, not a hosted web app (SPEC D3): the "live
# system" is the running agent on this PC, its IPC port on 127.0.0.1, and its
# SQLite database under the user's data root. The web deployment floor
# (Cloudflare, /wp-admin and /.env probes, robots.txt) has nothing to probe
# here; the IPC section probes the equivalent exposure (token, Host header,
# loopback-only listener). Every database probe opens the file read-only.
#
# Secrets: this script reads connection strings from the environment,
# never from hardcoded values. It belongs in the repo; its inputs do not.
# The IPC token is read at run time from the agent's own user-only ipc.json.

set -uo pipefail   # note: no -e on purpose; a failing probe is EVIDENCE,
                   # and we want to see it, not exit before the next probe

# ─── CONFIG ──────────────────────────────────────────────────────────────────
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Data root: VERIFY_DATA_DIR, else the agent's own JOBFORGE_DATA_DIR override, else the default.
DATA="${VERIFY_DATA_DIR:-${JOBFORGE_DATA_DIR:-$LOCALAPPDATA/dev.kevin.jobforge}}"
DB="$DATA/data/jobforge.db"
INSTALL="${VERIFY_INSTALL_DIR:-$LOCALAPPDATA/JobForge}"
PY="$ROOT/agent/.venv/Scripts/python.exe"          # the agent's venv (uv sync in agent/)
SCANNER="${VERIFY_SCANNER_DIR:-$ROOT/../job-scanner}"   # optional: filter port vs scanner

section() {
  printf '\n════════════════════════════════════════════════════════════\n'
  printf '  %s\n' "$1"
  printf '════════════════════════════════════════════════════════════\n'
}

probe() {
  # probe "label" cmd args...
  # Prints the label, the exact command (IPC token masked: output gets pasted into evidence
  # files), then the raw output.
  local label="$1"; shift
  local shown="$*"
  [[ -n "${TOKEN:-}" ]] && shown="${shown//$TOKEN/<token>}"
  printf '\n── %s\n' "$label"
  printf '   $ %s\n' "$shown"
  "$@" 2>&1
}

# Read-only SQL against the live database; prints rows as JSON lines.
sql() {
  "$PY" -c 'import sqlite3, sys, json
c = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
cur = c.execute(sys.argv[2])
print([d[0] for d in cur.description] if cur.description else "(no columns)")
for r in cur.fetchall(): print(json.dumps(r))' "$DB" "$1"
}

ps1() { powershell.exe -NoProfile -Command "$1"; }

# IPC: port and token from the agent's ipc.json (user-only DACL), never from this repo.
ipc_field() { "$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$DATA/run/ipc.json" "$1" 2>/dev/null; }
PORT="$(ipc_field port || true)"
TOKEN="$(ipc_field token || true)"
api() { curl -sS --max-time 10 -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$PORT$1"; }

# $1 is the section being tested; the remaining args are the operator's
# selections. Shift before matching — testing $1 against a list that still
# contains $1 made the filter a tautology (every section always ran).
want() { local name="$1"; shift; [[ $# -eq 0 ]] || [[ " $* " == *" $name "* ]]; }
SECTIONS=("$@")
run() { want "$1" "${SECTIONS[@]:-}"; }

printf 'data root: %s\ndatabase:  %s\ninstall:   %s\nagent ipc: port=%s token=%s\n' \
  "$DATA" "$DB" "$INSTALL" "${PORT:-<no ipc.json>}" "$([[ -n "$TOKEN" ]] && echo '<read from ipc.json>' || echo '<none>')"
[[ -f "$DB" ]] || echo "No database at $DB. That is itself a finding: nothing below can be read from the live system."
[[ -x "$PY" ]] || echo "No agent venv python at $PY (run 'uv sync' in agent/). SQL probes will fail."

# ─── IPC: the exposure surface (the desktop counterpart of the edge floor) ──
if run ipc; then
  section "IPC — agent up, token and Host checks, loopback only, ipc.json ACL"
  if [[ -z "$PORT" ]]; then
    echo "No ipc.json under $DATA/run. That is itself a finding: the agent is not running"
    echo "(or this is the wrong data root). Start JobForge and rerun."
  else
    probe "Agent is up (expect 200 JSON: version, protocol_version, schema, pid, elevated)" \
      bash -c "curl -sS -i --max-time 10 -H 'Authorization: Bearer $TOKEN' http://127.0.0.1:$PORT/v1/health | head -20"
    probe "Missing token (expect 401)" \
      curl -sS -o /dev/null -w 'HTTP %{http_code}\n' --max-time 10 "http://127.0.0.1:$PORT/v1/health"
    probe "Foreign Host header with the token (expect 400)" \
      curl -sS -o /dev/null -w 'HTTP %{http_code}\n' --max-time 10 -H "Authorization: Bearer $TOKEN" -H 'Host: evil.example' "http://127.0.0.1:$PORT/v1/health"
    AGENT_PID="$(api /v1/health | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["pid"])' 2>/dev/null)"
    probe "Listening sockets of agent pid $AGENT_PID (expect only 127.0.0.1:$PORT)" \
      ps1 "Get-NetTCPConnection -OwningProcess $AGENT_PID -State Listen -ErrorAction SilentlyContinue | Format-Table LocalAddress,LocalPort -AutoSize | Out-String"
  fi
  probe "run\\ ACL (expect only your SID; SYSTEM/Administrators printed as-is if inherited)" \
    icacls "$(cygpath -w "$DATA/run" 2>/dev/null || echo "$DATA/run")"
fi

# ─── PROCESS: lifecycle, elevation, autostart ───────────────────────────────
if run process; then
  section "PROCESS — one agent, run history, elevation, autostart"
  probe "jobforge-agent and JobForge processes (expect at most one agent)" \
    ps1 "Get-Process jobforge-agent,JobForge -ErrorAction SilentlyContinue | Format-Table Id,ProcessName,StartTime,Path -AutoSize | Out-String -Width 200"
  probe "agent_runs, last 10 (start/stop, stop_reason, elevated)" \
    sql "SELECT id, version, pid, elevated, datetime(started_at_ms/1000,'unixepoch','localtime') AS started,
                datetime(stopped_at_ms/1000,'unixepoch','localtime') AS stopped, stop_reason
           FROM agent_runs ORDER BY id DESC LIMIT 10"
  probe "Overlapping agent runs (expect zero rows)" \
    sql "SELECT a.id, b.id FROM agent_runs a JOIN agent_runs b ON a.id < b.id
          WHERE b.started_at_ms < COALESCE(a.stopped_at_ms, a.heartbeat_at_ms)"
  probe "agent_start lines: elevated flag on every start (expect false)" \
    bash -c "grep '\"agent_start\"' '$DATA/logs/agent.jsonl' | tail -10 | grep -oE '\"version\": \"[^\"]+\"|\"schema\": [0-9]+|\"elevated\": (true|false)|\"timestamp\": \"[^\"]+\"' | paste - - - -"
  probe "Refusals and relaunches in the logs (refused_elevated, relaunched_unelevated, not spawning another)" \
    bash -c "grep -hE 'refused_elevated|relaunched_unelevated|not spawning another|exited with code' '$DATA/logs/agent.jsonl' '$DATA/logs/host.log' 2>/dev/null | tail -15 || echo '(none)'"
  probe "Autostart: HKCU and HKLM Run values (JobForge present only in HKCU, only when the setting is on)" \
    ps1 "foreach (\$k in 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run','HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run') { \"\$k -> \" + ((Get-ItemProperty \$k -ErrorAction SilentlyContinue).JobForge) }"
  probe "Bootloader output (agent-console.log tail; empty when healthy)" \
    bash -c "tail -20 '$DATA/logs/agent-console.log' 2>/dev/null; echo '(end, $(wc -c < "$DATA/logs/agent-console.log" 2>/dev/null || echo 0) bytes)'"
  probe "Paused state and fetches since the last pause change" \
    sql "SELECT (SELECT value FROM settings WHERE key='discovery_paused') AS paused,
                (SELECT max(created_at_ms) FROM change_events WHERE type IN ('discovery_paused','discovery_resumed')) AS last_change_ms,
                (SELECT count(*) FROM source_fetches WHERE started_at_ms >
                    COALESCE((SELECT max(created_at_ms) FROM change_events WHERE type = 'discovery_paused'), 0)) AS fetches_since_last_pause"
  cat <<'MANUAL'

── Manual rows (need a person at the keyboard; the output is what you observe):
   - Survives window close: close the window, rerun 'bash verify.sh process discovery';
     same agent pid, source_fetches started after the close.
   - Elevated agent refuses: from an elevated shell run
     "$LOCALAPPDATA\JobForge\agent\jobforge-agent.exe"; exit code 5, log line refused_elevated.
   - Elevated host relaunches unelevated: start JobForge.exe elevated; host.log relaunched_unelevated.
   - Different-user elevation: "Run as different user" with another admin; message, no agent, no files.
MANUAL
fi

# ─── DATA: per-user dirs only ───────────────────────────────────────────────
if run data; then
  section "DATA — program files unchanged since install, data under the data root"
  # Installed files keep their build time; uninstall.exe is written by the installer itself, so its
  # time is the install time.
  probe "Install dir: files written after the installer finished (expect none)" \
    ps1 "\$i = Get-Item '$(cygpath -w "$INSTALL" 2>/dev/null)\\uninstall.exe' -ErrorAction SilentlyContinue; if (\$i) { \"installed \$(\$i.LastWriteTime)\"; Get-ChildItem '$(cygpath -w "$INSTALL" 2>/dev/null)' -Recurse -File | Where-Object { \$_.LastWriteTime -gt \$i.LastWriteTime.AddSeconds(5) } | Select-Object -First 20 FullName,LastWriteTime | Format-Table -AutoSize | Out-String -Width 220 } else { 'no install at $INSTALL' }"
  probe "Data root tree (top two levels, sizes)" \
    ps1 "Get-ChildItem '$(cygpath -w "$DATA" 2>/dev/null)' -Recurse -Depth 1 | Select-Object FullName,Length,LastWriteTime | Format-Table -AutoSize | Out-String -Width 220"
fi

# ─── DB: live state, not migration logs ─────────────────────────────────────
# SQLite has no RLS. Write enforcement lives in per-component authorizer
# allow-lists (agent/src/jobforge_agent/db/connect.py, GRANTS; SPEC D2/D4) and
# append-only triggers; this section shows the live database's side of both.
if run db; then
  section "DB — pragmas, integrity, triggers, backups"
  probe "Pragmas and integrity (expect user_version = SCHEMA_VERSION, wal, ok, no FK rows)" \
    "$PY" -c 'import sqlite3, sys
c = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
for p in ("user_version", "journal_mode", "integrity_check"): print(p, c.execute("PRAGMA " + p).fetchall())
print("foreign_key_check", c.execute("PRAGMA foreign_key_check").fetchall())' "$DB"
  probe "Schema version this code expects" \
    "$PY" -c 'import sys; sys.path.insert(0, sys.argv[1]); from jobforge_agent.db import migrate; print("SCHEMA_VERSION", migrate.SCHEMA_VERSION)' "$ROOT/agent/src"
  probe "Append-only triggers per protected table (expect an update and a delete trigger each)" \
    sql "SELECT tbl_name, group_concat(name, ', ') FROM sqlite_master WHERE type='trigger' GROUP BY tbl_name ORDER BY tbl_name"
  probe "Backups (daily <= 7, pre-migrate, before-restore; no -wal/-shm side files)" \
    ls -la "$DATA/backups"
fi

# ─── APP: behavior probes from SPEC.md's verification plan ──────────────────
if run discovery; then
  section "DISCOVERY — continuity, intervals, conditional requests, jitter, budget, recovery, change detection"
  probe "scripts/verify_discovery.py (every discovery row in SPEC 6)" "$PY" "$ROOT/scripts/verify_discovery.py" "$DB"
  [[ -n "$PORT" ]] && probe "Source errors as the agent reports them (compare with the SQL above)" \
    bash -c "curl -sS --max-time 10 -H 'Authorization: Bearer $TOKEN' http://127.0.0.1:$PORT/v1/discovery/status | '$PY' -c 'import json,sys; d=json.load(sys.stdin); print(\"paused\", d[\"paused\"], \"jobs_open\", d[\"jobs_open\"]); [print(e[\"id\"], e[\"name\"], e[\"status\"], e[\"consecutive_failures\"], e[\"last_error\"]) for e in d[\"employers\"] if e[\"status\"] != \"active\" or e[\"consecutive_failures\"]]; print(\"built_in\", d[\"built_in\"])'"
  probe "Built-in company list (P1.10): rows by origin, switch state, poll interval" \
    sql "SELECT origin, enabled, status, poll_interval_s, count(*) AS n FROM employers GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4"
  probe "The built-in-list switch (no row = on)" \
    sql "SELECT key, value FROM settings WHERE key = 'directory_watch'"
  probe "Shipped list (directory.json) and the last startup syncs (agent log)" \
    bash -c "'$PY' -c 'import sys; sys.path.insert(0, sys.argv[1]); from jobforge_agent.discovery import directory as d; doc = d.load(); print(\"version\", doc[\"version\"], \"crawls\", doc[\"crawls\"], \"entries\", len(doc[\"entries\"]), \"failed index queries\", doc[\"index_queries_failed\"])' '$ROOT/agent/src'; grep -h 'directory_sync' '$DATA/logs/agent.jsonl' 2>/dev/null | tail -3"
  probe "Requests per board host in the last hour vs its budget (the list shares it with your companies)" \
    sql "SELECT s.kind, s.max_requests_per_hour, count(f.id) AS fetches_last_hour FROM sources s LEFT JOIN employers e ON e.source_id = s.id LEFT JOIN source_fetches f ON f.employer_id = e.id AND f.started_at_ms > (strftime('%s','now') - 3600) * 1000 GROUP BY s.id"
fi

if run filter; then
  section "FILTER — coverage, history, analysis queue, scanner parity"
  probe "scripts/verify_filter.py" "$PY" "$ROOT/scripts/verify_filter.py" "$DB"
  probe "Posting text kept by verdict under the active profile (0010: kept = pass or override; reject and no profile = not kept)" \
    sql "SELECT coalesce(fr.verdict, 'no verdict') AS verdict, EXISTS (SELECT 1 FROM filter_overrides fo WHERE fo.job_id = j.id) AS override, (rp.description_text IS NOT NULL OR pt.raw_posting_id IS NOT NULL) AS text_kept, count(*) AS open_jobs, sum(length(pt.description_raw_z) + length(pt.description_text_z)) AS text_bytes FROM jobs j JOIN raw_postings rp ON rp.id = j.current_raw_posting_id LEFT JOIN posting_texts pt ON pt.raw_posting_id = rp.id LEFT JOIN filter_results fr ON fr.raw_posting_id = rp.id AND fr.search_profile_id = (SELECT id FROM search_profiles ORDER BY version DESC LIMIT 1) WHERE j.closed_at_ms IS NULL GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"
  probe "Boards with open jobs that have no verdict under the active profile: ETag cleared, minutes until due (a re-filter makes them due at once)" \
    sql "SELECT count(DISTINCT j.employer_id) AS boards, count(*) AS jobs, sum(ps.etag IS NULL) AS jobs_on_etag_cleared_boards, min((ps.next_poll_at_ms - CAST(strftime('%s','now') AS INTEGER) * 1000) / 60000) AS min_due_in_min, max((ps.next_poll_at_ms - CAST(strftime('%s','now') AS INTEGER) * 1000) / 60000) AS max_due_in_min FROM jobs j JOIN employer_poll_state ps ON ps.employer_id = j.employer_id WHERE j.closed_at_ms IS NULL AND NOT EXISTS (SELECT 1 FROM filter_results fr WHERE fr.raw_posting_id = j.current_raw_posting_id AND fr.search_profile_id = (SELECT id FROM search_profiles ORDER BY version DESC LIMIT 1))"
  probe "Same employer, title and location among open jobs (the list shows exact copies as one row; text is compared in views.py, not here)" \
    sql "SELECT coalesce(fr.verdict, 'no verdict') AS verdict, count(*) AS groups, sum(n) AS jobs, max(n) AS largest FROM (SELECT j.employer_id, lower(trim(j.title)) t, lower(trim(coalesce(j.location_text, ''))) l, min(j.current_raw_posting_id) rp, count(*) n FROM jobs j WHERE j.closed_at_ms IS NULL GROUP BY 1, 2, 3 HAVING n > 1) g LEFT JOIN filter_results fr ON fr.raw_posting_id = g.rp AND fr.search_profile_id = (SELECT id FROM search_profiles ORDER BY version DESC LIMIT 1) GROUP BY 1 ORDER BY 1"
  probe "Database size" sql "SELECT page_count * page_size AS bytes FROM pragma_page_count(), pragma_page_size()"
  if [[ -x "$SCANNER/.venv/Scripts/python.exe" ]]; then
    probe "Filter port vs job-scanner (scanner's interpreter)" "$SCANNER/.venv/Scripts/python.exe" "$ROOT/scripts/verify_filter.py" "$DB" --scanner "$SCANNER"
  else
    echo "No job-scanner venv at $SCANNER: the scanner-parity row was not gathered."
  fi
fi

if run analysis; then
  section "ANALYSIS — E0-E2 rows"
  probe "scripts/verify_analysis.py" "$PY" "$ROOT/scripts/verify_analysis.py" "$DB"
  probe "Jobs with more than one pending analysis (expect zero rows)" \
    sql "SELECT job_id, count(*) FROM posting_analyses WHERE status='pending' GROUP BY job_id HAVING count(*) > 1"
  probe "Conflict rows keep both readings (rows missing a model_* or cue_* reading; expect zero)" \
    sql "SELECT id FROM requirements WHERE interpretation_status='conflict' AND (model_polarity IS NULL OR cue_polarity IS NULL)"
  probe "Model failures from budget or context, 7 days, per config" \
    sql "SELECT model_config_id, status, last_error, count(*) FROM posting_analyses
          WHERE last_error LIKE '%budget_exhausted%' OR last_error LIKE '%context_overflow%' GROUP BY 1,2,3"
  probe "Enabled model configs never tested (expect zero rows; CHECK constraint)" \
    sql "SELECT id, name FROM model_configs WHERE enabled = 1 AND tested_at_ms IS NULL"
fi

if run evaluation; then
  section "EVALUATION — recommendations, SKIP evidence, citations, inference independence"
  probe "scripts/verify_evaluation.py (distribution, SKIP, citations, eligibility by code, inference-down windows)" \
    "$PY" "$ROOT/scripts/verify_evaluation.py" "$DATA"
fi

if run profile; then
  section "PROFILE — verification times, resume round trip, no profile content in logs"
  probe "Verified items without a verification time (expect zero rows; CHECK constraint)" \
    sql "SELECT id FROM experience_items WHERE status='verified' AND verified_at_ms IS NULL"
  if [[ -n "$PORT" ]]; then
    probe "Resume round trip: sha256 of the downloaded original vs the stored hash, per resume" \
      "$PY" -c 'import hashlib, json, sqlite3, sys, urllib.request
db, port, token = sys.argv[1:4]
c = sqlite3.connect("file:" + db + "?mode=ro", uri=True)
for rid, stored in c.execute("SELECT id, sha256 FROM resumes ORDER BY id"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/resumes/{rid}/original", headers={"Authorization": "Bearer " + token})
    got = hashlib.sha256(urllib.request.urlopen(req, timeout=10).read()).hexdigest()
    print(rid, "stored", stored[:16], "downloaded", got[:16], "same" if got == stored else "DIFFERENT")
print("(no resumes)") if not c.execute("SELECT 1 FROM resumes").fetchone() else None' "$DB" "$PORT" "$TOKEN"
  fi
  probe "Profile text found in agent.jsonl (counts only; the text itself is never printed)" \
    "$PY" -c 'import sqlite3, sys
c = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
log = open(sys.argv[2], encoding="utf-8", errors="replace").read()
probes = [r[0] for r in c.execute("SELECT statement FROM experience_items") if len(r[0]) >= 16]
for (text,) in c.execute("SELECT extracted_text FROM resumes WHERE extracted_text IS NOT NULL"):
    probes += [text[i:i + 40] for i in range(0, min(len(text), 4000), 400) if len(text[i:i + 40].strip()) >= 30]
print(len(probes), "probe strings;", sum(1 for p in probes if p in log), "found in", sys.argv[2])' "$DB" "$DATA/logs/agent.jsonl"
fi

if run window; then
  section "WINDOW — applications history, event cursor, live refresh"
  probe "One application per job; one event per status change" \
    sql "SELECT a.job_id, a.status, count(e.id) AS events FROM applications a LEFT JOIN application_events e ON e.application_id = a.id GROUP BY a.id ORDER BY a.id DESC LIMIT 20"
  probe "Host event cursor and stream lines (event_stream_connected after=N, first event id)" \
    bash -c "echo cursor: \$(cat '$DATA/host/event_cursor' 2>/dev/null || echo none); grep -hE 'event_stream_(connected|first_event)' '$DATA/logs/host.log' | tail -6"
  cat <<'MANUAL'

── Manual rows (screenshots): a new posting appears at the top of Jobs > Filtered out (most new postings) or For you without a
   reload (posting_new within 5 s of the fetch); a posting with <script>/onerror/javascript: renders inert.
   Automated parts run in the 'tests' section (vitest sanitizer, route contract, outbound scan).
MANUAL
fi

if run golden; then
  section "GOLDEN — your labels against JobForge (read only)"
  sql "SELECT 'current labels by your call (SKIP also hides the job from For you and Needs your input):' AS note"
  sql "SELECT g.recommendation, count(*) FROM golden_labels g WHERE g.state = 'active'
         AND g.id = (SELECT max(id) FROM golden_labels WHERE job_id = g.job_id) GROUP BY g.recommendation"
  probe "jobforge-agent golden" env JOBFORGE_DATA_DIR="$(cygpath -w "$DATA" 2>/dev/null || echo "$DATA")" "$PY" -m jobforge_agent golden
  if [[ "${VERIFY_INTERPRETATION:-}" == "1" ]]; then
    probe "jobforge-agent golden --suite interpretation (calls the default model)" \
      env JOBFORGE_DATA_DIR="$(cygpath -w "$DATA" 2>/dev/null || echo "$DATA")" "$PY" -m jobforge_agent golden --suite interpretation
  else
    echo "Interpretation report not run (it calls the model, ~2-5 min). Set VERIFY_INTERPRETATION=1 to include it."
  fi
fi

if run tests; then
  section "TESTS — suites named in SPEC 6 (pass/fail counts as the runners print them)"
  probe "Agent suite (all rows: verdict, interpretation, authorizer, lifecycle, migrations, route contract, outbound scan)" \
    bash -c "cd '$ROOT/agent' && uv run pytest -q 2>&1 | tail -15"
  probe "Desktop sanitizer (vitest)" bash -c "cd '$ROOT/desktop' && npx vitest run 2>&1 | tail -6"
  probe "Host (cargo test, via PowerShell)" \
    ps1 "Set-Location '$(cygpath -w "$ROOT/desktop/src-tauri")'; cargo test 2>&1 | Select-String 'test result|panicked|error' | Out-String"
fi

# ─── REPO: no secrets or personal data tracked ──────────────────────────────
if run repo; then
  section "REPO — secret and data hygiene (git ls-files, not eyeballing)"
  probe "Tracked files matching secret patterns (expect empty)" \
    bash -c "cd '$ROOT' && git ls-files | grep -iE '\.env|secret|credential|\.pem|\.key$' || echo '(no matches)'"
  probe "Tracked databases, backups or data paths (expect empty)" \
    bash -c "cd '$ROOT' && git ls-files | grep -iE '\.db$|\.sqlite|backups/|/data/|ipc\.json|agent\.jsonl|profile_draft|config\.yaml$|watchlist' || echo '(no matches)'"
fi

# ─── LOGS: recent reality ───────────────────────────────────────────────────
if run logs; then
  section "LOGS — last 50 lines of the agent and host logs"
  probe "agent.jsonl" tail -50 "$DATA/logs/agent.jsonl"
  probe "host.log" tail -50 "$DATA/logs/host.log"
fi

printf '\n(End of evidence. No verdict is printed on purpose. Read it.)\n'
