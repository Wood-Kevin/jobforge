// Status (P1.8b): is JobForge working, and is anything wrong? A plain summary and a short list of real
// problems first; the per-source and per-company tables are under "Details". Refreshes on agent
// events and every 15 s (ages change without events).
import { type Supervisor } from "../agent";
import { api } from "../api";
import { Badge, Card, ErrorBox, age, useAction, useLive } from "../ui";

export default function Dashboard({ sup, onSetup }: { sup: Supervisor | null; onSetup?: () => void }) {
  const health = useLive(api.health, [], [], 15_000);
  const disc = useLive(api.discoveryStatus, [], ["posting_new", "job_closed", "discovery_paused", "discovery_resumed", "employer_enabled", "employer_disabled"], 15_000);
  const analysis = useLive(api.analysisStatus, [], ["analysis_done", "analysis_failed", "job_queued_for_analysis"], 15_000);
  const evaluation = useLive(api.evaluationStatus, [], ["evaluation_done", "evaluation_failed", "evaluations_queued"], 15_000);
  const recs = useLive(() => api.jobs("recommended", "", 500), [], ["recommendation_computed"]);
  const act = useAction();

  const h = health.data;
  const d = disc.data;
  const enabled = (d?.employers ?? []).filter((e) => e.enabled);
  const counts: Record<string, number> = {};
  for (const j of recs.data?.jobs ?? []) counts[j.recommendation!.recommendation] = (counts[j.recommendation!.recommendation] ?? 0) + 1;
  const gate = Object.values(evaluation.data?.inference ?? {})[0] ?? Object.values(analysis.data?.inference ?? {})[0];
  const waitingAi = (analysis.data?.counts.pending ?? 0) + (evaluation.data?.counts.pending ?? 0);
  const lastCheck = Math.min(...enabled.map((e) => e.last_success_age_s ?? Infinity));

  const problems: string[] = [];
  if (sup && sup.state !== "running") problems.push("JobForge's background service is not running.");
  if (h?.discovery_paused) problems.push("Checking for new jobs is paused.");
  if (gate && gate.up === false) problems.push(`The AI model isn't reachable (LM Studio closed or its server stopped). ${waitingAi} postings are waiting; they'll be read automatically when it's back.`);
  if ((evaluation.data?.pending_waiting_for_disabled_config ?? 0) + (analysis.data?.pending_waiting_for_disabled_config ?? 0) > 0)
    problems.push("The AI model is turned off, so postings aren't being read. Turn it on in Preferences.");
  for (const e of enabled) {
    if (e.status === "dead") problems.push(`${e.name}: job board not found. Check the link in Preferences.`);
    else if (e.status === "blocked") problems.push(`${e.name}: the job board refused access.`);
    else if ((e.consecutive_failures ?? 0) >= 3) problems.push(`${e.name}: the last ${e.consecutive_failures} checks failed (${e.last_error ?? e.last_outcome}).`);
  }
  for (const s of d?.sources ?? []) if (s.enabled && s.backoff_until_ms) problems.push(`${s.kind} asked JobForge to slow down; checks resume shortly.`);
  if (h?.last_maintenance_error) problems.push(`Daily backup failed: ${h.last_maintenance_error}`);

  return (
    <div className="page">
      <Card title="Status" actions={h && (
        <button disabled={act.busy} onClick={() => act.run(async () => { await (h.discovery_paused ? api.resumeDiscovery() : api.pauseDiscovery()); health.reload(); disc.reload(); })}>
          {h.discovery_paused ? "Resume checking for jobs" : "Pause checking for jobs"}
        </button>)}>
        <p className="lead">
          {enabled.length === 0 ? <>No companies are being watched yet. <a href="#" onClick={(e) => { e.preventDefault(); onSetup?.(); }}>Open setup</a></>
            : <>Watching <strong>{enabled.length}</strong> companies{isFinite(lastCheck) ? <>, last check {age(lastCheck)} ago</> : ""}.
              {" "}{waitingAi > 0 ? <>{waitingAi} postings are waiting to be read by the AI model.</> : "Every posting found so far has been read."}</>}
        </p>
        <div className="stats">
          {(["APPLY", "STRETCH", "REVIEW", "SKIP"] as const).map((c) => (
            <div key={c} className="stat"><Badge value={c} /><strong>{counts[c] ?? 0}</strong></div>))}
          <div className="stat"><span className="muted">open jobs found</span><strong>{d?.jobs_open ?? "-"}</strong></div>
        </div>
        <ErrorBox error={health.error ?? act.error} />
      </Card>

      <Card title={problems.length ? `Needs attention (${problems.length})` : "Needs attention"}>
        {problems.length === 0 ? <p className="ok-text">Nothing. Everything is working.</p>
          : <ul className="compact">{problems.map((p, i) => <li key={i}>{p}</li>)}</ul>}
      </Card>

      <details className="card section">
        <summary><h2>Details</h2></summary>
        <h3>Job boards, last hour</h3>
        <table>
          <thead><tr><th>Board</th><th>Checks</th><th>Unchanged</th><th>Requests used / allowed</th><th>Data</th></tr></thead>
          <tbody>{(d?.sources ?? []).filter((s) => s.enabled).map((s) => (
            <tr key={s.kind}><td>{s.kind}</td><td>{s.polls_last_hour}</td>
              <td>{s.polls_last_hour ? `${Math.round(100 * s.not_modified_last_hour / s.polls_last_hour)}%` : "-"}</td>
              <td>{s.requests_last_hour} / {s.max_requests_per_hour}</td><td>{(s.bytes_last_hour / 1e6).toFixed(1)} MB</td></tr>))}</tbody>
        </table>
        <h3>AI model queue</h3>
        <p className="muted">Postings: {fmt(analysis.data?.counts)} · Matches against your profile: {fmt(evaluation.data?.counts)}</p>
        <h3>Companies</h3>
        <table>
          <thead><tr><th>Company</th><th>Board</th><th>Last check</th><th>Next check</th><th /></tr></thead>
          <tbody>{enabled.map((e) => (
            <tr key={e.id}><td>{e.name}</td><td className="muted">{e.source}</td><td>{e.last_success_age_s === null ? "pending" : `${age(e.last_success_age_s)} ago`}</td>
              <td>{e.next_poll_in_s === null ? "-" : `in ${age(Math.max(0, e.next_poll_in_s))}`}</td>
              <td><button className="small" disabled={act.busy} onClick={() => act.run(async () => { await api.pollNow(e.id); disc.reload(); })}>Check now</button></td></tr>))}</tbody>
        </table>
        <p className="muted">{h && `Version ${h.version} · data ${h.data_dir}`}</p>
        <ErrorBox error={disc.error ?? analysis.error ?? evaluation.error} />
      </details>
    </div>
  );
}

function fmt(c: Record<string, number> | undefined): string {
  if (!c || Object.keys(c).length === 0) return "none yet";
  const words: Record<string, string> = { done: "done", pending: "waiting", running: "in progress", failed: "failed", superseded: "replaced" };
  return Object.entries(c).map(([k, v]) => `${v} ${words[k] ?? k}`).join(", ");
}
