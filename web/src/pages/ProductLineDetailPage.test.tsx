import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ItemCard, ProductLineDetail, ReportSummary, TenderCard } from "@/types/api";

const getProductLine = vi.fn();
const postProductLineReport = vi.fn();
const postItemFeedback = vi.fn();
const postTenderFeedback = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getProductLine: (...args: unknown[]) => getProductLine(...args),
    postProductLineReport: (...args: unknown[]) => postProductLineReport(...args),
    postItemFeedback: (...args: unknown[]) => postItemFeedback(...args),
    postTenderFeedback: (...args: unknown[]) => postTenderFeedback(...args),
  },
}));

import { ProductLineDetailPage } from "./ProductLineDetailPage";

function makeItem(over: Partial<ItemCard> = {}): ItemCard {
  return {
    id: 1,
    title: "פוד כיוון חדש",
    url: "https://example.test/1",
    source_name: "Test Source",
    published_at: "2026-09-03T10:00:00+03:00",
    lang: "he",
    domain: "airborne_pods",
    subdomain: "targeting_pods",
    report_kind: "verified_report",
    trl: "prototype",
    geography: "IL",
    score: 80,
    level: "orange",
    triage_reason: "בדיקה",
    summary_he: "תקציר בדיקה",
    so_what_he: "אז מה בדיקה",
    entities_mentioned: [],
    tags: [],
    security_status: "clean",
    dedup_of: null,
    key_facts: [],
    uncertainty_he: null,
    tech_maturity: null,
    tech_actor_kind: null,
    tech_readiness_note_he: null,
    israel_relevance: null,
    israel_reasons: [],
    product_lines: ["targeting_pods"],
    ...over,
  };
}

function makeTender(over: Partial<TenderCard> = {}): TenderCard {
  return {
    id: 1,
    source: "SAM.gov",
    external_ref: "REF-1",
    title: "Targeting Pod Sustainment IDIQ",
    agency: "US Air Force",
    country: "US",
    published_at: "2026-08-20T14:00:00+00:00",
    deadline: "2026-09-10",
    url: "https://sam.gov/opp/example",
    cpv_naics: ["336413"],
    summary_he: "סיכום בעברית של המכרז",
    relevance: 4,
    relevance_score: 0.8,
    intake: "accepted",
    matched_terms: ["targeting pod"],
    entities: ["Lockheed Martin"],
    status: "open",
    item_id: null,
    created_at: "2026-08-20T14:05:00+00:00",
    updated_at: "2026-09-01T06:00:00+00:00",
    product_lines: ["targeting_pods"],
    ...over,
  };
}

function makeReport(over: Partial<ReportSummary> = {}): ReportSummary {
  return {
    id: 960,
    kind: "product_line",
    period_start: "2026-06-08T00:00:00+03:00",
    period_end: "2026-09-06T00:00:00+03:00",
    path_docx: null,
    path_md: null,
    path_html: "/reports/pl_960.html",
    qa_passed: true,
    created_at: "2026-09-06T07:00:00+03:00",
    headline_count: 6,
    territory: null,
    title_he: "דוח קו מוצר — פודי כיוון",
    subject_he: "פודי כיוון",
    built_at: "2026-09-06T07:00:00+03:00",
    preview_he: "תקציר קצר",
    source_count: 4,
    qa_issues: 0,
    group_key: "product_line:targeting_pods",
    is_latest: true,
    ...over,
  };
}

function detail(over: Partial<ProductLineDetail> = {}): ProductLineDetail {
  return {
    id: "targeting_pods",
    name_he: "פודי ציון מטרות / תקיפה",
    name_en: "Targeting Pods",
    subdomains: ["airborne_pods/targeting_pods"],
    exemplar_systems: ["Litening"],
    competitors: ["Lockheed Martin"],
    stats: {
      items_7d: 1,
      items_30d: 4,
      events_30d: 2,
      open_tenders: 1,
      forecasts: 1,
      patents_90d: 3,
      active_competitors: 1,
    },
    latest_report: { id: 960, created_at: "2026-09-06T07:00:00+03:00", qa_passed: true, path_html: "/reports/pl_960.html" },
    recent_items: [makeItem()],
    open_tenders: [makeTender()],
    reports: [makeReport()],
    ...over,
  };
}

function renderPage(id = "targeting_pods") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/product-lines/${id}`]}>
        <Routes>
          <Route path="/product-lines/:id" element={<ProductLineDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getProductLine.mockReset();
  postProductLineReport.mockReset();
  postItemFeedback.mockReset();
  postTenderFeedback.mockReset();
});

describe("ProductLineDetailPage (PL-ui)", () => {
  it("renders the header name, chips and stat tiles", async () => {
    getProductLine.mockResolvedValue(detail());
    renderPage();

    expect(await screen.findByRole("heading", { name: "פודי ציון מטרות / תקיפה" })).toBeInTheDocument();
    expect(screen.getByText("Litening")).toBeInTheDocument();
    expect(screen.getByText("Lockheed Martin")).toBeInTheDocument();
    expect(screen.getByText("פריטים (7 ימים)")).toBeInTheDocument();
  });

  it("defaults to the recent-items tab and reuses FeedRow for each item", async () => {
    getProductLine.mockResolvedValue(detail());
    renderPage();

    expect(await screen.findByTestId("feed-row-1")).toBeInTheDocument();
  });

  it("switches to the open-tenders tab and renders the shared TenderTable", async () => {
    const user = userEvent.setup();
    getProductLine.mockResolvedValue(detail());
    renderPage();

    await screen.findByTestId("feed-row-1");
    await user.click(screen.getByRole("tab", { name: /מכרזים פתוחים/ }));
    expect(await screen.findByText("Targeting Pod Sustainment IDIQ")).toBeInTheDocument();
  });

  it("switches to the reports tab and links each report to the Reports page", async () => {
    const user = userEvent.setup();
    getProductLine.mockResolvedValue(detail());
    renderPage();

    await screen.findByTestId("feed-row-1");
    await user.click(screen.getByRole("tab", { name: /^דוחות/ }));
    const link = await screen.findByRole("link", { name: /דוח קו מוצר/ });
    expect(link).toHaveAttribute("href", "/reports?id=960");
  });

  it("queues a report build from the detail page's 'צור דוח' button", async () => {
    const user = userEvent.setup();
    getProductLine.mockResolvedValue(detail());
    postProductLineReport.mockResolvedValue({ job_id: "mock-pl-job-2" });
    renderPage();

    const button = await screen.findByTestId("product-line-detail-create-report");
    await user.click(button);

    await waitFor(() => expect(postProductLineReport).toHaveBeenCalledWith("targeting_pods"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "הדוח בבנייה ברקע — יופיע ברשימה כשיושלם",
    );
  });

  it("shows an empty state when there are no recent items", async () => {
    getProductLine.mockResolvedValue(detail({ recent_items: [] }));
    renderPage();
    expect(await screen.findByText("אין פריטים אחרונים לקו מוצר זה")).toBeInTheDocument();
  });

  it("shows an error state with retry on failure", async () => {
    getProductLine.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });
});
