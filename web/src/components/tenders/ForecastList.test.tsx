import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ForecastCard, ItemDetail } from "@/types/api";

const getItem = vi.fn();
const postInvestigationNew = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getItem: (...args: unknown[]) => getItem(...args),
    postInvestigationNew: (...args: unknown[]) => postInvestigationNew(...args),
  },
}));

import { ForecastList } from "./ForecastList";

function forecast(over: Partial<ForecastCard> = {}): ForecastCard {
  return {
    id: 1,
    platform: "F-35 Block 4 sustainment",
    buyer_country: "US",
    trigger_event_id: null,
    trigger_item_id: 12,
    payload_need: "פוד כיוון מהדור הבא",
    candidate_vendors: [],
    likelihood: 0.78,
    window_from: "2026-10-01",
    window_to: "2027-01-31",
    rationale_he: "בהתבסס על ההכרזה [item 12] צפוי פרסום RFI רשמי.",
    sources: ["item:12"],
    created_at: "2026-09-03T20:30:00+00:00",
    updated_at: "2026-09-03T20:30:00+00:00",
    ...over,
  };
}

function item(over: Partial<ItemDetail> = {}): ItemDetail {
  return {
    id: 12,
    title: "US Air Force awards targeting pod sustainment IDIQ",
    url: "https://example.com/articles/12",
    source_name: "Defense News",
    published_at: "2026-08-20T14:00:00+00:00",
    lang: "en",
    domain: "airborne_pods",
    subdomain: null,
    report_kind: "daily",
    trl: null,
    geography: "US",
    score: 8,
    level: "red",
    triage_reason: null,
    summary_he: "",
    so_what_he: null,
    entities_mentioned: [],
    tags: [],
    security_status: "clean",
    dedup_of: null,
    key_facts: [],
    uncertainty_he: null,
    clean_text: "",
    events: [],
    edges: [],
    investigations: [],
    ...over,
  } as ItemDetail;
}

function renderList(forecasts: ForecastCard[]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/tenders"]}>
        <Routes>
          <Route path="/tenders" element={<ForecastList forecasts={forecasts} />} />
          <Route path="/investigations/:id" element={<div>investigation page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getItem.mockReset();
  postInvestigationNew.mockReset();
});

describe("ForecastList source links (W22)", () => {
  it("resolves an item: source token to a real link labeled with outlet and date", async () => {
    getItem.mockResolvedValue(item());
    renderList([forecast()]);

    const link = await screen.findByRole("link", { name: /Defense News/ });
    expect(link).toHaveAttribute("href", "https://example.com/articles/12");
    expect(getItem).toHaveBeenCalledWith(12);
  });

  it("gives the resolved source link a title tooltip carrying the item's headline", async () => {
    getItem.mockResolvedValue(item({ title: "A very specific headline" }));
    renderList([forecast()]);

    const link = await screen.findByRole("link", { name: /Defense News/ });
    expect(link).toHaveAttribute("title", "A very specific headline");
  });

  it("still renders a real, direct link for a legacy raw-URL source", async () => {
    renderList([forecast({ sources: ["https://example-source.test/articles/2000"] })]);

    const link = await screen.findByRole("link", { name: /example-source\.test/ });
    expect(link).toHaveAttribute("href", "https://example-source.test/articles/2000");
    expect(getItem).not.toHaveBeenCalled();
  });

  it("shows a placeholder label (not a dead link) while the item is still resolving", async () => {
    getItem.mockReturnValue(new Promise(() => {})); // never resolves during this test
    renderList([forecast()]);

    expect(await screen.findByText("פריט 12")).toBeInTheDocument();
  });
});

describe("ForecastList deep-dive button (W22)", () => {
  it("states what it does via title/aria-label", async () => {
    renderList([forecast({ sources: [] })]);
    const button = await screen.findByRole("button", { name: "פתח חקירת עומק על תחזית זו" });
    expect(button).toHaveAttribute("title", "פתח חקירת עומק על תחזית זו");
  });

  it("opens a new investigation with the forecast's own question and navigates to it", async () => {
    postInvestigationNew.mockResolvedValue({ job_id: "99" });
    renderList([forecast({ sources: [] })]);

    fireEvent.click(await screen.findByRole("button", { name: "פתח חקירת עומק על תחזית זו" }));

    await waitFor(() => expect(postInvestigationNew).toHaveBeenCalledTimes(1));
    const [{ question }] = postInvestigationNew.mock.calls[0];
    expect(question).toContain("F-35 Block 4 sustainment");
    expect(question).toContain("פוד כיוון מהדור הבא");
    expect(await screen.findByText("investigation page")).toBeInTheDocument();
  });
});
