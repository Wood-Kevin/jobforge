// Job list (P1.8). Default view APPLY + STRETCH ranked by opportunity value (ARCHITECTURE 6.5 noise
// control); REVIEW jobs live in the REVIEW questions view; SKIP and the filter's rejects are one click
// away, never hidden. No "everything" tab: it was "For you" plus "Filtered out" (2026-10-24: 111 and
// 125,232 of 125,378 open jobs) and the slowest list to load. A job you skip (your call, the golden
// label) moves to "Skipped by you" with its exact copies; JobForge's own recommendation for it is
// unchanged. The sort menu orders the rows (sortJobs.ts) and the filter row narrows them (filterJobs.ts).
// A new posting appears without reload
// (posting_new / recommendation_computed).
// Rows, not a table (UX redesign step 2): each row says why the job is there (the fit counts as a bar and a
// line) and its pay, and the recommendation is the one colored mark; fit and pay/place/level read as text.
// Column headers became a sort menu: with rows there are no columns to click.
import { useCallback, useState, type MouseEvent } from "react";
import { api, type Category, type JobRow } from "../api";
import { FOUND_WITHIN, NO_FILTERS, filterJobs, isFiltered, type Filters } from "../filterJobs";
import { icons } from "../icons";
import { OPPORTUNITY_SHORT, fitLabel, fmtSalary, opportunityLabel, plain, whyLine } from "../labels";
import { sortJobs, type Sort } from "../sortJobs";
import { Badge, Card, ErrorBox, FitBar, PageHeader, RecBadge, Toast, ago, useAction, useLive } from "../ui";

const LIMIT = 500;
const FITS = ["STRONG", "COMPETITIVE", "STRETCH", "WEAK", "INSUFFICIENT"];
const OPPORTUNITIES = ["HIGH", "MEDIUM", "LOW", "UNKNOWN"];

type View = "recommended" | "rejected" | "skipped";

// The sort menu: what the column headers did, as named orders.
const SORTS: Record<View, [string, Sort][]> = {
  recommended: [["Best match first", { key: "recommendation", dir: "asc" }], ["Newest first", { key: "found", dir: "desc" }],
    ["Best fit first", { key: "fit", dir: "asc" }], ["Best pay & place first", { key: "opportunity", dir: "asc" }],
    ["Job title, A to Z", { key: "job", dir: "asc" }]],
  rejected: [["Newest first", { key: "found", dir: "desc" }], ["By reason", { key: "reason", dir: "asc" }],
    ["Job title, A to Z", { key: "job", dir: "asc" }]],
  skipped: [["Newest first", { key: "found", dir: "desc" }], ["Best match first", { key: "recommendation", dir: "asc" }],
    ["Job title, A to Z", { key: "job", dir: "asc" }]],
};
const sortId = (s: Sort) => `${s.key}:${s.dir}`;

