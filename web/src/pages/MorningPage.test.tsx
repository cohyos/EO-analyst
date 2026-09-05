import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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
    };
    getMorning.mockResolvedValue(payload);

    renderMorningPage();

    expect(await screen.findByText("כותרת בדיקה")).toBeInTheDocument();
    expect(screen.getByText("שאלה פתוחה?")).toBeInTheDocument();
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
