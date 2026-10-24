import { describe, expect, it } from "vitest";
import { latestGate } from "./latest";

describe("latestGate", () => {
  it("lets only the newest request apply its answer, whatever order answers arrive in", async () => {
    const gate = latestGate();
    const shown: string[] = [];
    const load = (answer: string, ms: number) => {
      const ticket = gate.begin();
      return new Promise<void>((done) => setTimeout(() => {
        if (gate.isCurrent(ticket)) shown.push(answer);
        done();
      }, ms));
    };
    // "Filtered out" (slow) clicked first, then "For you" (fast): the slow answer lands last.
    await Promise.all([load("filtered-out", 30), load("for-you", 5)]);
    expect(shown).toEqual(["for-you"]);
  });
});
