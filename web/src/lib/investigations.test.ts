import { describe, expect, it } from "vitest";
import { ALL_OUTCOME_REASONS, outcomeLabel, outcomeTone } from "./investigations";

// 2026-09-06 (job 86 regression fix, point 5): `off_topic` is a distinct investigation outcome
// from `not_found` -- job 86's investigation `finish`'d with outcome="found"/confidence=0.9 on an
// answer (Elbit MOSP 5000) unrelated to the actual question (US Air Force / Reaper / Iran). The
// taxonomy must carry a Hebrew label + tone for it so the UI can show it distinctly.
describe("off_topic outcome taxonomy (job 86 regression fix)", () => {
  it("is included in the enumerated outcome reasons", () => {
    expect(ALL_OUTCOME_REASONS).toContain("off_topic");
  });

  it("has a Hebrew label distinct from not_found", () => {
    expect(outcomeLabel("off_topic")).not.toBe("—");
    expect(outcomeLabel("off_topic")).not.toBe(outcomeLabel("not_found"));
  });

  it("has a tone class", () => {
    expect(outcomeTone("off_topic")).toMatch(/\S/);
  });

  it("falls back gracefully for an unknown outcome", () => {
    expect(outcomeLabel("something_new")).toBe("something_new");
    expect(outcomeTone("something_new")).toBe("text-fg-dim bg-bg-sunken");
  });
});
