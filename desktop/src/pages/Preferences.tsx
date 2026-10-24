// Preferences (P1.8b): the same plain forms as first-run setup. No JSON, no patterns: the search
// profile form keeps any tuned patterns it does not show (agent: filtering/simple.py).
import { CompaniesCard, ModelCard, SearchFormCard } from "../settings";
import { Card, ErrorBox, PageHeader, useAction } from "../ui";
import { useAccuracyTools, writeAccuracyTools } from "../viewPrefs";

export default function Preferences({ onSetup }: { onSetup: () => void }) {
  return (
    <div className="page">
      <PageHeader title="What you're looking for" sub="Your search, the companies JobForge watches and the AI model that reads postings."
        actions={<button className="quiet" onClick={onSetup}>Open the setup guide</button>} />
      <SearchFormCard />
      <CompaniesCard />
      <ModelCard />
      <AccuracyToolsCard />
    </div>
  );
}

// The switch for the tools that check JobForge's own accuracy (viewPrefs.ts; off by default).
function AccuracyToolsCard() {
  const on = useAccuracyTools();
  const act = useAction();
  return (
    <Card title="Checking JobForge's accuracy">
      <label className="check"><input type="checkbox" checked={on} disabled={act.busy}
        onChange={(e) => { const v = e.target.checked; act.run(async () => writeAccuracyTools(v)); }} />
        Show the tools for checking JobForge's accuracy
        <div className="muted">Adds "Your call" and the technical details to each job, and how JobForge's calls compare with yours on
          Status. Useful if you're helping test JobForge; not needed to find work.</div></label>
      <ErrorBox error={act.error} />
    </Card>
  );
}
