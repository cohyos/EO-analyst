import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { PatentFacetsResponse, PatentRecord, PatentsResponse, PatentsStatusResponse } from "@/types/api";

const getPatents = vi.fn();
const getPatentsStatus = vi.fn();
const getPatentsHeatmap = vi.fn();
const getPatentSurveys = vi.fn();
const createPatentSurvey = vi.fn();
const getPatentFacets = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getPatents: (...args: unknown[]) => getPatents(...args),
    getPatentsStatus: (...args: unknown[]) => getPatentsStatus(...args),
    getPatentsHeatmap: (...args: unknown[]) => getPatentsHeatmap(...args),
    getPatentSurveys: (...args: unknown[]) => getPatentSurveys(...args),
    createPatentSurvey: (...args: unknown[]) => createPatentSurvey(...args),
    getPatentFacets: (...args: unknown[]) => getPatentFacets(...args),
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

function facetsResponse(over: Partial<PatentFacetsResponse> = {}): PatentFacetsResponse {
  return { assignees: [], subdomains: [], ...over };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/patents"]}>
        <PatentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...view, queryClient };
}

beforeEach(() => {
  getPatents.mockReset();
  getPatentsStatus.mockReset();
  getPatentsHeatmap.mockReset();
  getPatentSurveys.mockReset();
  createPatentSurvey.mockReset();
  getPatentFacets.mockReset();
  getPatents.mockResolvedValue(patentsResponse([]));
  getPatentsStatus.mockResolvedValue(statusResponse());
  getPatentsHeatmap.mockResolvedValue({ cpc_codes: [], assignees: [], cells: [] });
  getPatentSurveys.mockResolvedValue([]);
  getPatentFacets.mockResolvedValue(facetsResponse());
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

  // F33 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md): assignee/subdomain/q used to be applied
  // client-side against whatever the (200-row-capped) unfiltered fetch happened to contain -- a
  // match sitting outside that cap could never be found. These assert the filters are forwarded
  // to `api.getPatents` itself (server-side filtering, applied before any cap), not just used to
  // re-filter an already-fetched page.
  describe("F33: filters are sent to the API, not applied only client-side", () => {
    it("forwards the assignee filter as a getPatents() call argument", async () => {
      // "Rafael" must exist as a dropdown option (from the uncapped facets fetch, F33) before it
      // can be selected at all -- a native <select> silently ignores a value with no matching
      // <option>.
      getPatentFacets.mockResolvedValue(facetsResponse({ assignees: ["Elbit", "Rafael"] }));
      const baseline = [makePatent({ id: 1, assignees: ["Elbit"] })];
      const beyondCap = makePatent({ id: 2, pub_number: "US99999999B2", title: "Beyond the cap", assignees: ["Rafael"] });
      getPatents.mockImplementation((params: { assignee?: string } = {}) =>
        Promise.resolve(patentsResponse(params.assignee === "Rafael" ? [beyondCap] : baseline)),
      );
      renderPage();
      await screen.findByText("Digital pixel readout integrated circuit");

      fireEvent.change(screen.getByRole("combobox", { name: "סינון לפי בעלים" }), {
        target: { value: "Rafael" },
      });

      // The row that only exists in the server's assignee-filtered result must now show up --
      // it was never present in the earlier (unfiltered) fetch at all, so a client-side-only
      // filter could not possibly have found it.
      expect(await screen.findByText("Beyond the cap")).toBeInTheDocument();
      expect(getPatents).toHaveBeenCalledWith(expect.objectContaining({ assignee: "Rafael" }));
    });

    it("re-fetches from the server (not a local re-filter) when the subdomain filter changes", async () => {
      getPatentFacets.mockResolvedValue(
        facetsResponse({ subdomains: ["droic_digital_pixel", "rare_subdomain"] }),
      );
      const baseline = [makePatent({ id: 1 })];
      const filtered = [
        makePatent({ id: 3, pub_number: "US1", title: "Rare match (filtered)", subdomain: "rare_subdomain" }),
      ];
      getPatents.mockImplementation((params: { subdomain?: string } = {}) =>
        Promise.resolve(patentsResponse(params.subdomain === "rare_subdomain" ? filtered : baseline)),
      );
      renderPage();
      await screen.findByText("Digital pixel readout integrated circuit");

      fireEvent.change(screen.getByRole("combobox", { name: "סינון לפי תת-תחום" }), {
        target: { value: "rare_subdomain" },
      });

      // A record that only exists in the server's subdomain-filtered response (distinct title)
      // proves this went back to the server, not a client-side re-filter of the initial fetch.
      expect(await screen.findByText("Rare match (filtered)")).toBeInTheDocument();
      expect(screen.queryByText("אין תוצאות תואמות")).not.toBeInTheDocument();
      expect(getPatents).toHaveBeenCalledWith(expect.objectContaining({ subdomain: "rare_subdomain" }));
    });
  });

  // R06/F33 (SOL-REVIEW2-2026-09-24 review): server-side "load more" pagination -- a second
  // page's rows must be reachable and appended to what's already on screen, not replace it.
  it("a load-more click fetches page 2 and appends its rows", async () => {
    const page1 = makePatent({ id: 1 });
    const page2 = makePatent({ id: 2, pub_number: "US99999999B2", title: "Second page patent" });
    getPatents.mockImplementation((params: { page?: number } = {}) =>
      Promise.resolve({
        patents: params.page === 2 ? [page2] : [page1],
        total: 2,
        page: params.page ?? 1,
        limit: 200,
        has_more: (params.page ?? 1) === 1,
      }),
    );
    renderPage();
    await screen.findByText("Digital pixel readout integrated circuit");
    const loadMore = await screen.findByRole("button", { name: /טען עוד/ });
    fireEvent.click(loadMore);
    expect(await screen.findByText("Second page patent")).toBeInTheDocument();
    // The first page's row must still be visible -- "load more" appends, it doesn't replace.
    expect(screen.getByText("Digital pixel readout integrated circuit")).toBeInTheDocument();
    expect(getPatents).toHaveBeenCalledWith(expect.objectContaining({ page: 2 }));
  });

  // P2 (SOL-REVIEW3-2026-09-24 "pagination"): a re-delivered page above page 1 (react-query
  // refetchOnWindowFocus/refetchOnReconnect, an invalidation, or a StrictMode double effect) must
  // not duplicate that page's rows -- accumulation is keyed by page number (Map.set), not pushed
  // onto an array, so re-storing page 2's rows overwrites the same slot instead of appending it.
  it("a refetch of an already-loaded page does not duplicate its rows", async () => {
    const page1 = makePatent({ id: 1 });
    const page2 = makePatent({ id: 2, pub_number: "US99999999B2", title: "Second page patent" });
    getPatents.mockImplementation((params: { page?: number } = {}) =>
      Promise.resolve({
        patents: params.page === 2 ? [page2] : [page1],
        total: 2,
        page: params.page ?? 1,
        limit: 200,
        has_more: (params.page ?? 1) === 1,
      }),
    );
    const { queryClient } = renderPage();
    await screen.findByText("Digital pixel readout integrated circuit");
    fireEvent.click(await screen.findByRole("button", { name: /טען עוד/ }));
    await screen.findByText("Second page patent");

    // Simulate the SAME page-2 response being re-delivered (e.g. a window-focus refetch of the
    // still-mounted page-2 query) without the user clicking "load more" again.
    await queryClient.refetchQueries({ queryKey: ["patents"] });
    await screen.findByText("Second page patent");

    expect(screen.getAllByText("Second page patent")).toHaveLength(1);
    expect(screen.getAllByText("Digital pixel readout integrated circuit")).toHaveLength(1);
  });

  // R09 (SOL-REVIEW2-2026-09-24 review): the "database empty" empty-state must key off the
  // facets endpoint's unfiltered `total`, not off facet VALUE presence -- a table where every
  // existing patent has a null assignee AND null subdomain must not be reported as empty.
  it("does not show the 'database empty' state when facets.total > 0 but assignees/subdomains are null (R09)", async () => {
    getPatentFacets.mockResolvedValue(facetsResponse({ assignees: [], subdomains: [], total: 2 }));
    getPatents.mockResolvedValue(patentsResponse([makePatent({ assignees: [], subdomain: null })]));
    renderPage();
    expect(await screen.findByText("Digital pixel readout integrated circuit")).toBeInTheDocument();
    expect(screen.queryByText("לא זוהו פטנטים")).not.toBeInTheDocument();
  });
});
