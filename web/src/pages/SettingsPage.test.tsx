import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Job, JobState } from "@/types/api";

// W20 (docs/REVIEW_2026-09-06_evening.md): the jobs table used to show only the generic `kind`
// -- these tests cover the added subject/duration/status-colour columns, scoped to this round's
// file ownership (SettingsPage.tsx's jobs table only; the rest of the page's many other queries
// are mocked just enough to let it render without throwing).

const getJobs = vi.fn();
const getSettings = vi.fn();
const getLlmProviders = vi.fn();
const getLlmCalls = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getJobs: (...args: unknown[]) => getJobs(...args),
    postJobCancel: vi.fn(),
    postRun: vi.fn(),
    getSettings: (...args: unknown[]) => getSettings(...args),
    putSettings: vi.fn(),
    getLlmProviders: (...args: unknown[]) => getLlmProviders(...args),
    putLlmSettings: vi.fn(),
    getLlmCalls: (...args: unknown[]) => getLlmCalls(...args),
  },
}));

import { SettingsPage } from "./SettingsPage";

function makeJob(over: Partial<Job> = {}): Job {
  return {
    id: 118,
    kind: "bd_report",
    payload: { territory: "DE", lookback_days: 90 },
    state: "done" as JobState,
    priority: 5,
    attempts: 1,
    not_before: null,
    started_at: "2026-09-06T20:46:19.696059+03:00",
    finished_at: "2026-09-06T20:46:20.083028+03:00",
    error: null,
    result: null,
    created_at: "2026-09-06T20:46:19.696059+03:00",
    updated_at: "2026-09-06T20:46:20.083028+03:00",
    subject_he: "גרמניה",
    ...over,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <SettingsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getJobs.mockReset();
  getSettings.mockReset();
  getLlmProviders.mockReset();
  getLlmCalls.mockReset();
  getSettings.mockResolvedValue({ yaml: "" });
  getLlmProviders.mockResolvedValue({
    mode: "local",
    interactive_default: "ollama",
    allow_cloud: false,
    providers: [],
    chains: {},
  });
  getLlmCalls.mockResolvedValue({ totals: { calls: 0, cloud_calls: 0, fallbacks: 0, est_cost_usd: 0 } });
  getJobs.mockResolvedValue([]);
});

describe("SettingsPage jobs table (W20)", () => {
  it("shows an empty state when there are no jobs", async () => {
    renderPage();
    expect(await screen.findByText("אין עבודות")).toBeInTheDocument();
  });

  it("shows the derived subject next to the job kind", async () => {
    getJobs.mockResolvedValue([makeJob()]);
    renderPage();
    expect(await screen.findByText("bd_report")).toBeInTheDocument();
    expect(screen.getByText("גרמניה")).toBeInTheDocument();
  });

  it("falls back to an em dash when a job has no derived subject", async () => {
    getJobs.mockResolvedValue([makeJob({ id: 200, kind: "ingest", payload: { mode: "poll" }, subject_he: null })]);
    renderPage();
    await screen.findByText("ingest");
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("shows a computed duration between started_at and finished_at", async () => {
    getJobs.mockResolvedValue([
      makeJob({
        started_at: "2026-09-06T20:46:00+03:00",
        finished_at: "2026-09-06T20:46:05+03:00",
      }),
    ]);
    renderPage();
    await screen.findByText("bd_report");
    expect(screen.getByText("0:05")).toBeInTheDocument();
  });

  it("colours a failed job's state distinctly from a done one", async () => {
    getJobs.mockResolvedValue([
      makeJob({ id: 1, state: "done" as JobState }),
      makeJob({ id: 2, state: "failed" as JobState, error: "boom", subject_he: null }),
    ]);
    renderPage();
    const done = await screen.findByText("הושלם");
    // the failed row's state cell also holds a sibling error-detail span, so its own text node
    // isn't independently queryable by exact match -- locate it via the error title instead and
    // walk up to the shared <td> that carries both the label and the colour class.
    const failedCell = (await screen.findByTitle("boom")).closest("td")!;
    expect(done.className).toContain("text-ok");
    expect(failedCell.className).toContain("text-danger");
    expect(failedCell.textContent).toContain("נכשל");
  });
});
