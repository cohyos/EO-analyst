import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type {
  ForecastCard,
  TenderCard,
  TenderSourceCoverageResponse,
  TenderStatus,
  TendersResponse,
} from "@/types/api";

const getTenders = vi.fn();
const getTenderForecasts = vi.fn();
const getTenderSourceCoverage = vi.fn();
const postTenderFeedback = vi.fn();
const getTenderFeedback = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getTenders: (...args: unknown[]) => getTenders(...args),
    getTenderForecasts: (...args: unknown[]) => getTenderForecasts(...args),
    getTenderSourceCoverage: (...args: unknown[]) => getTenderSourceCoverage(...args),
    postTenderFeedback: (...args: unknown[]) => postTenderFeedback(...args),
    getTenderFeedback: (...args: unknown[]) => getTenderFeedback(...args),
  },
}));

import { TendersPage } from "./TendersPage";

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
    matched_terms: ["targeting pod", "EO/IR"],
    entities: ["Lockheed Martin"],
    status: "open",
    item_id: 42,
    created_at: "2026-08-20T14:05:00+00:00",
    updated_at: "2026-09-01T06:00:00+00:00",
    ...over,
  };
}

/** F24: `getTenders` now resolves `{tenders, counts}` -- `counts` defaults to a tally of the
 * given rows by status unless the caller wants to assert a specific header-chip summary. */
function tendersResponse(tenders: TenderCard[], counts?: Partial<Record<TenderStatus, number>>): TendersResponse {
  const tally: Partial<Record<TenderStatus, number>> = {};
  for (const t of tenders) tally[t.status] = (tally[t.status] ?? 0) + 1;
  return { tenders, counts: counts ?? tally };
}

function makeForecast(over: Partial<ForecastCard> = {}): ForecastCard {
  return {
    id: 1,
    platform: "F-35 Block 4 sustainment",
    buyer_country: "US",
    trigger_event_id: null,
    trigger_item_id: 12,
    payload_need: "פוד כיוון מהדור הבא",
    candidate_vendors: ["Elbit Systems", "Lockheed Martin"],
    likelihood: 0.78,
    window_from: "2026-10-01",
    window_to: "2027-01-31",
    rationale_he: "בהתבסס על ההכרזה [item 12] צפוי פרסום RFI רשמי.",
    sources: ["https://example-source.test/articles/2000"],
    created_at: "2026-09-03T20:30:00+00:00",
    updated_at: "2026-09-03T20:30:00+00:00",
    ...over,
  };
}

