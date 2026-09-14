import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { PipelineLastRun, PipelineStageInfo } from "@/types/api";
import { PipelineReplayTimeline } from "./PipelineReplayTimeline";

function stage(overrides: Partial<PipelineStageInfo> = {}): PipelineStageInfo {
  return { status: "done", minutes: 1, last_event: "done", last_at: null, ...overrides };
}

describe("PipelineReplayTimeline", () => {
  it("distinguishes exhausted time and paused resources from skipped work", () => {
    render(<PipelineReplayTimeline lastRun={{
      started_at: null, finished_at: null, state: "partial",
      stages: {
        tenders: stage({ status: "partial", last_event: "deadline", minutes: 15 }),
        embed_dedup: stage({ status: "deferred", last_event: "deferred", minutes: 0 }),
      },
    }} />);
    expect(screen.getByText("חלקי")).toBeInTheDocument();
    expect(screen.getByText("מושהה")).toBeInTheDocument();
    expect(screen.queryByText("דולג")).not.toBeInTheDocument();
    expect(screen.getAllByTitle(/תקציב הזמן הסתיים/).length).toBeGreaterThan(0);
  });
  it("renders nothing when there is no last run", () => {
    const { container } = render(<PipelineReplayTimeline lastRun={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  // UI QA fix (2026-09-08, docs/qa/content_review/UI-TIMELINE.md): the legend used to write
  // durations as "N דק׳" -- a geresh glyph that, in the app's font stack, reads like a yod
  // ("14.5 דקי"). Every duration now spells out "דקות" instead.
  it("spells out stage durations as 'X דקות', not the geresh-abbreviated 'דק׳'", () => {
    const lastRun: PipelineLastRun = {
      started_at: null,
      finished_at: null,
      state: "done",
      stages: { ingest: stage({ minutes: 14.5 }) },
    };
    render(<PipelineReplayTimeline lastRun={lastRun} />);
    expect(screen.getByText("14.5 דקות")).toBeInTheDocument();
    expect(screen.queryByText(/דק׳/)).not.toBeInTheDocument();
  });

  // The stage's own accessible name (aria-label) must say which stage failed, and its error
  // detail (agent/eoa/orchestrator/jobs.py's captured exception text) must be surfaced somewhere
  // an analyst can read it -- the legend row's title tooltip.
  it("marks a failed stage with a visible 'נכשל' badge, an aria-label naming it, and its error in the tooltip", () => {
    const lastRun: PipelineLastRun = {
      started_at: null,
      finished_at: null,
      state: "partial",
      stages: {
        ingest: stage({ status: "done" }),
        classify: stage({
          status: "failed",
          minutes: 0.4,
          detail: { error: "requests.exceptions.ConnectionError: boom" },
        }),
      },
    };
    render(<PipelineReplayTimeline lastRun={lastRun} />);

    // Visible failure badge next to the failed stage's label.
    expect(screen.getByText("נכשל")).toBeInTheDocument();

    // The failed stage's accessible name carries status and error text.
    const failedLabel = screen.getByLabelText(/סיווג — נכשל — requests\.exceptions\.ConnectionError: boom/);
    expect(failedLabel).toBeInTheDocument();

    // The legend row's tooltip (title attribute) also carries the error text, for a mouse user
    // hovering without a screen reader.
    const failedRow = failedLabel.closest("li");
    expect(failedRow).not.toBeNull();
    expect(failedRow).toHaveAttribute("title", expect.stringContaining("requests.exceptions.ConnectionError: boom"));
  });

  it("does not show a failure badge or error text for a stage that succeeded", () => {
    const lastRun: PipelineLastRun = {
      started_at: null,
      finished_at: null,
      state: "done",
      stages: { ingest: stage({ status: "done", minutes: 3 }) },
    };
    render(<PipelineReplayTimeline lastRun={lastRun} />);
    expect(screen.queryByText("נכשל")).not.toBeInTheDocument();
  });

  it("gives every legend cell min-w-0 so a long label truncates instead of overflowing its grid track", () => {
    const lastRun: PipelineLastRun = {
      started_at: null,
      finished_at: null,
      state: "done",
      stages: { dedup_xlang: stage({ minutes: 2 }) },
    };
    render(<PipelineReplayTimeline lastRun={lastRun} />);
    const label = screen.getByText("זיהוי כפילויות רב-לשוני");
    expect(label.className).toContain("truncate");
    expect(label.closest("li")?.className).toContain("min-w-0");
  });
});
