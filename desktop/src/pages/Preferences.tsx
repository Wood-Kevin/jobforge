// Preferences (P1.8b): the same plain forms as first-run setup. No JSON, no patterns: the search
// profile form keeps any tuned patterns it does not show (agent: filtering/simple.py).
import { CompaniesCard, ModelCard, SearchFormCard } from "../settings";

export default function Preferences() {
  return (
    <div className="page">
      <SearchFormCard />
      <CompaniesCard />
      <ModelCard />
    </div>
  );
}
