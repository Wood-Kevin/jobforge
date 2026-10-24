// Every internal term the window shows goes through here (P1.8b: "jargon everywhere"). The agent
// keeps its precise names; the person sees plain words. Unknown values fall back to a readable form.
export const PLAIN: Record<string, string> = {
  // recommendation
  APPLY: "Apply", STRETCH: "Stretch", REVIEW: "Needs your input", SKIP: "Skip",
  // eligibility
  ELIGIBLE: "You qualify", UNKNOWN: "Check eligibility", BLOCKED: "You don't qualify",
  // fit
  STRONG: "Strong fit", COMPETITIVE: "Good fit", WEAK: "Big gaps", INSUFFICIENT: "Not enough info",
  // opportunity
  // (not "Great": a green "Great" on a REVIEW row read as an overall verdict, usability pass 2026-10-24)
  HIGH: "Favorable", MEDIUM: "Mixed", LOW: "Unfavorable",
  // match
  direct: "You have this", transferable: "Related experience", gap: "Missing", unknown: "Not in your profile",
  // requirement class
  hard: "Must-have", negotiable: "Required", preferred: "Nice to have", context: "About the company", negated: "Not required",
  // reading of the posting
  confirmed: "Clear", conflict: "Unclear wording", unverifiable: "Not found in posting",
  // profile
  verified: "Confirmed", self_reported: "Not confirmed",
  // opportunity factor values
  favorable: "Good", neutral: "Neutral", unfavorable: "Poor",
  // gap size
  material: "Important", minor: "Small",
  // filter
  pass: "Matches your search", reject: "Filtered out",
  // applications
  saved: "Saved", applied: "Applied", interviewing: "Interviewing", offer: "Offer", rejected: "Rejected", withdrawn: "Withdrawn",
  // kinds and fact types
  role: "Role", achievement: "Achievement", skill: "Skill", cert: "Certification", education: "Education", eligibility: "Eligibility",
  work_authorization: "Work authorization", sponsorship: "Visa sponsorship", clearance: "Security clearance", residency: "Where you live",
  physical: "Physical", travel: "Travel", license: "License", program_status: "Military or student program", availability: "Hours and availability",
  // opportunity factor names
  pay: "Pay", work_mode: "Remote / on-site", location: "Location", seniority: "Level", role_family: "Kind of role", age: "Posting age",
  // employer status
  active: "Watching", dead: "Board not found", blocked: "Board refused access",
};

export function plain(v: string | null | undefined): string {
  if (!v) return "";
  return PLAIN[v] ?? v.replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
}

// One-line summaries of the three assessments.
export const ELIGIBILITY_TEXT: Record<string, string> = {
  ELIGIBLE: "Nothing in the posting rules you out.",
  UNKNOWN: "Something in the posting may rule you out; JobForge needs your answer.",
  BLOCKED: "A requirement rules you out (based on a fact you confirmed).",
};
export const FIT_TEXT: Record<string, string> = {
  STRONG: "Your experience covers the requirements directly.",
  COMPETITIVE: "No important gaps, but some requirements rest on related experience or are unanswered.",
  STRETCH: "You cover most requirements, with one or two important gaps.",
  WEAK: "Several important requirements are missing from your experience.",
  INSUFFICIENT: "Too many requirements aren't covered by your profile to judge yet.",
};
export const OPPORTUNITY_TEXT: Record<string, string> = {
  HIGH: "Pay, location and the kind of role look good for you.",
  MEDIUM: "A reasonable fit for what you're after.",
  LOW: "Pay, location or the kind of role are not what you're after.",
  UNKNOWN: "The posting doesn't say enough about pay, location or level.",
};

