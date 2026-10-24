// Job list (P1.8). Default view APPLY + STRETCH ranked by opportunity value (ARCHITECTURE 6.5 noise
// control); REVIEW jobs live in the REVIEW questions view; SKIP and the filter's rejects are one click
// away, never hidden. No "everything" tab: it was "For you" plus "Filtered out" (2026-10-24: 111 and
// 125,232 of 125,378 open jobs) and the slowest list to load. A job you skip (your call, the golden
// label) moves to "Skipped by you" with its exact copies; JobForge's own recommendation for it is
// unchanged. Column headers sort the rows (sortJobs.ts). A new posting appears without reload
// (posting_new / recommendation_computed).
import { useState, type MouseEvent, type ReactNode } from "react";
import { api, type Category, type JobRow } from "../api";
import { FIRST_DIR, sortJobs, type Sort, type SortKey } from "../sortJobs";
import { Badge, Card, ErrorBox, ago, useAction, useLive } from "../ui";

const LIMIT = 500;

type View = "recommended" | "rejected" | "skipped";

export default function Jobs({ onOpen }: { onOpen: (id: number) => void }) {
  const [view, setView] = useState<View>("recommended");
  const [sort, setSort] = useState<Sort>({ key: "recommendation", dir: "asc" });
  const [cats, setCats] = useState<Category[]>(["APPLY", "STRETCH"]);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const list = useLive(() => api.jobs(view, query, LIMIT), [view, query],
    ["posting_new", "posting_changed", "job_closed", "job_reopened", "recommendation_computed", "refilter_done", "application_changed", "evaluate_anyway", "golden_labeled"]);
  const shown = (list.data?.jobs ?? []).filter((j) => view !== "recommended" || (j.recommendation && cats.includes(j.recommendation.recommendation)));
  const rows = sortJobs(shown, sort, (j) => filterReason(j.filter_primary));
  const toggle = (c: Category) => setCats((cur) => cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]);
  // Each tab opens in the agent's own order: For you by recommendation, the others newest first.
  const open = (v: View) => { setView(v); setSort(v === "recommended" ? { key: "recommendation", dir: "asc" } : { key: "found", dir: "desc" }); };
  const by = (key: SortKey) => setSort((cur) => cur.key === key ? { key, dir: cur.dir === "asc" ? "desc" : "asc" } : { key, dir: FIRST_DIR[key] });
  const head = (key: SortKey | null, label: ReactNode) => key === null ? <th>{label}</th> : (
    <th aria-sort={sort.key === key ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button className="sort" onClick={() => by(key)}>{label}{sort.key === key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""}</button></th>);
  const rejected = view === "rejected";
  // Filtered out holds far more than one load: the window has its newest LIMIT, so another order sorts those.
  const partial = list.data !== null && list.data !== undefined && list.data.total > list.data.jobs.length && sort.key !== "found";

  return (
    <div className="page">
      <Card title="Jobs" actions={
        <form className="row" onSubmit={(e) => { e.preventDefault(); setQuery(search.trim()); }}>
          <input placeholder="Search title, company, location" value={search} onChange={(e) => setSearch(e.target.value)} />
          <button type="submit">Search</button>
        </form>}>
        <div className="tabs">
          {(["recommended", "rejected", "skipped"] as View[]).map((v) => (
            <button key={v} className={view === v ? "tab active" : "tab"} onClick={() => open(v)}>
              {v === "recommended" ? "For you" : v === "rejected" ? "Filtered out" : "Skipped by you"}</button>))}
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
            <thead><tr>{head("job", "Job")}{rejected ? head("reason", "Why it was filtered out") : head("recommendation", "Recommendation")}
              {head(rejected ? null : "fit", "Fit")}{head(rejected ? null : "opportunity", "Pay, place, level")}{head("found", "Found")}<th /></tr></thead>
            <tbody>{rows.map((j) => <Row key={j.id} j={j} view={view} onOpen={onOpen} />)}</tbody>
          </table>)}
        <p className="muted">{rows.length} shown{list.data ? ` of ${list.data.total}` : ""}
          {partial && ` · sorted among the ${list.data!.jobs.length} found most recently; search to narrow the rest`}</p>
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
        {j.application_status && <> · <Badge value={j.application_status} /></>}</div>
        <ErrorBox error={act.error} /></td>
      <td>{view === "rejected" ? <span className="muted">{filterReason(j.filter_primary)}{j.override ? " (assessing anyway)" : ""}</span>
        : r ? <><Badge value={r.recommendation} />{!r.current && <span className="muted" title="based on your profile before your latest edit"> (updating)</span>}</>
        : <span className="muted">{j.filter_verdict === "pass" ? "being read" : "-"}</span>}</td>
      {/* No eligibility column: APPLY and STRETCH require ELIGIBLE, so on "For you" it could only say
          "You qualify" (29 of 29, 2026-10-24); the job page lists each eligibility check. */}
      <td><Badge value={r?.fit} /></td><td><Badge value={r?.opportunity} /></td>
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
