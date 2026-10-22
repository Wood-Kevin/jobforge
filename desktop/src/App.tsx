import { useCallback, useEffect, useState } from "react";
import { agentCall, onAgentEvent, onSupervisorChange, supervisorStatus, type ChangeEvent, type Health, type Supervisor } from "./agent";

// P1.2 shell window: supervisor state, agent health, pause/resume, live events.
// The full UI (dashboard, jobs, profile) arrives in P1.8.
export default function App() {
  const [sup, setSup] = useState<Supervisor | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<ChangeEvent[]>([]);

  const refresh = useCallback(async () => {
    try {
      setSup(await supervisorStatus());
      setHealth(await agentCall<Health>("GET", "/v1/health"));
      setError(null);
    } catch (e) {
      setHealth(null);
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    const unlisten = [
      onAgentEvent((e) => {
        setEvents((prev) => [e, ...prev].slice(0, 50));
        refresh();
      }),
      onSupervisorChange((s) => setSup(s)),
    ];
    return () => {
      clearInterval(timer);
      unlisten.forEach((p) => p.then((fn) => fn()));
    };
  }, [refresh]);

  const togglePause = async () => {
    if (!health) return;
    await agentCall("POST", health.discovery_paused ? "/v1/discovery/resume" : "/v1/discovery/pause");
    refresh();
  };

  return (
    <main>
      <h1>JobForge</h1>
      <section className="card">
        <h2>Background agent</h2>
        <p>
          <span className={`dot ${sup?.state ?? "starting"}`} /> {label(sup)}
        </p>
        {health && (
          <dl>
            <dt>Version</dt>
            <dd>{health.version} (schema {health.schema})</dd>
            <dt>Process</dt>
            <dd>pid {health.pid}, up {formatUptime(health.uptime_s)}</dd>
            <dt>Discovery</dt>
            <dd>{health.discovery_paused ? "paused" : "running"}</dd>
            {health.last_maintenance_error && (
              <>
                <dt>Maintenance</dt>
                <dd className="warn">{health.last_maintenance_error}</dd>
              </>
            )}
          </dl>
        )}
        {error && !health && <p className="warn">{error}</p>}
        <button onClick={togglePause} disabled={!health}>
          {health?.discovery_paused ? "Resume discovery" : "Pause discovery"}
        </button>
      </section>
      <section className="card">
        <h2>Live events</h2>
        {events.length === 0 ? (
          <p className="muted">No events since this window opened.</p>
        ) : (
          <ul>
            {events.map((e) => (
              <li key={e.id}>
                <code>#{e.id}</code> {e.type} <span className="muted">{new Date(e.created_at_ms).toLocaleTimeString()}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
      <p className="muted">Closing this window keeps JobForge running in the tray. Use the tray menu to exit.</p>
    </main>
  );
}

function label(s: Supervisor | null): string {
  if (!s) return "Connecting...";
  const text: Record<Supervisor["state"], string> = {
    starting: "Starting the agent...",
    running: "Running",
    not_responding: "Agent not responding",
    stopped_db_newer: "Stopped: the database is newer than this version of JobForge. Restore a backup or install the newer version.",
    stopped_elevated: "Stopped: JobForge must run as you, without administrator rights.",
    failing: "Agent keeps failing; see the log folder.",
    exiting: "Exiting...",
  };
  return s.detail ? `${text[s.state]} (${s.detail})` : text[s.state];
}

function formatUptime(s: number): string {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
