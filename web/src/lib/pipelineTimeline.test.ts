import { describe, expect, it } from "vitest";
import { buildStageTimeline, formatStageMinutes, stageLabelHe, STAGE_ORDER } from "./pipelineTimeline";
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

  it("gives each entry a Hebrew label, falling back to a humanised form for unknown stages", () => {
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
    // Q5-5 (docs/qa/findings_Q5_r1.md): a stage key with no Hebrew label yet used to render
    // as the raw snake_case key verbatim -- now falls back to a humanised form instead.
    expect(entries.find((e) => e.key === "mystery_stage")?.label).toBe("mystery stage");
  });

  it("carries a failed stage's error detail through as `error`, and leaves it null otherwise", () => {
    // UI QA fix (2026-09-08, docs/qa/content_review/UI-TIMELINE.md): the legend used to give no
    // hint which stage failed or why. `detail.error` comes from agent/eoa/orchestrator/jobs.py's
    // `except` handler (`{"error": str(exc)[:300], "minutes": ...}`) via
    // agent/eoa/api/services.py's `_stage_timeline_from_log`.
    const lastRun: PipelineLastRun = {
      started_at: null,
      finished_at: null,
      state: "partial",
      stages: {
        ingest: stage({ status: "done" }),
        classify: stage({
          status: "failed",
          minutes: 0.4,
          detail: { error: "requests.exceptions.ConnectionError: ..." },
        }),
      },
    };
    const entries = buildStageTimeline(lastRun);
    expect(entries.find((e) => e.key === "ingest")?.error).toBeNull();
    expect(entries.find((e) => e.key === "classify")?.error).toBe(
      "requests.exceptions.ConnectionError: ...",
    );
  });

  it("treats a missing, empty, or non-string detail.error as no error", () => {
    const lastRun: PipelineLastRun = {
      started_at: null,
      finished_at: null,
      state: "done",
      stages: {
        ingest: stage({ detail: undefined }),
        classify: stage({ detail: {} }),
        triage: stage({ detail: { error: "" } }),
      },
    };
    const entries = buildStageTimeline(lastRun);
    expect(entries.find((e) => e.key === "ingest")?.error).toBeNull();
    expect(entries.find((e) => e.key === "classify")?.error).toBeNull();
    expect(entries.find((e) => e.key === "triage")?.error).toBeNull();
  });

  it("has a Hebrew label for every stage in the canonical STAGE_ORDER, including post_tenders_catchup", () => {
    // Q5-5: agent/eoa/orchestrator/jobs.py's STAGE_ORDER runs "post_tenders_catchup" between
    // "tenders" and "report" -- it was missing from STAGE_LABEL_HE, so the morning replay
    // timeline showed the raw English key for that stage.
    for (const key of STAGE_ORDER) {
      expect(stageLabelHe(key)).not.toBe(key);
    }
  });
});

describe("stageLabelHe", () => {
  it("returns the known Hebrew label for a canonical stage", () => {
    expect(stageLabelHe("post_tenders_catchup")).toBe("השלמת מכרזים");
  });

  it("humanises (replaces underscores with spaces) an unknown stage key instead of returning it raw", () => {
    expect(stageLabelHe("some_new_stage")).toBe("some new stage");
  });
});

describe("formatStageMinutes", () => {
  // UI QA fix (2026-09-08, docs/qa/content_review/UI-TIMELINE.md): "דק׳" (the geresh abbreviation)
  // rendered in the app's font stack as a glyph indistinguishable from a yod ("14.5 דקי"). Every
  // duration in the timeline now spells the word out instead.
  it("spells out the word 'דקות' instead of using the geresh abbreviation", () => {
    expect(formatStageMinutes(14.5)).toBe("14.5 דקות");
  });

  it("spells it out for sub-one-minute durations too", () => {
    expect(formatStageMinutes(0.4)).toBe("0.4 דקות");
  });
});
