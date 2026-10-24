// Applications (P1.8): the user's own record. Status changes are append-only history rows
// (application_events); JobForge never contacts an employer.
import { api } from "../api";
import { Badge, Card, ErrorBox, PageHeader, since, useLive } from "../ui";

export default function Applications({ onOpen, onJobs }: { onOpen: (id: number) => void; onJobs: () => void }) {
  const apps = useLive(api.applications, [], ["application_changed"]);
  const rows = apps.data ?? [];
  return (
    <div className="page">
      <PageHeader title="Applications" sub="Jobs you're tracking. Set a status from a job's page. JobForge only keeps your record; it never applies or contacts an employer." />
      <Card>
        <ErrorBox error={apps.error} />
        {/* Loading, failed and empty are different states (UX review 2026-10-24: an empty table showed while loading). */}
        {apps.data === null ? (apps.error ? null : <p className="muted">Loading...</p>)
          : rows.length === 0 ? <p className="muted">No applications tracked yet. Save a job or set its status from the job's page.{" "}
            <button className="small" onClick={onJobs}>Go to Jobs</button></p> : (
          <table>
            <thead><tr><th>Job</th><th>Status</th><th>Notes</th><th>Last change</th></tr></thead>
            <tbody>{rows.map((a) => (
              <tr key={a.id} className="clickable" onClick={() => onOpen(a.job_id)}>
                <td><strong>{a.title}</strong><div className="muted">{a.company}{a.job_closed && " · posting closed"}</div></td>
                <td><Badge value={a.status} /></td><td>{a.notes}</td><td className="muted">{since(a.last_change_ms)}</td>
              </tr>))}</tbody>
          </table>)}
      </Card>
    </div>
  );
}
