import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { MorningResponse } from "@/types/api";

const getMorning = vi.fn();
const postClarificationAnswer = vi.fn();
const getTenders = vi.fn();
const getTenderForecasts = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getMorning: (...args: unknown[]) => getMorning(...args),
    postClarificationAnswer: (...args: unknown[]) => postClarificationAnswer(...args),
    getReportFileUrl: (id: number, fmt: string) => `/api/reports/${id}/file?fmt=${fmt}`,
    getTenders: (...args: unknown[]) => getTenders(...args),
    getTenderForecasts: (...args: unknown[]) => getTenderForecasts(...args),
  },
  USE_MOCKS: false,
}));

import { MorningPage } from "./MorningPage";

function renderMorningPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <MorningPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getMorning.mockReset();
  postClarificationAnswer.mockReset();
  getTenders.mockReset();
  getTenderForecasts.mockReset();
  // Most tests below don't care about the tenders tile -- default it to an
  // empty, non-hanging response so `MorningPage`'s unconditional tenders
  // queries never leave those tests waiting on unresolved promises.
  getTenders.mockResolvedValue([]);
  getTenderForecasts.mockResolvedValue([]);
});

describe("MorningPage against an empty/partial backend", () => {
  it("renders without throwing when report, headlines, open_points and night_summary are all null/empty", async () => {
    // Mirrors the real crash: an empty database with no nightly run yet —
    // GET /api/morning returning report: null, night_summary: null.
    const payload = {
      report: null,
      headlines: [],
      open_points: [],
      night_summary: null,
    } as unknown as MorningResponse;
    getMorning.mockResolvedValue(payload);

    renderMorningPage();

    expect(await screen.findByText("אין ריצה לילית עדיין")).toBeInTheDocument();
    expect(screen.getByText("אין פריטים")).toBeInTheDocument();
    expect(screen.getByText("אין נקודות פתוחות")).toBeInTheDocument();
    // Zero-filled stat tiles render "0", not a crash.
    expect(screen.getAllByText("0").length).toBeGreaterThan(0);
  });

  it("renders without throwing given an explicit all-zero night_summary (the fixed backend contract)", async () => {
    const payload: MorningResponse = {
      report: null,
      headlines: [],
      open_points: [],
      night_summary: {
        items_ingested: 0,
        classified: 0,
        red: 0,
        orange: 0,
        deep_searches: 0,
        duration_min: 0,
        errors: 0,
        state: "none",
        tenders_open: 0,
        tenders_unknown: 0,
        new_forecasts: 0,
      },
      recent_errors: [],
    };
    getMorning.mockResolvedValue(payload);

    renderMorningPage();

    expect(await screen.findByText("אין ריצה לילית עדיין")).toBeInTheDocument();
    expect(screen.getByText("0 דק׳")).toBeInTheDocument();
  });

  it("renders a full payload without crashing (sanity check for the happy path)", async () => {
    const payload: MorningResponse = {
      report: null,
      headlines: [
        {
          item_id: 1,
          title: "כותרת בדיקה",
          level: "red",
          summary_he: "תקציר",
          url: "https://example.test/1",
        },
      ],
      open_points: [
        { id: 1, question: "שאלה פתוחה?", options: ["כן", "לא"], answer: null, assumed: false },
      ],
      night_summary: {
        items_ingested: 12,
        classified: 10,
        red: 2,
        orange: 3,
        deep_searches: 1,
        duration_min: 45,
        errors: 0,
        state: "done",
        tenders_open: 0,
        tenders_unknown: 0,
        new_forecasts: 0,
      },
      recent_errors: [],
    };
    getMorning.mockResolvedValue(payload);

    renderMorningPage();

    expect(await screen.findByText("כותרת בדיקה")).toBeInTheDocument();
    expect(screen.getByText("שאלה פתוחה?")).toBeInTheDocument();
  });
});

describe("MorningPage KPI cards (U2)", () => {
  const fullNightSummary = {
    items_ingested: 50,
    classified: 45,
    red: 6,
    orange: 11,
    deep_searches: 2,
    duration_min: null,
    errors: 3,
    state: "done" as const,
    tenders_open: 6,
    tenders_unknown: 1,
    new_forecasts: 3,
  };

  it("every KPI card navigates to its filtered view", async () => {
    getMorning.mockResolvedValue({
      report: null,
      headlines: [],
      open_points: [],
      night_summary: fullNightSummary,
      recent_errors: [],
    });
    renderMorningPage();
    await screen.findByText("50");

    expect(screen.getByRole("link", { name: /פריטים שנקלטו/ })).toHaveAttribute(
      "href",
      "/feed?since=24h",
    );
    expect(screen.getByRole("link", { name: /פריטים קריטיים/ })).toHaveAttribute(
      "href",
      "/feed?level=red",
    );
    expect(screen.getByRole("link", { name: /פריטים חשובים/ })).toHaveAttribute(
      "href",
      "/feed?level=orange",
    );
    expect(screen.getByRole("link", { name: /חקירות עומק/ })).toHaveAttribute(
      "href",
      "/investigations",
    );
  });

  it("the errors card opens a drawer listing recent run_log errors, with a close control", async () => {
    getMorning.mockResolvedValue({
      report: null,
      headlines: [],
      open_points: [],
      night_summary: fullNightSummary,
      recent_errors: [
        { id: 1, job_id: 9, stage: "ingest", message: "FetchError: timeout", at: "2026-09-04T01:12:00+03:00" },
      ],
    });
    renderMorningPage();
    await screen.findByText("50");

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /שגיאות/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("FetchError: timeout")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "סגור" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("shows a dash (not a crash) for run duration when no run has completed yet (F12)", async () => {
    getMorning.mockResolvedValue({
      report: null,
      headlines: [],
      open_points: [],
      night_summary: fullNightSummary, // duration_min: null
      recent_errors: [],
    });
    renderMorningPage();
    await screen.findByText("50");
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});

describe("MorningPage tenders tile", () => {
  it("counts only open tenders due within 30 days and forecasts created in the last week", async () => {
    getMorning.mockResolvedValue({
      report: null,
      headlines: [],
      open_points: [],
      night_summary: null,
    });

    const now = Date.now();
    const inDays = (n: number) => new Date(now + n * 86_400_000).toISOString().slice(0, 10);
    const hoursAgo = (n: number) => new Date(now - n * 60 * 60 * 1000).toISOString();

    getTenders.mockResolvedValue([
      { id: 1, deadline: inDays(5), status: "open" }, // within 30 days -> counted
      { id: 2, deadline: inDays(45), status: "open" }, // beyond 30 days -> not counted
      { id: 3, deadline: null, status: "open" }, // no deadline -> not counted
    ]);
    getTenderForecasts.mockResolvedValue([
      { id: 10, created_at: hoursAgo(2) }, // within the last week -> counted
      { id: 11, created_at: hoursAgo(24 * 20) }, // 20 days ago -> not counted
    ]);

    renderMorningPage();

    expect(await screen.findByText("1")).toBeInTheDocument();
    expect(
      screen.getByText("מכרזים פתוחים ב-30 הימים הקרובים · 1 תחזיות חדשות השבוע"),
    ).toBeInTheDocument();
    expect(screen.getByText("מכרזים ו-RFI/RFP")).toBeInTheDocument();
  });
});
