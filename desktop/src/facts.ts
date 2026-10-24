// Eligibility facts in plain words (UX redesign step 3: the profile showed "Availability: holidays: true, night
// shifts: true, ..." and then the same list again in grey). A fact's attributes are what JobForge checks; this
// only says them as a person would. Field names come from the agent's schema (GET /v1/profile/schema).

export const FIELD_LABEL: Record<string, string> = {
  us_citizen: "US citizen", authorized_to_work_us: "Allowed to work in the US", needs_sponsorship: "Needs visa sponsorship",
  level: "Clearance level", status: "Status", polygraph: "Polygraph", last_active_year: "Last active (year)",
  country: "Country", state: "State", city: "City or town", willing_to_relocate: "Willing to relocate", time_zone: "Time zone",
  max_lift_lbs: "Most you can lift (lbs)", can_stand_hours: "Hours you can stand", max_travel_percent: "Most travel you'd do (%)",
  name: "License", active: "Currently active", active_duty: "Active duty", veteran: "Veteran", student: "Student",
  on_call: "On-call", weekends: "Weekends", holidays: "Holidays", night_shifts: "Night shifts", rotating_shifts: "Rotating shifts",
  other_time_zone_hours: "Hours set in another time zone",
};

export const OPTION_LABEL: Record<string, string> = {
  none: "None", confidential: "Confidential", secret: "Secret", top_secret: "Top Secret", ts_sci: "TS/SCI",
  active: "Active", current: "Current", inactive: "Inactive", expired: "Expired", ci: "Counterintelligence", full_scope: "Full scope",
  eastern: "Eastern", central: "Central", mountain: "Mountain", pacific: "Pacific", alaska: "Alaska", hawaii: "Hawaii",
};

export const fieldLabel = (f: string) => FIELD_LABEL[f] ?? cap(f.replace(/_/g, " "));
export const optionLabel = (o: string) => OPTION_LABEL[o] ?? cap(o.replace(/_/g, " "));
const cap = (s: string) => s.replace(/^./, (c) => c.toUpperCase());
const low = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

type Attrs = Record<string, unknown>;
const has = (a: Attrs, k: string) => a[k] !== undefined && a[k] !== null && a[k] !== "";

/** One fact's answers as a short sentence: "Top Secret clearance, active · full scope polygraph". */
export function factSummary(type: string, a: Attrs): string {
  const yes = (keys: string[]) => keys.filter((k) => a[k] === true);
  const no = (keys: string[]) => keys.filter((k) => a[k] === false);
  const parts: string[] = [];
  switch (type) {
    case "work_authorization":
      if (has(a, "us_citizen")) parts.push(a.us_citizen ? "US citizen" : "Not a US citizen");
      if (has(a, "authorized_to_work_us")) parts.push(a.authorized_to_work_us ? "allowed to work in the US" : "not allowed to work in the US");
      break;
    case "sponsorship":
      if (has(a, "needs_sponsorship")) parts.push(a.needs_sponsorship ? "Needs visa sponsorship" : "Doesn't need visa sponsorship");
      break;
    case "clearance": {
      const level = has(a, "level") ? optionLabel(String(a.level)) : null;
      if (level && a.level !== "none") {
        let s = `${level} clearance`;
        if (has(a, "status")) s += `, ${low(optionLabel(String(a.status)))}`;
        if (has(a, "last_active_year")) s += ` (last active ${a.last_active_year})`;
        parts.push(s);
      } else if (level) parts.push("No clearance");
      if (has(a, "polygraph")) parts.push(a.polygraph === "none" ? "no polygraph" : `${low(optionLabel(String(a.polygraph)))} polygraph`);
      break;
    }
    case "residency": {
      const place = ["city", "state", "country"].filter((k) => has(a, k)).map((k) => String(a[k]));
      if (place.length) parts.push(place.join(", "));
      if (has(a, "time_zone")) parts.push(`${optionLabel(String(a.time_zone))} time`);
      if (has(a, "willing_to_relocate")) parts.push(a.willing_to_relocate ? "willing to relocate" : "not willing to relocate");
      break;
    }
    case "physical":
      if (has(a, "max_lift_lbs")) parts.push(`Can lift up to ${a.max_lift_lbs} lbs`);
      if (has(a, "can_stand_hours")) parts.push(`can stand ${a.can_stand_hours} hours`);
      break;
    case "travel":
      if (has(a, "max_travel_percent")) parts.push(`Up to ${a.max_travel_percent}% travel`);
      break;
    case "license":
      if (has(a, "name")) parts.push(`${a.name} license${has(a, "active") ? (a.active ? ", active" : ", not active") : ""}`);
      break;
    case "program_status": {
      const on = yes(["active_duty", "veteran", "student"]).map(fieldLabel);
      parts.push(on.length ? on.join(", ") : "Not active duty, a veteran or a student");
      break;
    }
    case "availability": {
      const keys = ["on_call", "weekends", "holidays", "night_shifts", "rotating_shifts", "other_time_zone_hours"];
      const ok = yes(keys).map((k) => low(fieldLabel(k)));
      const not = no(keys).map((k) => low(fieldLabel(k)));
      if (ok.length) parts.push(`OK with ${list(ok)}`);
      if (not.length) parts.push(`not ${list(not)}`);
      break;
    }
  }
  if (parts.length) return cap(parts.join(" · "));
  // A type this window doesn't know yet: its answers, readably.
  return answers(a).map(([k, v]) => `${fieldLabel(k)}: ${typeof v === "boolean" ? (v ? "yes" : "no") : String(v)}`).join(" · ");
}

function list(xs: string[]): string {
  return xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
}

/** The heading a fact type shows under. */
export const TYPE_TITLE: Record<string, string> = {
  work_authorization: "Work authorization", sponsorship: "Visa sponsorship", clearance: "Security clearance", residency: "Where you live",
  physical: "Physical", travel: "Travel", license: "License", program_status: "Military or student program", availability: "Hours and availability",
};
export const typeTitle = (t: string) => TYPE_TITLE[t] ?? cap(t.replace(/_/g, " "));

const answers = (a: Attrs) => Object.entries(a).filter(([k]) => k !== "type" && k !== "verify_note");

/** What saving with an empty description stores: "Hours and availability: OK with holidays and weekends". */
export function generatedStatement(type: string, a: Attrs): string {
  return `${typeTitle(type)}: ${factSummary(type, a)}`;
}

/** The person's own words for a fact, or null when the statement was generated from the answers, now or by
 * the window before this pass ("Availability: holidays: true, ..."): those say nothing the summary doesn't. */
export function ownWords(statement: string, type: string, a: Attrs): string | null {
  const s = statement.trim();
  const legacy = `${cap(type.replace(/_/g, " "))}: ${answers(a).map(([k, v]) => `${k.replace(/_/g, " ")}: ${v}`).join(", ")}`;
  return !s || s === legacy || s === generatedStatement(type, a) ? null : s;
}
