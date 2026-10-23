// Discovery dashboard (P1.8): agent, freshness per source and employer, errors and backoff, volume,
// queues, inference. Refreshes on agent events and every 10 s (ages change without events).
import { useState } from "react";
import { type Supervisor } from "../agent";
import { api } from "../api";
import { Badge, Card, ErrorBox, age, useAction, useLive } from "../ui";

export default function Dashboard({ sup }: { sup: Supervisor | null }) {
  const health = useLive(api.health, [], [], 10_000);
  const disc = useLive(api.discoveryStatus, [], ["posting_new", "posting_changed", "job_closed", "job_reopened", "discovery_paused", "discovery_resumed"], 10_000);
  const analysis = useLive(api.analysisStatus, [], ["analysis_done", "analysis_failed", "job_queued_for_analysis", "evaluate_anyway"], 10_000);
  const evaluation = useLive(api.evaluationStatus, [], ["evaluation_done", "evaluation_failed", "evaluations_queued"], 10_000);
  const filter = useLive(api.filterSummary, [], ["refilter_done", "search_profile_saved", "posting_new"]);
  const recs = useLive(() => api.jobs("recommended", "", 500), [], ["recommendation_computed"]);
  const act = useAction();
  const [showAll, setShowAll] = useState(false);

  const h = health.data;
  const d = disc.data;
  const counts: Record<string, number> = {};
  for (const j of recs.data?.jobs ?? []) counts[j.recommendation!.recommendation] = (counts[j.recommendation!.recommendation] ?? 0) + 1;
  const troubled = (d?.employers ?? []).filter((e) => e.enabled && ((e.consecutive_failures ?? 0) > 0 || e.status !== "active"
    || (e.last_success_age_s ?? Infinity) > 2 * e.interval_s));
  const gate = Object.values(evaluation.data?.inference ?? analysis.data?.inference ?? {})[0];

  return (
    <div className="page">
      <Card title="Agent" actions={h && (
        <button disabled={act.busy} onClick={() => act.run(async () => { await (h.discovery_paused ? api.resumeDiscovery() : api.pauseDiscovery()); health.reload(); disc.reload(); })}>
          {h.discovery_paused ? "Resume discovery" : "Pause discovery"}
        </button>)}>
        <p><span className={`dot ${sup?.state ?? "starting"}`} /> {sup?.state === "running" ? "Running" : sup?.state ?? "Connecting..."}
          {h && <span className="muted"> · v{h.version}, schema {h.schema}, pid {h.pid}, up {age(h.uptime_s)}</span>}</p>
        {h?.discovery_paused && <p className="warn">Discovery is paused.</p>}
        {h?.last_maintenance_error && <p className="warn">Maintenance: {h.last_maintenance_error}</p>}
        <ErrorBox error={health.error ?? act.error} />
      </Card>

      <div className="grid">
        <Card title="Recommendations">
          <div className="stats">
            {(["APPLY", "STRETCH", "REVIEW", "SKIP"] as const).map((c) => (
              <div key={c} className="stat"><Badge value={c} /><strong>{counts[c] ?? 0}</strong></div>))}
          </div>
          <ErrorBox error={recs.error} />
        </Card>
        <Card title="Queues and inference">
          <dl>
            <dt>Analyses</dt><dd>{fmtCounts(analysis.data?.counts)}</dd>
            <dt>Evaluations</dt><dd>{fmtCounts(evaluation.data?.counts)}</dd>
            <dt>Model server</dt>
            <dd>{gate === undefined || gate.up === null ? <span className="muted">not checked yet</span> : gate.up
              ? <Badge value="pass" title="answering" /> : <span className="warn">down ({gate.reason}); next check in {age(Math.max(0, Math.round((gate.next_check_ms - Date.now()) / 1000)))}</span>}</dd>
          </dl>
          {(evaluation.data?.pending_waiting_for_disabled_config ?? 0) > 0 &&
            <p className="warn">Work is waiting for a model config that is not enabled (Preferences).</p>}
          <ErrorBox error={analysis.error ?? evaluation.error} />
        </Card>
        <Card title="Filter">
          {filter.data?.verdicts ? (
            <dl>
              <dt>Profile</dt><dd>v{filter.data.profile_version} ({filter.data.ruleset})</dd>
              <dt>Passed</dt><dd>{filter.data.verdicts.pass ?? 0}</dd>
              <dt>Rejected</dt><dd>{filter.data.verdicts.reject ?? 0} <span className="muted">{fmtCounts(filter.data.rejected_by_primary_category)}</span></dd>
            </dl>) : <p className="muted">No search profile yet (Preferences).</p>}
          <ErrorBox error={filter.error} />
        </Card>
      </div>

      <Card title="Sources" actions={d && <span className="muted">{d.jobs_open} open jobs · {d.polls_in_flight} polls in flight</span>}>
        <table>
          <thead><tr><th>Source</th><th>Polls / h</th><th>Not modified</th><th>Requests / budget</th><th>Data / h</th><th>Backoff</th></tr></thead>
          <tbody>{(d?.sources ?? []).filter((s) => s.enabled).map((s) => (
            <tr key={s.kind}><td>{s.kind}</td><td>{s.polls_last_hour}</td>
              <td>{s.polls_last_hour ? `${Math.round(100 * s.not_modified_last_hour / s.polls_last_hour)}%` : "-"}</td>
              <td>{s.requests_last_hour} / {s.max_requests_per_hour}</td><td>{(s.bytes_last_hour / 1e6).toFixed(1)} MB</td>
              <td>{s.backoff_until_ms ? <span className="warn">{s.backoff_reason}</span> : "-"}</td></tr>))}</tbody>
        </table>
        <ErrorBox error={disc.error} />
      </Card>

      <Card title={`Employers needing attention (${troubled.length})`} actions={
        <label className="muted"><input type="checkbox" checked={showAll} onChange={(e) => setShowAll(e.target.checked)} /> show all {d?.employers.length ?? 0}</label>}>
        {(showAll ? d?.employers ?? [] : troubled).length === 0 ? <p className="muted">Every enabled employer polled successfully within two intervals.</p> : (
          <table>
            <thead><tr><th>Employer</th><th>Source</th><th>Last success</th><th>Next poll</th><th>Failures</th><th>Last outcome</th><th /></tr></thead>
            <tbody>{(showAll ? d!.employers : troubled).map((e) => (
              <tr key={e.id} className={e.enabled ? "" : "muted"}>
                <td>{e.name}</td><td>{e.source}</td><td>{age(e.last_success_age_s)} ago</td><td>{e.next_poll_in_s === null ? "-" : `in ${age(Math.max(0, e.next_poll_in_s))}`}</td>
                <td>{e.consecutive_failures ?? 0}</td><td title={e.last_error ?? ""}>{e.status !== "active" ? <Badge value={e.status} /> : e.last_outcome ?? "-"}</td>
                <td><button className="small" disabled={act.busy || !e.enabled} onClick={() => act.run(async () => { await api.pollNow(e.id); disc.reload(); })}>Poll now</button></td>
              </tr>))}</tbody>
          </table>)}
      </Card>
    </div>
  );
}

function fmtCounts(c: Record<string, number> | undefined): string {
  if (!c || Object.keys(c).length === 0) return "none";
  return Object.entries(c).map(([k, v]) => `${v} ${k}`).join(", ");
}
