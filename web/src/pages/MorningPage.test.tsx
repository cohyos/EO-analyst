import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { MorningResponse } from "@/types/api";

const getMorning = vi.fn();
const postClarificationAnswer = vi.fn();
const getReportFileUrl = vi.fn(() => "/api/reports/1/file?fmt=docx");

vi.mock("@/api", () => ({
  api: {
    getMorning: (...args: unknown[]) => getMorning(...args),
    postClarificationAnswer: (...args: unknown[]) => postClarificationAnswer(...args),
    getReportFileUrl: (...args: unknown[]) => getReportFileUrl(...args),
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
      },
    };
    getMorning.mockResolvedValue(payload);

    renderMorningPage();

    expect(await screen.findByText("כותרת בדיקה")).toBeInTheDocument();
    expect(screen.getByText("שאלה פתוחה?")).toBeInTheDocument();
  });
});
