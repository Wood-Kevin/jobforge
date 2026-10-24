import { describe, expect, it } from "vitest";
import { factorText, opportunityLabel, plain } from "./labels";

describe("factorText", () => {
  it("tells a posting that doesn't say apart from a preference you haven't set", () => {
    expect(factorText({ factor: "pay", value: "unknown", detail: "no salary in the posting" }))
      .toEqual({ label: "Not stated", note: "The posting doesn't give a salary." });
    expect(factorText({ factor: "location", value: "unknown", detail: "UNKNOWN" }).label).toBe("Not stated");
    expect(factorText({ factor: "work_mode", value: "unknown", detail: "no work-mode preferences in the search profile" }).label)
      .toBe("No preference set");
    expect(factorText({ factor: "role_family", value: "unknown", detail: "no role_family preferences in the search profile" }).label)
      .toBe("No preference set");
  });
  it("never shows the requirement wording 'Not in your profile'", () => {
    for (const detail of ["no salary in the posting", "work mode not stated", "no posting date", "UNKNOWN", "something new"])
      expect(factorText({ factor: "x", value: "unknown", detail }).label).not.toBe("Not in your profile");
  });
  it("explains the known values in plain words", () => {
    expect(factorText({ factor: "location", value: "favorable", detail: "US_WIDE" })).toEqual({ label: "Good", note: "Open anywhere in the US." });
    expect(factorText({ factor: "location", value: "unfavorable", detail: "REMOTE_ANCHORED:office in NYC" }).note).toBe("Tied to an office elsewhere.");
    expect(factorText({ factor: "work_mode", value: "favorable", detail: "remote" })).toEqual({ label: "Good", note: "Remote" });
    expect(factorText({ factor: "age", value: "neutral", detail: "12 days" }).note).toBe("Posted 12 days ago.");
    expect(factorText({ factor: "seniority", value: "neutral", detail: "title matches no listed pattern" }).note)
      .toBe("The title shows no level you listed to like or skip.");
  });
  it("words opportunity UNKNOWN apart from eligibility UNKNOWN", () => {
    expect(opportunityLabel("UNKNOWN")).toBe("Not enough info");
    expect(plain("UNKNOWN")).toBe("Check eligibility");
    expect(opportunityLabel("MEDIUM")).toBeUndefined();
  });
});