export default function Jobs({ onOpen }: { onOpen: (id: number) => void }) {
  const [view, setView] = useState<View>("recommended");
  const [sort, setSort] = useState<Sort>({ key: "recommendation", dir: "asc" });
  const [cats, setCats] = useState<Category[]>(["APPLY", "STRETCH"]);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  // "I'd skip it" moves the job out of this list; the message offers Undo right here.
  const [skipped, setSkipped] = useState<{ id: number; title: string } | null>(null);
  const undo = useAction();
  const closeToast = useCallback(() => setSkipped(null), []);
  const list = useLive(() => api.jobs(view, query, LIMIT), [view, query],
    ["posting_new", "posting_changed", "job_closed", "job_reopened", "recommendation_computed", "refilter_done", "application_changed", "evaluate_anyway", "golden_labeled"]);
  const reason = (j: JobRow) => filterReason(j.filter_primary);
  const loaded = list.data?.jobs ?? [];
  const shown = filterJobs(loaded.filter((j) => view !== "recommended" || (j.recommendation && cats.includes(j.recommendation.recommendation))),
    filters, Date.now(), reason);
  const rows = sortJobs(shown, sort, reason);
  const reasons = [...new Set(loaded.map(reason))].sort();
  const set = (f: Partial<Filters>) => setFilters((cur) => ({ ...cur, ...f }));
  const toggle = (c: Category) => setCats((cur) => cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]);
  // Each tab opens in the agent's own order: For you by recommendation, the others newest first.
  const open = (v: View) => {
    setView(v); setFilters(NO_FILTERS);
    setSort(SORTS[v][0][1]);
  };
  // Filtered out holds far more than one load: the window has its newest LIMIT, so another order, or a
  // filter, works on those.
  const partial = list.data !== null && list.data !== undefined && list.data.total > list.data.jobs.length
    && (sort.key !== "found" || isFiltered(filters));
  const pick = (label: string, value: string, options: [string, string][], onChange: (v: string) => void) => (
    <label className="filter">{label}{" "}
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">any</option>{options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>);

  return (
    <div className="page">
      <PageHeader title="Jobs" sub="Jobs JobForge found for you, best match first. New postings are checked every 15 minutes."
        actions={<form className="row" role="search" onSubmit={(e) => { e.preventDefault(); setQuery(search.trim()); }}>
          <input aria-label="Search jobs" placeholder="Search title, company, location" value={search} onChange={(e) => setSearch(e.target.value)} />
          <button type="submit">Search</button>
        </form>} />
      <div className="row toolbar">
        <div className="tabs">
          {(["recommended", "skipped", "rejected"] as View[]).map((v) => (
            <button key={v} className={view === v ? "tab active" : "tab"} aria-pressed={view === v} onClick={() => open(v)}>
              {v === "recommended" ? "For you" : v === "rejected" ? "Filtered out" : "Skipped by you"}</button>))}
        </div>
        <span className="spacer" />
        <label className="filter">Sort{" "}
          <select value={sortId(sort)} onChange={(e) => setSort(SORTS[view].find(([, s]) => sortId(s) === e.target.value)![1])}>
            {SORTS[view].map(([t, s]) => <option key={sortId(s)} value={sortId(s)}>{t}</option>)}</select></label>
      </div>
      {view === "recommended" && (
        <div className="chips" role="group" aria-label="Show">{(["APPLY", "STRETCH", "REVIEW", "SKIP"] as Category[]).map((c) => (
          <label key={c} className={cats.includes(c) ? "chip on" : "chip"}>
            <input className="visually-hidden" type="checkbox" checked={cats.includes(c)} onChange={() => toggle(c)} /><Badge value={c} /></label>))}
        </div>)}
      <div className="row filters">
        {view === "rejected" ? pick("Why filtered out:", filters.reason, reasons.map((r) => [r, r]), (v) => set({ reason: v })) : <>
          {pick("Fit:", filters.fit, FITS.map((v) => [v, fitLabel(v)]), (v) => set({ fit: v }))}
          {pick("Pay, place, level:", filters.opportunity, OPPORTUNITIES.map((v) => [v, opportunityLabel(v) ?? plain(v)]),
            (v) => set({ opportunity: v }))}</>}
        {pick("Found:", filters.foundWithinDays === null ? "" : String(filters.foundWithinDays), FOUND_WITHIN.map(([d, t]) => [String(d), t]),
          (v) => set({ foundWithinDays: v ? Number(v) : null }))}
        {isFiltered(filters) && <button className="small" onClick={() => setFilters(NO_FILTERS)}>Clear filters</button>}
      </div>
      {view === "skipped" && <p className="muted">Jobs you said you'd skip, with their exact copies. Each one also counts as your
        call for measuring JobForge. Undo puts a job back.</p>}
      <ErrorBox error={list.error ?? undo.error} />
      {/* Loading, failed, filtered to nothing, and truly empty are different states (UX review 2026-10-24). */}
      {!list.data ? (list.error ? null : <Card><p className="muted">Loading...</p></Card>) : rows.length === 0 ? (
        <Card>{isFiltered(filters) || (view === "recommended" && loaded.length > 0)
          ? <p className="muted">{isFiltered(filters) ? "No jobs match these filters" : "No jobs in the groups selected above"}
              {partial || list.data.total > loaded.length ? ` (among the ${loaded.length} loaded)` : ""}.{" "}
              {isFiltered(filters) && <button className="small" onClick={() => setFilters(NO_FILTERS)}>Clear filters</button>}</p>
          : <p className="muted">{view === "recommended" ? "No jobs here yet. New postings are checked every 15 minutes and each one is read by the AI model before it shows up here (Status shows how many are waiting). Jobs that need an answer from you are under Needs your input." : "Nothing here."}</p>}</Card>
      ) : (
        <ul className="job-list">{rows.map((j) => <Row key={j.id} j={j} view={view} onOpen={onOpen} onSkipped={setSkipped} />)}</ul>)}
      <p className="muted">{rows.length} shown{list.data ? ` of ${list.data.total}` : ""}
        {partial && ` · ${isFiltered(filters) ? "filtered" : "sorted"} among the ${list.data!.jobs.length} found most recently; search to narrow the rest`}</p>
      {skipped && (
        <Toast onClose={closeToast}>
          <span>Skipped “{skipped.title}”</span>
          <button className="toast-action" disabled={undo.busy}
            onClick={() => undo.run(async () => { await api.withdrawGoldenLabel(skipped.id); setSkipped(null); })}>Undo</button>
        </Toast>)}
    </div>
  );
}