// The job page's first sentence for APPLY and STRETCH, from the fit counts (required items only), so it
// says how much rests on direct evidence instead of a fixed "A good match" (usability pass 2026-10-24).
export function evidenceSentence(c: Record<string, unknown>): string {
  const n = (k: string) => Number(c[k] ?? 0) || 0;
  const count = (k: string, one: string, many: string) => `${n(k)} ${n(k) === 1 ? one : many}`;
  const parts = [count("direct", "requirement matches directly", "requirements match directly")];
  if (n("transferable")) parts.push(count("transferable", "relies on related experience", "rely on related experience"));
  if (n("gap_material")) parts.push(count("gap_material", "is an important gap", "are important gaps"));
  if (n("gap_minor")) parts.push(count("gap_minor", "is a small gap", "are small gaps"));
  if (n("unknown")) parts.push(count("unknown", "is unanswered", "are unanswered"));
  return parts.length === 1 ? `${parts[0]}.` : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}.`;
}

export function headline(rec: string, c: Record<string, unknown>): string | null {
  const lead = rec === "APPLY" ? "Worth applying" : rec === "STRETCH" ? "Worth a try" : null;
  if (!lead) return null;
  const unconfirmed = Number(c["self_reported_covering"] ?? 0) > 0 ? " Some of it rests on experience you haven't confirmed in Profile." : "";
  return `${lead}: ${evidenceSentence(c)}${unconfirmed}`;
}

function years(n: unknown): string {
  const v = Number(n);
  if (!isFinite(v)) return "?";
  if (v < 1) return `${Math.max(1, Math.round(v * 12))} months`;
  return `${Math.round(v * 10) / 10} years`;
}

/** A plain sentence for what code found about one requirement match (no flag names). */
// "Pay, location and level" (E6 opportunity factors). A factor's value "unknown" is not the match
// type "unknown" ("Not in your profile"): it is either the posting not saying (salary, work mode,
// where it hires, date) or you not having set a preference. The agent's `detail` says which; these
// exact strings are pinned to evaluation/assess.py by agent/tests/test_views.py.
// Opportunity value UNKNOWN shares its key with eligibility UNKNOWN ("Check eligibility").
export const opportunityLabel = (v: string | null | undefined) => (v === "UNKNOWN" ? "Not enough info" : undefined);

export type Factor = { factor: string; value: string; detail: string };
const NOT_STATED: Record<string, string> = {
  "no salary in the posting": "The posting doesn't give a salary.",
  "work mode not stated": "The posting doesn't say remote, hybrid or on-site.",
  "no posting date": "The posting has no date.",
  UNKNOWN: "The posting doesn't say where it can hire.",
};
const NO_PREFERENCE: Record<string, string> = {
  "no work-mode preferences in the search profile": "You haven't said how you'd like to work (Remote / Hybrid / On-site in your search).",
  "no seniority preferences in the search profile": "You haven't listed levels to like or skip in your search.",
  "no role_family preferences in the search profile": "You haven't listed titles you'd especially like in your search.",
};
const LOCATION: Record<string, string> = {
  HOME_OK: "Open where you live.", US_WIDE: "Open anywhere in the US.",
  LOCATION_NOT_ELIGIBLE: "On-site, too far from your towns.", REMOTE_EXCLUDES_HOME: "Remote, but not open where you live.",
  REMOTE_ANCHORED: "Tied to an office elsewhere.",
};
const TITLE: Record<string, Record<string, string>> = {
  seniority: { favorable: "The title shows a level you'd especially like.", unfavorable: "The title shows a level you skip.",
    neutral: "The title shows no level you listed to like or skip." },
  role_family: { favorable: "The title is one you'd especially like.", unfavorable: "The title is one you'd rather not have.",
    neutral: "The title isn't one of your \"titles you'd especially like\"." },
};

export function factorText(f: Factor): { label: string; note: string } {
  if (f.value === "unknown") {
    if (NOT_STATED[f.detail]) return { label: "Not stated", note: NOT_STATED[f.detail] };
    if (NO_PREFERENCE[f.detail]) return { label: "No preference set", note: NO_PREFERENCE[f.detail] };
    return { label: "Unknown", note: f.detail };
  }
  const label = plain(f.value);
  if (f.factor === "location") return { label, note: LOCATION[f.detail.split(":")[0]] ?? f.detail };
  if (f.factor === "work_mode") return { label, note: plain(f.detail === "onsite" ? "On-site" : f.detail) };
  if (f.factor === "age") return { label, note: `Posted ${f.detail} ago.` };
  if (TITLE[f.factor]?.[f.value]) return { label, note: TITLE[f.factor][f.value] };
  return { label, note: f.detail };
}

export function matchNote(m: { match_type: string; gap_size: Record<string, unknown> | null; flags: { flag: string; [k: string]: unknown }[] }): string | null {
  const g = m.gap_size ?? {};
  if ("years_needed" in g) return `Needs ${years(g.years_needed)}; your profile shows ${years(g.years_shown ?? 0)}.`;
  if ("eligibility" in g) return "One of your eligibility facts doesn't meet this.";
  const f = new Set(m.flags.map((x) => x.flag));
  if (f.has("absence_not_evidence")) return "This certification isn't in your profile (mark your certification list complete if you don't have it).";
  if (f.has("citation_rejected")) return "The AI pointed at something that isn't in your profile; that was ignored.";
  if (f.has("model_omitted")) return "The AI didn't assess this one.";
  if (f.has("no_profile_items_model_not_called")) return "Add your experience in Profile to have this assessed.";
  if (typeof g.detail === "string") return g.detail;
  return null;
}
