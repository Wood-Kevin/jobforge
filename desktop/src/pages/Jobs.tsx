// Job list (P1.8). Default view APPLY + STRETCH ranked by opportunity value (ARCHITECTURE 6.5 noise
// control); REVIEW jobs live in the REVIEW questions view; SKIP and the filter's rejects are one click
// away, never hidden. A job you skip (your call, the golden label) moves to "Skipped by you" with its
// exact copies; JobForge's own recommendation for it is unchanged. A new posting appears without reload
// (posting_new / recommendation_computed).
import { useState, type MouseEvent } from "react";
import { api, type Category, type JobRow } from "../api";
import { Badge, Card, ErrorBox, ago, useAction, useLive } from "../ui";

type View = "recommended" | "rejected" | "skipped" | "all";

export default function Jobs({ onOpen }: { onOpen: (id: number) => void }) {
  const [view, setView] = useState<View>("recommended");
  const [cats, setCats] = useState<Category[]>(["APPLY", "STRETCH"]);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const list = useLive(() => api.jobs(view, query, 500), [view, query],
    ["posting_new", "posting_changed", "job_closed", "job_reopened", "recommendation_computed", "refilter_done", "application_changed", "evaluate_anyway", "golden_labeled"]);
  const rows = (list.data?.jobs ?? []).filter((j) => view !== "recommended" || (j.recommendation && cats.includes(j.recommendation.recommendation)));
  const toggle = (c: Category) => setCats((cur) => cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]);

  return (
    <div className="page">
      <Card title="Jobs" actions={
        <form className="row" onSubmit={(e) => { e.preventDefault(); setQuery(search.trim()); }}>
          <input placeholder="Search title, company, location" value={search} onChange={(e) => setSearch(e.target.value)} />
          <button type="submit">Search</button>
        </form>}>
        <div className="tabs">
          {(["recommended", "rejected", "skipped", "all"] as View[]).map((v) => (
            <button key={v} className={view === v ? "tab active" : "tab"} onClick={() => setView(v)}>
              {v === "recommended" ? "For you" : v === "rejected" ? "Filtered out" : v === "skipped" ? "Skipped by you" : "Everything found"}</button>))}
        </div>
        {view === "recommended" && (
          <div className="chips">{(["APPLY", "STRETCH", "REVIEW", "SKIP"] as Category[]).map((c) => (
            <label key={c} className={cats.includes(c) ? "chip on" : "chip"}>
              <input type="checkbox" checked={cats.includes(c)} onChange={() => toggle(c)} /> <Badge value={c} /></label>))}
          </div>)}
        {view === "skipped" && <p className="muted">Jobs you said you'd skip, with their exact copies. Each one also counts as your
          call for measuring JobForge. Undo puts a job back.</p>}
        <ErrorBox error={list.error} />
        {list.loading && !list.data ? <p className="muted">Loading...</p> : rows.length === 0 ? (
          <p className="muted">{view === "recommended" ? "No jobs here yet. New postings are checked every 15 minutes and each one is read by the AI model before it shows up here. Jobs that need an answer from you are under Needs your input." : "Nothing here."}</p>) : (
          <table className="jobs">
            <thead><tr><th>Job</th><th>{view === "rejected" ? "Why it was filtered out" : "Recommendation"}</th><th>Eligibility</th><th>Fit</th><th>Pay, place, level</th><th>Found</th><th /></tr></thead>
            <tbody>{rows.map((j) => <Row key={j.id} j={j} view={view} onOpen={onOpen} />)}</tbody>
          </table>)}
        <p className="muted">{rows.length} shown{list.data ? ` of ${list.data.total}` : ""}</p>
      </Card>
    </div>
  );
}

function Row({ j, view, onOpen }: { j: JobRow; view: View; onOpen: (id: number) => void }) {
  const r = j.recommendation;
  const act = useAction();
  // The row opens the job; its buttons must not.
  const run = (e: MouseEvent, f: () => Promise<unknown>) => { e.stopPropagation(); act.run(f); };
  return (
    <tr className="clickable" onClick={() => onOpen(j.id)}>
      <td><strong>{j.title}</strong><div className="muted">{j.company}{j.location ? ` · ${j.location}` : ""}
        {j.copies > 1 && ` · posted ${j.copies} times`}
        {j.application_status && <> · <Badge value={j.application_status} /></>}
        {view === "all" && j.skipped && " · skipped by you"}</div><ErrorBox error={act.error} /></td>
      <td>{view === "rejected" ? <span className="muted">{filterReason(j.filter_primary)}{j.override ? " (assessing anyway)" : ""}</span>
        : r ? <><Badge value={r.recommendation} />{!r.current && <span className="muted" title="based on your profile before your latest edit"> (updating)</span>}</>
        : <span className="muted">{j.filter_verdict === "pass" ? "being read" : "-"}</span>}</td>
      <td><Badge value={r?.eligibility} /></td><td><Badge value={r?.fit} /></td><td><Badge value={r?.opportunity} /></td>
      <td className="muted">{ago(j.first_seen_at_ms)}</td>
      <td>{j.skipped === "you" ? <button className="small" disabled={act.busy} onClick={(e) => run(e, () => api.withdrawGoldenLabel(j.id))}>Undo</button>
        : !j.skipped && <button className="small" disabled={act.busy} title="Hide it from For you; counts as your call"
          onClick={(e) => run(e, () => api.setGoldenLabel(j.id, { recommendation: "SKIP", keep_details: true }))}>Skip</button>}</td>
    </tr>
  );
}

function filterReason(primary: string | null): string {
  const r = (primary ?? "").split(":")[0];
  const text: Record<string, string> = {
    TITLE_NO_MATCH: "Title not on your list", TITLE_SENIORITY: "Level too senior", TITLE_NON_IT: "A field you skip",
    LOCATION_NOT_ELIGIBLE: "On-site, too far", REMOTE_EXCLUDES_HOME: "Remote, not open where you live",
    REMOTE_ANCHORED: "Tied to an office elsewhere", STALE_POSTING: "Too old", SALARY_BELOW_FLOOR: "Pay below your minimum",
  };
  return text[r] ?? "Doesn't match your search";
}
