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

type Page = "setup" | "dashboard" | "jobs" | "review" | "profile" | "applications" | "preferences";
const PAGES: [Page, string][] = [["jobs", "Jobs"], ["review", "Needs your input"], ["profile", "Your profile"],
  ["applications", "Applications"], ["dashboard", "Status"], ["preferences", "Preferences"], ["setup", "Setup"]];

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
  useEffect(() => {
    if (checkedSetup || sup?.state !== "running") return;
    api.setupStatus().then((s) => { if (!s.complete) setPage("setup"); setCheckedSetup(true); setStartError(null); })
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

  return (
    <div className="shell">
      <nav>
        <h1>JobForge</h1>
        {PAGES.map(([p, label]) => (
          <button key={p} className={page === p && job === null ? "nav active" : "nav"} onClick={() => go(p)}>{label}</button>))}
        <p className="muted foot"><span className={`dot ${sup?.state ?? "starting"}`} /> {sup?.state === "running" ? "Running in the background" : sup?.state === "starting" || !sup ? "Starting..." : "Not running - see Status"}</p>
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
                : page === "setup" ? <Setup onDone={() => go("jobs")} />
                : <Preferences />}
            </div>
          </>}
        <p className="muted">Closing this window keeps JobForge running in the tray. Use the tray menu to exit.</p>
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
