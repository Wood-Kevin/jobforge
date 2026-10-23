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
  HIGH: "Great", MEDIUM: "OK", LOW: "Poor",
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
  physical: "Physical", travel: "Travel", license: "License", program_status: "Military or student program",
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
  COMPETITIVE: "You cover the requirements, some through related experience.",
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

function years(n: unknown): string {
  const v = Number(n);
  if (!isFinite(v)) return "?";
  if (v < 1) return `${Math.max(1, Math.round(v * 12))} months`;
  return `${Math.round(v * 10) / 10} years`;
}

/** A plain sentence for what code found about one requirement match (no flag names). */
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
