// The JobForge window (P1.8). Navigation is local state (no router): the window is one process's
// view of the agent, and closing it keeps JobForge running in the tray.
import { useEffect, useRef, useState } from "react";
import { onSupervisorChange, supervisorStatus, type Supervisor } from "./agent";
import Applications from "./pages/Applications";
import Dashboard from "./pages/Dashboard";
import JobDetail from "./pages/JobDetail";
import Jobs from "./pages/Jobs";
import Preferences from "./pages/Preferences";
import Profile from "./pages/Profile";
import Review from "./pages/Review";
import Restore from "./pages/Restore";
import Setup from "./pages/Setup";
import { api } from "./api";
import { icons, Logo } from "./icons";
import { useHasDraft } from "./draft";
import { byJob } from "./reviewGroups";
import { useLive } from "./ui";

type Page = "setup" | "dashboard" | "jobs" | "review" | "profile" | "applications" | "preferences";
// The menu in two groups (visual foundation 2026-10-24): finding work, then what JobForge knows about you. Status
// sits at the bottom with the running dot; Setup shows only until setup is complete (Preferences links to it).
const FIND: [Page, string][] = [["jobs", "Jobs"], ["review", "Needs your input"], ["applications", "Applications"]];
const ABOUT: [Page, string][] = [["profile", "Your profile"], ["preferences", "What you're looking for"]];

export default function App() {
  const [page, setPage] = useState<Page>("jobs");
  const [job, setJob] = useState<number | null>(null);
  const [sup, setSup] = useState<Supervisor | null>(null);
  // Failures of the two startup checks are shown, not dropped: a dropped one left "Starting..." or
  // skipped the setup guide with no sign why.
  const [startError, setStartError] = useState<string | null>(null);
  useEffect(() => {
    supervisorStatus().then(setSup).catch((e) => { setSup(null); setStartError(`Could not read the host's status: ${String(e)}`); });
    const un = onSupervisorChange(setSup);
    return () => { un.then((fn) => fn()); };
  }, []);
  // First run: open the setup guide until companies, a search profile and the model are in place.
  const [checkedSetup, setCheckedSetup] = useState(false);
  const [setupDone, setSetupDone] = useState(true);
  useEffect(() => {
    if (checkedSetup || sup?.state !== "running") return;
    api.setupStatus().then((s) => { if (!s.complete) setPage("setup"); setSetupDone(s.complete); setCheckedSetup(true); setStartError(null); })
      .catch((e) => setStartError(`Could not check whether setup is finished: ${e instanceof Error ? e.message : String(e)}`));
  }, [sup, checkedSetup]);
  // Opening a job keeps the page it was opened from mounted (hidden), so its tab, search, filters and
  // sort survive; Back restores the scroll position and puts focus back on what opened the job (UX
  // review 2026-10-24: returning used to rebuild the list with defaults, scrolled to the top).
  const back = useRef<{ y: number; focus: HTMLElement | null } | null>(null);
  const go = (p: Page) => { back.current = null; setJob(null); setPage(p); };
  const open = (id: number) => {
    back.current = { y: window.scrollY, focus: document.activeElement instanceof HTMLElement ? document.activeElement : null };
    setJob(id);
  };
  const close = () => setJob(null);
  useEffect(() => { window.scrollTo(0, 0); }, [page]); // another page starts at its top
  useEffect(() => {
    if (job !== null) { window.scrollTo(0, 0); return; }
    const b = back.current;
    back.current = null;
    if (!b) return;
    window.scrollTo(0, b.y);
    if (b.focus?.isConnected) b.focus.focus({ preventScroll: true });
  }, [job]);
  const down = sup && sup.state !== "running";
  // Menu counts: the jobs worth a look (Apply and Stretch, the Jobs page's default view) and the jobs waiting on
  // an answer, counted like the Needs your input page counts them. A failed count shows no number; the page
  // itself shows the error when opened.
  const recs = useLive(() => api.jobs("recommended", "", 500), [], ["recommendation_computed", "golden_labeled", "job_closed", "job_reopened", "refilter_done"]);
  const questions = useLive(api.reviewQuestions, [], ["recommendation_computed", "profile_changed", "golden_labeled"]);
  const counts: Partial<Record<Page, number>> = {
    jobs: recs.data?.jobs.filter((j) => j.recommendation && ["APPLY", "STRETCH"].includes(j.recommendation.recommendation)).length,
    review: questions.data ? byJob(questions.data).jobs.length : undefined,
  };
  const unsaved = useHasDraft();
  const item = ([p, label]: [Page, string]) => (
    <button key={p} className={page === p && job === null ? "nav active" : "nav"} aria-current={page === p && job === null ? "page" : undefined} onClick={() => go(p)}>
      {icons[p as keyof typeof icons]}<span>{label}</span>
      {counts[p] !== undefined && counts[p]! > 0 && <span className={p === "review" ? "count attention" : "count"}>{counts[p]}</span>}
      {p === "preferences" && unsaved && <span className="unsaved" title="Unsaved changes"><span className="visually-hidden">Unsaved changes</span></span>}
    </button>);

  return (
    <div className="shell">
      <nav aria-label="Main">
        <div className="brand"><Logo />JobForge</div>
        {(!setupDone || page === "setup") && item(["setup", setupDone ? "Setup guide" : "Finish setting up"])}
        <div className="group">Find work</div>
        {FIND.map(item)}
        <div className="group">About you</div>
        {ABOUT.map(item)}
        <div className="foot">
          <button className={page === "dashboard" && job === null ? "nav status active" : "nav status"} onClick={() => go("dashboard")}>
            <span className={`dot ${sup?.state ?? "starting"}`} />
            <span>{sup?.state === "running" ? "Running · Status" : sup?.state === "starting" || !sup ? "Starting..." : "Not running · Status"}</span>
          </button>
        </div>
      </nav>
      <main>
        {down && <p className="banner">{supervisorText(sup!)}</p>}
        {startError && <p className="banner">{startError}</p>}
        {/* Only the restore screen while stopped this way: every page would just show "not reachable" (P1.9 drill). */}
        {sup?.state === "stopped_db_newer" ? <Restore />
          : <>
            {job !== null && <JobDetail id={job} onBack={close} />}
            <div hidden={job !== null}>
              {page === "dashboard" ? <Dashboard sup={sup} onSetup={() => go("setup")} />
                : page === "jobs" ? <Jobs onOpen={open} />
                : page === "review" ? <Review onOpen={open} onProfile={() => go("profile")} />
                : page === "profile" ? <Profile />
                : page === "applications" ? <Applications onOpen={open} onJobs={() => go("jobs")} />
                : page === "setup" ? <Setup onDone={() => { setSetupDone(true); go("jobs"); }} />
                : <Preferences onSetup={() => go("setup")} />}
            </div>
          </>}
      </main>
    </div>
  );
}

function supervisorText(s: Supervisor): string {
  const text: Record<Supervisor["state"], string> = {
    starting: "Starting the agent...",
    running: "Running",
    not_responding: "The agent is not responding.",
    stopped_db_newer: "Stopped: your data is from a newer version of JobForge. See below.",
    stopped_elevated: "Stopped: JobForge must run as you, without administrator rights.",
    failing: "The agent keeps failing; see the log folder.",
    exiting: "Exiting...",
  };
  return s.detail ? `${text[s.state]} (${s.detail})` : text[s.state];
}