function renderPage(initialEntries: string[] = ["/tenders"]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <TendersPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function coverageResponse(
  over: Partial<TenderSourceCoverageResponse> = {},
): TenderSourceCoverageResponse {
  return {
    regions: [
      {
        region: "EU",
        sources: [
          {
            id: "ted_eu",
            name: "TED (Tenders Electronic Daily) -- EU",
            kind: "api_json",
            country: "EU",
            status: "integrated_keyless",
            verified: true,
            needs_key_env_var: null,
            notices_stored: 12,
            last_fetch_at: "2026-09-06T04:00:00Z",
            priority_decrement: 0,
          },
        ],
      },
      {
        region: "US",
        sources: [
          {
            id: "sam_gov_api",
            name: "SAM.gov Opportunities API v2 (US)",
            kind: "api_json",
            country: "US",
            status: "waiting_for_key",
            verified: false,
            needs_key_env_var: "SAM_GOV_API_KEY",
            notices_stored: 0,
            last_fetch_at: null,
            priority_decrement: 0,
          },
        ],
      },
    ],
    totals: { integrated_keyless: 1, waiting_for_key: 1, search_only: 0, not_integrated: 0 },
    source_count: 2,
    ...over,
  };
}

beforeEach(() => {
  getTenders.mockReset();
  getTenderForecasts.mockReset();
  getTenderSourceCoverage.mockReset();
  getTenderSourceCoverage.mockResolvedValue(coverageResponse());
});

describe("TendersPage — open tenders tab", () => {
  it("shows the Hebrew empty state when there are no tenders at all", async () => {
    getTenders.mockResolvedValue(tendersResponse([]));
    renderPage();
    expect(await screen.findByText("אין מכרזים פתוחים כרגע")).toBeInTheDocument();
  });

  it("renders a tender row with an outbound link opening in a new tab", async () => {
    getTenders.mockResolvedValue(tendersResponse([makeTender()]));
    renderPage();
    const link = await screen.findByRole("link", { name: /Targeting Pod Sustainment IDIQ/ });
    expect(link).toHaveAttribute("href", "https://sam.gov/opp/example");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("applies the urgent (red) deadline chip style when fewer than 14 days remain, and the neutral style otherwise", async () => {
    const now = new Date();
    const soon = new Date(now.getTime() + 5 * 86_400_000).toISOString().slice(0, 10);
    const later = new Date(now.getTime() + 40 * 86_400_000).toISOString().slice(0, 10);
    getTenders.mockResolvedValue(
      tendersResponse([
        makeTender({ id: 1, title: "Urgent tender", deadline: soon }),
        makeTender({ id: 2, title: "Not urgent tender", deadline: later }),
      ]),
    );
    renderPage();

    const urgentRow = (await screen.findByText("Urgent tender")).closest("tr")!;
    const urgentChip = within(urgentRow).getByTitle(/נותרו \d+ ימים/);
    expect(urgentChip.className).toMatch(/text-danger/);

    const laterRow = screen.getByText("Not urgent tender").closest("tr")!;
    const laterChip = within(laterRow).getByTitle(/נותרו \d+ ימים/);
    expect(laterChip.className).not.toMatch(/text-danger/);
  });

  it("shows an em-dash and no days-left title when the tender has no deadline", async () => {
    getTenders.mockResolvedValue(tendersResponse([makeTender({ deadline: null })]));
    renderPage();
    await screen.findByText("Targeting Pod Sustainment IDIQ");
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("expands a row on click to show the summary, entities, CPV/NAICS and a link to the linked item", async () => {
    getTenders.mockResolvedValue(tendersResponse([makeTender()]));
    renderPage();
    const row = (await screen.findByText("Targeting Pod Sustainment IDIQ")).closest("tr")!;

    expect(screen.queryByText(/סיכום בעברית של המכרז/)).not.toBeInTheDocument();
    fireEvent.click(row);
    // F24: the detail row's "why relevant" line combines matched_terms + summary_he.
    expect(screen.getByText(/סיכום בעברית של המכרז/)).toBeInTheDocument();
    expect(screen.getByText(/targeting pod, EO\/IR/)).toBeInTheDocument();
    expect(screen.getByText("Lockheed Martin")).toBeInTheDocument();
    expect(screen.getByText("336413")).toBeInTheDocument();
    const itemLink = screen.getByRole("link", { name: /פתח פריט מקושר/ });
    expect(itemLink).toHaveAttribute("href", "/items/42");

    fireEvent.click(row);
    expect(screen.queryByText(/סיכום בעברית של המכרז/)).not.toBeInTheDocument();
  });

  it("shows header count chips summarizing tenders by status", async () => {
    getTenders.mockResolvedValue(
      tendersResponse([makeTender({ id: 1, status: "open" })], { open: 5, unknown: 2, closed: 8, archived: 3 }),
    );
    renderPage();
    const chips = await screen.findByRole("list", { name: "ספירת מכרזים לפי סטטוס" });
    expect(within(chips).getByText("פתוח: 5")).toBeInTheDocument();
    expect(within(chips).getByText("לא ידוע: 2")).toBeInTheDocument();
    expect(within(chips).getByText("סגור: 8")).toBeInTheDocument();
    expect(within(chips).getByText("בארכיון: 3")).toBeInTheDocument();
  });

  it("re-queries with an explicit status when the status filter changes, bypassing the default view", async () => {
    getTenders.mockImplementation(async (query: { status?: string }) =>
      query.status === "awarded"
        ? tendersResponse([makeTender({ id: 2, title: "IL awarded tender", status: "awarded", country: "IL" })])
        : tendersResponse([makeTender({ id: 1, title: "US open tender", status: "open", country: "US" })]),
    );
    renderPage();
    await screen.findByText("US open tender");

    fireEvent.change(screen.getByLabelText("סינון לפי סטטוס"), { target: { value: "awarded" } });
    expect(await screen.findByText("IL awarded tender")).toBeInTheDocument();
    expect(screen.queryByText("US open tender")).not.toBeInTheDocument();
    expect(getTenders.mock.calls.at(-1)?.[0]).toMatchObject({ status: "awarded" });
  });

  it("checking 'show closed/archived' re-queries with include_closed and include_archived set", async () => {
    getTenders.mockResolvedValue(tendersResponse([makeTender()]));
    renderPage();
    await screen.findByText("Targeting Pod Sustainment IDIQ");

    fireEvent.click(screen.getByRole("checkbox"));
    await screen.findByText("Targeting Pod Sustainment IDIQ");
    expect(getTenders.mock.calls.at(-1)?.[0]).toMatchObject({
      include_closed: true,
      include_archived: true,
    });
  });

  // Q5-11 (docs/qa/findings_Q5_r2.md): the default (open/unknown) view is empty but closed tenders
  // exist -- an inline hint + action must appear instead of a dead-end "no matches" message, and
  // clicking it must reveal them (re-query with include_closed/include_archived).
  it("shows an inline hint with a count when closed/archived rows are hidden by the default view", async () => {
    getTenders.mockResolvedValue(tendersResponse([], { closed: 5 }));
    renderPage();

    expect(await screen.findByText(/5 מכרזים סגורים מוסתרים בתצוגה הנוכחית/)).toBeInTheDocument();
    expect(screen.queryByText("אין מכרזים תואמים")).not.toBeInTheDocument();
  });

  it("clicking the inline hidden-closed-tenders action reveals them", async () => {
    getTenders.mockImplementation(async (query: { include_closed?: boolean }) =>
      query.include_closed
        ? tendersResponse([makeTender({ status: "closed" })], { closed: 5 })
        : tendersResponse([], { closed: 5 }),
    );
    renderPage();

    const cta = await screen.findByRole("button", { name: /הצג 5 מכרזים סגורים/ });
    fireEvent.click(cta);

    expect(await screen.findByText("Targeting Pod Sustainment IDIQ")).toBeInTheDocument();
    expect(getTenders.mock.calls.at(-1)?.[0]).toMatchObject({
      include_closed: true,
      include_archived: true,
    });
  });

  it("shows the plain 'no open tenders' empty state when there are no hidden closed/archived rows either", async () => {
    getTenders.mockResolvedValue(tendersResponse([]));
    renderPage();

    expect(await screen.findByText("אין מכרזים פתוחים כרגע")).toBeInTheDocument();
    expect(screen.queryByText(/מכרזים סגורים מוסתרים/)).not.toBeInTheDocument();
  });
});

describe("TendersPage — forecasts tab", () => {
  it("shows the Hebrew empty state when there are no forecasts", async () => {
    getTenders.mockResolvedValue(tendersResponse([]));
    getTenderForecasts.mockResolvedValue([]);
    renderPage(["/tenders?tab=forecast"]);
    expect(await screen.findByText("אין תחזיות מכרזים כרגע")).toBeInTheDocument();
  });

  it("sorts forecasts by likelihood descending", async () => {
    getTenders.mockResolvedValue(tendersResponse([]));
    getTenderForecasts.mockResolvedValue([
      makeForecast({ id: 1, payload_need: "Low likelihood need", likelihood: 0.2 }),
      makeForecast({ id: 2, payload_need: "High likelihood need", likelihood: 0.9 }),
    ]);
    renderPage(["/tenders?tab=forecast"]);

    await screen.findByText("High likelihood need");
    const cards = screen.getAllByText(/likelihood need/);
    expect(cards[0]).toHaveTextContent("High likelihood need");
    expect(cards[1]).toHaveTextContent("Low likelihood need");
  });

  it("turns [item N] tokens in rationale_he into links to /items/N", async () => {
    getTenders.mockResolvedValue(tendersResponse([]));
    getTenderForecasts.mockResolvedValue([makeForecast()]);
    renderPage(["/tenders?tab=forecast"]);

    const link = await screen.findByRole("link", { name: "[item 12]" });
    expect(link).toHaveAttribute("href", "/items/12");
  });

  it("switching to the forecast tab via the tab button updates the URL and shows forecast content", async () => {
    getTenders.mockResolvedValue(tendersResponse([]));
    getTenderForecasts.mockResolvedValue([makeForecast()]);
    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "תחזית מכרזים" }));
    expect(await screen.findByText("פוד כיוון מהדור הבא")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "תחזית מכרזים" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
});

// A15 (docs/TENDER_PORTALS.md): source-coverage panel, rendered on both tabs of /tenders.
describe("TendersPage — source coverage panel", () => {
  it("shows the panel title and summary counts collapsed by default", async () => {
    getTenders.mockResolvedValue(tendersResponse([]));
    renderPage();

    expect(await screen.findByText("כיסוי מקורות")).toBeInTheDocument();
    expect(screen.getByText("2 מקורות")).toBeInTheDocument();
    expect(screen.getByText(/משולב \(ללא מפתח\): 1/)).toBeInTheDocument();
    expect(screen.getByText(/ממתין למפתח API: 1/)).toBeInTheDocument();
    // Collapsed: no per-source rows yet.
    expect(screen.queryByText("TED (Tenders Electronic Daily) -- EU")).not.toBeInTheDocument();
  });

  it("expands to show per-region source rows including the needs-key env var", async () => {
    getTenders.mockResolvedValue(tendersResponse([]));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: /כיסוי מקורות/ }));

    expect(await screen.findByText("TED (Tenders Electronic Daily) -- EU")).toBeInTheDocument();
    expect(screen.getByText("SAM.gov Opportunities API v2 (US)")).toBeInTheDocument();
    expect(screen.getByText("SAM_GOV_API_KEY")).toBeInTheDocument();
  });

  it("collapses again on a second click", async () => {
    getTenders.mockResolvedValue(tendersResponse([]));
    renderPage();

    const toggle = await screen.findByRole("button", { name: /כיסוי מקורות/ });
    fireEvent.click(toggle);
    expect(await screen.findByText("TED (Tenders Electronic Daily) -- EU")).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.queryByText("TED (Tenders Electronic Daily) -- EU")).not.toBeInTheDocument();
  });

  it("renders nothing (no crash) when the coverage endpoint errors", async () => {
    getTenders.mockResolvedValue(tendersResponse([]));
    getTenderSourceCoverage.mockRejectedValue(new Error("network error"));
    renderPage();

    await screen.findByText("אין מכרזים פתוחים כרגע");
    expect(screen.queryByText("כיסוי מקורות")).not.toBeInTheDocument();
  });
});
