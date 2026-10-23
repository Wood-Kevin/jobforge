// The JobForge window (P1.8). Navigation is local state (no router): the window is one process's
// view of the agent, and closing it keeps JobForge running in the tray.
import { useEffect, useState } from "react";
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
  useEffect(() => {
    supervisorStatus().then(setSup).catch(() => setSup(null));
    const un = onSupervisorChange(setSup);
    return () => { un.then((fn) => fn()); };
  }, []);
  // First run: open the setup guide until companies, a search profile and the model are in place.
  const [checkedSetup, setCheckedSetup] = useState(false);
  useEffect(() => {
    if (checkedSetup || sup?.state !== "running") return;
    api.setupStatus().then((s) => { if (!s.complete) setPage("setup"); setCheckedSetup(true); }).catch(() => undefined);
  }, [sup, checkedSetup]);
  const go = (p: Page) => { setJob(null); setPage(p); };
  const open = (id: number) => setJob(id);
  useEffect(() => { window.scrollTo(0, 0); }, [page, job]); // a new view starts at its top
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
        {sup?.state === "stopped_db_newer" && <Restore />}
        {job !== null ? <JobDetail id={job} onBack={() => setJob(null)} />
          : page === "dashboard" ? <Dashboard sup={sup} onSetup={() => go("setup")} />
          : page === "jobs" ? <Jobs onOpen={open} />
          : page === "review" ? <Review onOpen={open} onProfile={() => go("profile")} />
          : page === "profile" ? <Profile />
          : page === "applications" ? <Applications onOpen={open} />
          : page === "setup" ? <Setup onDone={() => go("jobs")} />
          : <Preferences />}
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
