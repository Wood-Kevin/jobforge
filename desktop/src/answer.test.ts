import { describe, expect, it } from "vitest";
import { guessFactType } from "./pages/Answer";

describe("guessFactType", () => {
  it("routes Nash's misread lines to the right fact, not the model's wrong type", () => {
    expect(guessFactType("Remote in Eastern US (required)", "work_authorization")).toBe("residency");
    expect(guessFactType("Located in US, eastern timezone (remote)", "work_authorization")).toBe("residency");
    expect(guessFactType("US Time (Eastern): Friday - Tuesday, 8A - 5P", "work_authorization")).toBe("availability");
    expect(guessFactType("Participate in an on-call rotation as needed.", "work_authorization")).toBe("availability");
    expect(guessFactType("This role includes rotating weekend support coverage", "work_authorization")).toBe("availability");
  });
  it("falls back to the model's type", () => {
    expect(guessFactType("Must hold a valid CDL", "license")).toBe("license");
  });
});
