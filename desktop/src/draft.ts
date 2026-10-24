// Unsaved edits to the search form, kept while the window is open (UX redesign step 5): leaving the page used
// to drop them without a word, and the window has no dialog to ask "leave anyway?". The form starts from the
// draft when there is one, and the menu marks the page while it has unsaved changes. Window memory only:
// closing the window ends the draft, as it ended the old form's.
import { useEffect, useState } from "react";
import type { SearchForm } from "./api";

let draft: SearchForm | null = null;
const CHANGED = "jobforge-search-draft";

export const getDraft = () => draft;

export function setDraft(f: SearchForm | null): void {
  if (f === draft) return;
  draft = f;
  window.dispatchEvent(new Event(CHANGED));
}

export function useHasDraft(): boolean {
  const [has, setHas] = useState(draft !== null);
  useEffect(() => {
    const update = () => setHas(draft !== null);
    window.addEventListener(CHANGED, update);
    return () => window.removeEventListener(CHANGED, update);
  }, []);
  return has;
}
