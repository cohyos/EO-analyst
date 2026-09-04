import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ForecastCard, TenderCard } from "@/types/api";

const getTenders = vi.fn();
const getTenderForecasts = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getTenders: (...args: unknown[]) => getTenders(...args),
    getTenderForecasts: (...args: unknown[]) => getTenderForecasts(...args),
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
    matched_terms: ["targeting pod", "EO/IR"],
    entities: ["Lockheed Martin"],
    status: "open",
    item_id: 42,
    created_at: "2026-08-20T14:05:00+00:00",
    updated_at: "2026-09-01T06:00:00+00:00",
    ...over,
  };
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

beforeEach(() => {
  getTenders.mockReset();
  getTenderForecasts.mockReset();
});

describe("TendersPage — open tenders tab", () => {
  it("shows the Hebrew empty state when there are no tenders at all", async () => {
    getTenders.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText("אין מכרזים פתוחים כרגע")).toBeInTheDocument();
  });

  it("renders a tender row with an outbound link opening in a new tab", async () => {
    getTenders.mockResolvedValue([makeTender()]);
    renderPage();
    const link = await screen.findByRole("link", { name: /Targeting Pod Sustainment IDIQ/ });
    expect(link).toHaveAttribute("href", "https://sam.gov/opp/example");
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("applies the urgent (red) deadline chip style when fewer than 14 days remain, and the neutral style otherwise", async () => {
    const now = new Date();
    const soon = new Date(now.getTime() + 5 * 86_400_000).toISOString().slice(0, 10);
    const later = new Date(now.getTime() + 40 * 86_400_000).toISOString().slice(0, 10);
    getTenders.mockResolvedValue([
      makeTender({ id: 1, title: "Urgent tender", deadline: soon }),
      makeTender({ id: 2, title: "Not urgent tender", deadline: later }),
    ]);
    renderPage();

    const urgentRow = (await screen.findByText("Urgent tender")).closest("tr")!;
    const urgentChip = within(urgentRow).getByTitle(/נותרו \d+ ימים/);
    expect(urgentChip.className).toMatch(/text-danger/);

    const laterRow = screen.getByText("Not urgent tender").closest("tr")!;
    const laterChip = within(laterRow).getByTitle(/נותרו \d+ ימים/);
    expect(laterChip.className).not.toMatch(/text-danger/);
  });

  it("shows an em-dash and no days-left title when the tender has no deadline", async () => {
    getTenders.mockResolvedValue([makeTender({ deadline: null })]);
    renderPage();
    await screen.findByText("Targeting Pod Sustainment IDIQ");
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("expands a row on click to show the summary, entities, CPV/NAICS and a link to the linked item", async () => {
    getTenders.mockResolvedValue([makeTender()]);
    renderPage();
    const row = (await screen.findByText("Targeting Pod Sustainment IDIQ")).closest("tr")!;

    expect(screen.queryByText("סיכום בעברית של המכרז")).not.toBeInTheDocument();
    fireEvent.click(row);
    expect(screen.getByText("סיכום בעברית של המכרז")).toBeInTheDocument();
    expect(screen.getByText("Lockheed Martin")).toBeInTheDocument();
    expect(screen.getByText("336413")).toBeInTheDocument();
    const itemLink = screen.getByRole("link", { name: /פתח פריט מקושר/ });
    expect(itemLink).toHaveAttribute("href", "/items/42");

    fireEvent.click(row);
    expect(screen.queryByText("סיכום בעברית של המכרז")).not.toBeInTheDocument();
  });

  it("filters the table by status", async () => {
    getTenders.mockResolvedValue([
      makeTender({ id: 1, title: "US open tender", status: "open", country: "US" }),
      makeTender({ id: 2, title: "IL awarded tender", status: "awarded", country: "IL" }),
    ]);
    renderPage();
    await screen.findByText("US open tender");
    expect(screen.getByText("IL awarded tender")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("סינון לפי סטטוס"), { target: { value: "awarded" } });
    expect(screen.queryByText("US open tender")).not.toBeInTheDocument();
    expect(screen.getByText("IL awarded tender")).toBeInTheDocument();
  });
});

describe("TendersPage — forecasts tab", () => {
  it("shows the Hebrew empty state when there are no forecasts", async () => {
    getTenders.mockResolvedValue([]);
    getTenderForecasts.mockResolvedValue([]);
    renderPage(["/tenders?tab=forecast"]);
    expect(await screen.findByText("אין תחזיות מכרזים כרגע")).toBeInTheDocument();
  });

  it("sorts forecasts by likelihood descending", async () => {
    getTenders.mockResolvedValue([]);
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
    getTenders.mockResolvedValue([]);
    getTenderForecasts.mockResolvedValue([makeForecast()]);
    renderPage(["/tenders?tab=forecast"]);

    const link = await screen.findByRole("link", { name: "[item 12]" });
    expect(link).toHaveAttribute("href", "/items/12");
  });

  it("switching to the forecast tab via the tab button updates the URL and shows forecast content", async () => {
    getTenders.mockResolvedValue([]);
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
