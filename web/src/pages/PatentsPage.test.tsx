import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { PatentRecord, PatentsResponse, PatentsStatusResponse } from "@/types/api";

const getPatents = vi.fn();
const getPatentsStatus = vi.fn();
const getPatentsHeatmap = vi.fn();
const getPatentSurveys = vi.fn();
const createPatentSurvey = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getPatents: (...args: unknown[]) => getPatents(...args),
    getPatentsStatus: (...args: unknown[]) => getPatentsStatus(...args),
    getPatentsHeatmap: (...args: unknown[]) => getPatentsHeatmap(...args),
    getPatentSurveys: (...args: unknown[]) => getPatentSurveys(...args),
    createPatentSurvey: (...args: unknown[]) => createPatentSurvey(...args),
  },
}));

import { PatentsPage } from "./PatentsPage";

function makePatent(over: Partial<PatentRecord> = {}): PatentRecord {
  return {
    id: 1,
    pub_number: "US11234567B2",
    kind: "B2",
    title: "Digital pixel readout integrated circuit",
    abstract: "A digital-pixel ROIC for infrared focal plane arrays.",
    assignees: ["Elbit"],
    inventors: [],
    cpc: ["H01L27"],
    priority_date: "2023-02-01",
    filing_date: "2023-02-01",
    publication_date: "2025-06-15",
    grant_date: null,
    family_id: null,
    jurisdictions: ["US", "IL"],
    forward_citations: 3,
    backward_citations: 12,
    url: "https://patents.google.com/patent/US11234567B2/en",
    source: "google_patents_search",
    subdomain: "droic_digital_pixel",
    claims_summary_he: "סיכום תביעות בעברית.",
    so_what_he: "משמעות עסקית בעברית.",
    israel_relevance: 0.8,
    value_score: 62,
    value_reasons: ["הוגש/פורסם ב-2 מדינות/אזורים", "מדד פרוקסי, לא הערכת שווי כספית"],
    created_at: "2026-09-01T08:00:00+00:00",
    updated_at: "2026-09-01T08:00:00+00:00",
    ...over,
  };
}

function patentsResponse(patents: PatentRecord[]): PatentsResponse {
  return { patents, total: patents.length };
}

function statusResponse(over: Partial<PatentsStatusResponse> = {}): PatentsStatusResponse {
  return { structured_sources_configured: false, banner_he: null, ...over };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/patents"]}>
        <PatentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getPatents.mockReset();
  getPatentsStatus.mockReset();
  getPatentsHeatmap.mockReset();
  getPatentSurveys.mockReset();
  createPatentSurvey.mockReset();
  getPatents.mockResolvedValue(patentsResponse([]));
  getPatentsStatus.mockResolvedValue(statusResponse());
  getPatentsHeatmap.mockResolvedValue({ cpc_codes: [], assignees: [], cells: [] });
  getPatentSurveys.mockResolvedValue([]);
});

describe("PatentsPage", () => {
  it("shows an empty state when there are no patents", async () => {
    renderPage();
    expect(await screen.findByText("לא זוהו פטנטים")).toBeInTheDocument();
  });

  it("renders a fetched patent's title and value score", async () => {
    getPatents.mockResolvedValue(patentsResponse([makePatent()]));
    renderPage();
    expect(await screen.findByText("Digital pixel readout integrated circuit")).toBeInTheDocument();
    expect(screen.getByText("62")).toBeInTheDocument();
  });

  it("shows the search-only-mode banner when structured sources are not configured", async () => {
    getPatentsStatus.mockResolvedValue(
      statusResponse({ banner_he: "מקורות פטנטים: מצב חיפוש בלבד — הזן EPO_OPS_KEY/USPTO_ODP_API_KEY ב-.env לכיסוי מלא." }),
    );
    renderPage();
    expect(
      await screen.findByText(/מקורות פטנטים: מצב חיפוש בלבד/),
    ).toBeInTheDocument();
  });

  it("switches to the heatmap tab", async () => {
    getPatents.mockResolvedValue(patentsResponse([makePatent()]));
    renderPage();
    await screen.findByText("Digital pixel readout integrated circuit");
    fireEvent.click(screen.getByRole("tab", { name: "מטריצת CPC x בעלים" }));
    expect(await screen.findByText(/אין עדיין מספיק נתונים/)).toBeInTheDocument();
  });
});
