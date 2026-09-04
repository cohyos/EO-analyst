import { describe, expect, it } from "vitest";
import { buildStageTimeline, stageEventColor } from "./pipelineTimeline";
import type { PipelineLastRun } from "@/types/api";

describe("buildStageTimeline", () => {
  it("returns an empty list when there is no last run", () => {
    expect(buildStageTimeline(null)).toEqual([]);
    expect(buildStageTimeline(undefined)).toEqual([]);
  });

  it("orders known stages by the canonical STAGE_ORDER, not object insertion order", () => {
    const lastRun: PipelineLastRun = {
      started_at: "2026-09-03T20:00:00+03:00",
      finished_at: "2026-09-04T06:00:00+03:00",
      state: "done",
      stages: {
        // Deliberately out of pipeline order in the source object.
        report: { events: 1, last_event: "done", last_at: "2026-09-04T06:00:00+03:00" },
        ingest: { events: 12, last_event: "done", last_at: "2026-09-03T20:12:00+03:00" },
        triage: { events: 40, last_event: "done", last_at: "2026-09-03T21:20:00+03:00" },
        classify: { events: 40, last_event: "done", last_at: "2026-09-03T20:55:00+03:00" },
      },
    };
    const entries = buildStageTimeline(lastRun);
    expect(entries.map((e) => e.key)).toEqual(["ingest", "classify", "triage", "report"]);
  });

  it("approximates each stage's start as the previous stage's last_at, and the first stage's start as the run's started_at", () => {
    const lastRun: PipelineLastRun = {
      started_at: "2026-09-03T20:00:00+03:00",
      finished_at: "2026-09-04T06:00:00+03:00",
      state: "done",
      stages: {
        ingest: { events: 12, last_event: "done", last_at: "2026-09-03T20:12:00+03:00" },
        classify: { events: 40, last_event: "done", last_at: "2026-09-03T20:55:00+03:00" },
      },
    };
    const entries = buildStageTimeline(lastRun);
    expect(entries[0].start).toBe("2026-09-03T20:00:00+03:00");
    expect(entries[0].end).toBe("2026-09-03T20:12:00+03:00");
    // Second stage's start is the first stage's end.
    expect(entries[1].start).toBe("2026-09-03T20:12:00+03:00");
    expect(entries[1].end).toBe("2026-09-03T20:55:00+03:00");
  });

  it("appends stage keys not in the canonical STAGE_ORDER, sorted by their own last_at", () => {
    const lastRun: PipelineLastRun = {
      started_at: "2026-09-03T20:00:00+03:00",
      finished_at: "2026-09-04T06:00:00+03:00",
      state: "done",
      stages: {
        ingest: { events: 12, last_event: "done", last_at: "2026-09-03T20:12:00+03:00" },
        dedup: { events: 4, last_event: "done", last_at: "2026-09-03T20:18:00+03:00" },
        fetch: { events: 1, last_event: "done", last_at: "2026-09-03T20:05:00+03:00" },
      },
    };
    const entries = buildStageTimeline(lastRun);
    // "ingest" is canonical and comes first; "fetch"/"dedup" aren't in
    // STAGE_ORDER and are ordered chronologically by last_at after it.
    expect(entries.map((e) => e.key)).toEqual(["ingest", "fetch", "dedup"]);
  });

  it("gives each entry a Hebrew label, falling back to the raw key for unknown stages", () => {
    const lastRun: PipelineLastRun = {
      started_at: null,
      finished_at: null,
      state: "done",
      stages: {
        triage: { events: 1, last_event: "done", last_at: "2026-09-03T21:20:00+03:00" },
        mystery_stage: { events: 1, last_event: "done", last_at: "2026-09-03T21:25:00+03:00" },
      },
    };
    const entries = buildStageTimeline(lastRun);
    expect(entries.find((e) => e.key === "triage")?.label).toBe("מיון (Triage)");
    expect(entries.find((e) => e.key === "mystery_stage")?.label).toBe("mystery_stage");
  });
});

describe("stageEventColor", () => {
  it("maps known last_event values", () => {
    expect(stageEventColor("done")).toBe("done");
    expect(stageEventColor("skipped")).toBe("skipped");
    expect(stageEventColor("error")).toBe("error");
    expect(stageEventColor("failed")).toBe("error");
    expect(stageEventColor("running")).toBe("running");
  });

  it("falls back to unknown for null/unrecognized values", () => {
    expect(stageEventColor(null)).toBe("unknown");
    expect(stageEventColor(undefined)).toBe("unknown");
    expect(stageEventColor("something_else")).toBe("unknown");
  });
});
