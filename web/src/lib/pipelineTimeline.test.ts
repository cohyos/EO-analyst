import { describe, expect, it } from "vitest";
import { buildStageTimeline } from "./pipelineTimeline";
import type { PipelineLastRun, PipelineStageInfo } from "@/types/api";

function stage(overrides: Partial<PipelineStageInfo> = {}): PipelineStageInfo {
  return { status: "done", minutes: 1, last_event: "done", last_at: null, ...overrides };
}

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
        report: stage({ minutes: 8 }),
        ingest: stage({ minutes: 12 }),
        triage: stage({ minutes: 25 }),
        classify: stage({ minutes: 37 }),
      },
    };
    const entries = buildStageTimeline(lastRun);
    expect(entries.map((e) => e.key)).toEqual(["ingest", "classify", "triage", "report"]);
  });

  it("carries each stage's own status and minutes through untouched", () => {
    const lastRun: PipelineLastRun = {
      started_at: "2026-09-03T20:00:00+03:00",
      finished_at: "2026-09-04T06:00:00+03:00",
      state: "partial",
      stages: {
        ingest: stage({ status: "done", minutes: 12 }),
        classify: stage({ status: "failed", minutes: 1.1 }),
        deep_search: stage({ status: "skipped", minutes: null }),
      },
    };
    const entries = buildStageTimeline(lastRun);
    expect(entries.find((e) => e.key === "ingest")).toMatchObject({ status: "done", minutes: 12 });
    expect(entries.find((e) => e.key === "classify")).toMatchObject({ status: "failed", minutes: 1.1 });
    expect(entries.find((e) => e.key === "deep_search")).toMatchObject({ status: "skipped", minutes: null });
  });

  it("appends stage keys not in the canonical STAGE_ORDER, sorted alphabetically", () => {
    const lastRun: PipelineLastRun = {
      started_at: "2026-09-03T20:00:00+03:00",
      finished_at: "2026-09-04T06:00:00+03:00",
      state: "done",
      stages: {
        ingest: stage(),
        zzz_unknown: stage(),
        aaa_unknown: stage(),
      },
    };
    const entries = buildStageTimeline(lastRun);
    // "ingest" is canonical and comes first; unrecognized keys are sorted after it.
    expect(entries.map((e) => e.key)).toEqual(["ingest", "aaa_unknown", "zzz_unknown"]);
  });

  it("gives each entry a Hebrew label, falling back to the raw key for unknown stages", () => {
    const lastRun: PipelineLastRun = {
      started_at: null,
      finished_at: null,
      state: "done",
      stages: {
        triage: stage(),
        mystery_stage: stage(),
      },
    };
    const entries = buildStageTimeline(lastRun);
    expect(entries.find((e) => e.key === "triage")?.label).toBe("מיון (Triage)");
    expect(entries.find((e) => e.key === "mystery_stage")?.label).toBe("mystery_stage");
  });
});
