// Window-only display choices, kept in this window's own storage (WebView2 keeps it in the data folder's
// EBWebView profile). Only how the window looks: nothing here reaches the agent or changes a recommendation.
// Rejected: a table in the agent, which would need a migration and a route for a switch that only hides
// parts of the window.
import { useEffect, useState } from "react";

// "Tools for checking JobForge's accuracy": Your call on a job, Technical details, and the measuring line on
// Status. Off by default (UX redesign step 3): someone using JobForge to find work does not need them.
const ACCURACY_KEY = "jobforge.showAccuracyTools";
const CHANGED = "jobforge-view-prefs";

export function readAccuracyTools(): boolean {
  try {
    return window.localStorage.getItem(ACCURACY_KEY) === "1";
  } catch (e) {
    console.error("Could not read the accuracy-tools setting; showing them hidden.", e);
    return false;
  }
}

/** Throws when the setting can't be saved, so the caller shows the error. */
export function writeAccuracyTools(on: boolean): void {
  window.localStorage.setItem(ACCURACY_KEY, on ? "1" : "0");
  window.dispatchEvent(new Event(CHANGED));
}

export function useAccuracyTools(): boolean {
  const [on, setOn] = useState(readAccuracyTools);
  useEffect(() => {
    const update = () => setOn(readAccuracyTools());
    window.addEventListener(CHANGED, update);
    return () => window.removeEventListener(CHANGED, update);
  }, []);
  return on;
}