function Row({ j, view, onOpen, onSkipped }: { j: JobRow; view: View; onOpen: (id: number) => void;
  onSkipped: (s: { id: number; title: string }) => void }) {
  const r = j.recommendation;
  const act = useAction();
  // The row opens the job; its buttons must not.
  const run = (e: MouseEvent, f: () => Promise<unknown>) => { e.stopPropagation(); return act.run(f); };
  const why = whyLine(r?.fit_counts);
  return (
    <li className="job-row" onClick={() => onOpen(j.id)}>
      <div className="job-main">
        <button className="link job-title" onClick={(e) => { e.stopPropagation(); onOpen(j.id); }}>{j.title}</button>
        <div className="muted">{j.company}{j.location ? ` · ${j.location}` : ""}
          {j.copies > 1 && ` · posted ${j.copies} times`}
          {j.application_status && <> · <Badge value={j.application_status} /></>}</div>
        {view === "rejected"
          ? <div className="why muted">{filterReason(j.filter_primary)}{j.override ? " (assessing anyway)" : ""}</div>
          : why && <div className="why"><FitBar counts={r?.fit_counts} /><span className="muted">{why}</span></div>}
        <ErrorBox error={act.error} />
      </div>
      <div className="job-pay">
        <div className={j.salary ? "pay" : "pay none"}>{j.salary ? fmtSalary(j.salary) : "Pay not listed"}</div>
        {r && <div className="muted">{OPPORTUNITY_SHORT[r.opportunity] ?? plain(r.opportunity)}</div>}
      </div>
      <div className="job-rec">
        {view === "rejected" ? <span className="badge">Filtered out</span>
          : r ? <RecBadge value={r.recommendation} /> : <span className="muted">{j.filter_verdict === "pass" ? "Being read" : "-"}</span>}
        {r && !r.current && <span className="muted" title="based on your profile before your latest edit">Updating</span>}
        <span className="faint">{ago(j.first_seen_at_ms)} ago</span>
      </div>
      <div className="job-act">
        {j.skipped === "you"
          ? <button className="small" disabled={act.busy} onClick={(e) => run(e, () => api.withdrawGoldenLabel(j.id))}>Undo</button>
          : !j.skipped && <button className="quiet" disabled={act.busy} title="Hides the job and counts as your call on it"
            onClick={(e) => run(e, () => api.setGoldenLabel(j.id, { recommendation: "SKIP", keep_details: true }))
              .then((ok) => { if (ok) onSkipped({ id: j.id, title: j.title }); })}>{icons.hide}I'd skip it</button>}
      </div>
    </li>
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
