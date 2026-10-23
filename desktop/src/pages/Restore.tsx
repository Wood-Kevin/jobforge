// Restore screen (ARCHITECTURE 7.2, P1.9): shown when the agent stopped because the data is from a
// newer version of JobForge (after going back to an older installer). Two ways out, said plainly:
// install the newer version again, or go back to a backup from before the upgrade. Restoring keeps
// today's data as a "before-restore" backup file, so it can be undone by reinstalling the newer version.
import { useEffect, useState } from "react";
import { listBackups, restoreBackup, type BackupList } from "../agent";
import { Card, ConfirmButton, ErrorBox, useAction } from "../ui";

export default function Restore() {
  const [list, setList] = useState<BackupList | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const act = useAction();
  useEffect(() => { act.run(async () => setList(await listBackups())); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // Only backups older than the live data's schema can be opened by this (older) version.
  const usable = (list?.backups ?? []).filter((b) => b.schema !== null && list!.current_schema !== null && b.schema < list!.current_schema!);
  return (
    <Card title="Your data is from a newer version of JobForge">
      <p>This copy of JobForge is older than the one that last used your data, so it has stopped instead of risking it.</p>
      <p><strong>Easiest:</strong> install the newer version of JobForge again. Nothing is lost.</p>
      <p><strong>Or go back to a backup</strong> made before the upgrade. Everything recorded after that backup (new jobs, your
        changes, labels, applications) will not be in JobForge any more. Today's data is first saved as a separate backup
        file, so installing the newer version later and restoring that file brings it back.</p>
      {list && usable.length === 0 && <p className="warn">No backup from before the upgrade was found.</p>}
      {usable.length > 0 && (
        <table><tbody>{usable.map((b) => (
          <tr key={b.name}><td>{new Date(b.modified_ms).toLocaleString()}</td><td className="muted">{b.name}</td>
            <td className="muted">{Math.round(b.bytes / 1024)} KB</td>
            <td><ConfirmButton label="Go back to this backup" disabled={act.busy}
              onConfirm={() => act.run(async () => { setDone(await restoreBackup(b.name)); })} /></td></tr>))}</tbody></table>)}
      {act.busy && <p className="muted">Working...</p>}
      {done && <p className="ok-text">Done. JobForge is starting with the restored data. ({done})</p>}
      <ErrorBox error={act.error} />
    </Card>
  );
}
