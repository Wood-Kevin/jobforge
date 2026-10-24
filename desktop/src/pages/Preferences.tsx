// Preferences (P1.8b): the same plain forms as first-run setup. No JSON, no patterns: the search
// profile form keeps any tuned patterns it does not show (agent: filtering/simple.py).
import { CompaniesCard, ModelCard, SearchFormCard } from "../settings";

import { PageHeader } from "../ui";

export default function Preferences({ onSetup }: { onSetup: () => void }) {
  return (
    <div className="page">
      <PageHeader title="What you're looking for" sub="Your search, the companies JobForge watches and the AI model that reads postings."
        actions={<button className="quiet" onClick={onSetup}>Open the setup guide</button>} />
      <SearchFormCard />
      <CompaniesCard />
      <ModelCard />
    </div>
  );
}
