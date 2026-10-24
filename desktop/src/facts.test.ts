import { describe, expect, it } from "vitest";
import { factSummary, fieldLabel, generatedStatement, optionLabel, ownWords } from "./facts";

describe("factSummary", () => {
  it("never says true or false", () => {
    const a = { type: "availability", holidays: false, night_shifts: true, on_call: true, weekends: true };
    expect(factSummary("availability", a)).toBe("OK with on-call, weekends and night shifts · not holidays");
    expect(factSummary("work_authorization", { us_citizen: true, authorized_to_work_us: true })).toBe("US citizen · allowed to work in the US");
    expect(factSummary("sponsorship", { needs_sponsorship: false })).toBe("Doesn't need visa sponsorship");
    expect(factSummary("license", { name: "CDL Class A", active: false })).toBe("CDL Class A license, not active");
    expect(factSummary("program_status", { active_duty: false, student: true, veteran: false })).toBe("Student");
    for (const s of [factSummary("availability", a), factSummary("sponsorship", { needs_sponsorship: true })])
      expect(s).not.toMatch(/true|false/);
  });
  it("reads clearance and residency as a sentence", () => {
    expect(factSummary("clearance", { level: "top_secret", status: "expired", polygraph: "full_scope", last_active_year: 2019 }))
      .toBe("Top Secret clearance, expired (last active 2019) · full scope polygraph");
    expect(factSummary("clearance", { level: "none", status: "none" })).toBe("No clearance");
    expect(factSummary("residency", { city: "Springfield", state: "Florida", country: "US", time_zone: "eastern", willing_to_relocate: false }))
      .toBe("Springfield, Florida, US · Eastern time · not willing to relocate");
  });
  it("falls back to labelled answers for a type it doesn't know", () => {
    expect(factSummary("something_new", { type: "something_new", is_ok: true, size: 3 })).toBe("Is ok: yes · Size: 3");
  });
});

describe("ownWords", () => {
  it("hides statements generated from the answers, old style and new", () => {
    const a = { type: "license", active: true, name: "Forklift" };
    expect(ownWords("License: active: true, name: Forklift", "license", a)).toBeNull();
    expect(ownWords(generatedStatement("license", a), "license", a)).toBeNull();
    expect(ownWords("Class C, renewed 2025", "license", a)).toBe("Class C, renewed 2025");
  });
});

describe("labels", () => {
  it("names fields and options in plain words", () => {
    expect(fieldLabel("other_time_zone_hours")).toBe("Hours set in another time zone");
    expect(fieldLabel("brand_new_field")).toBe("Brand new field");
    expect(optionLabel("ts_sci")).toBe("TS/SCI");
  });
});
