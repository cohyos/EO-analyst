import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { EntitySummary, ItemDetail } from "@/types/api";

const getItem = vi.fn();
const getEntities = vi.fn();
const postItemFeedback = vi.fn();
const postItemInvestigate = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getItem: (...args: unknown[]) => getItem(...args),
    getEntities: (...args: unknown[]) => getEntities(...args),
    postItemFeedback: (...args: unknown[]) => postItemFeedback(...args),
    postItemInvestigate: (...args: unknown[]) => postItemInvestigate(...args),
  },
}));

import { ItemDetailPage } from "./ItemDetailPage";

function baseItem(over: Partial<ItemDetail> = {}): ItemDetail {
  return {
    id: 3,
    title: "General Atomics MQ-9B upgrade",
    url: "https://example.test/3",
    source_name: "Breaking Defense",
    published_at: "2026-09-03T10:00:00+03:00",
    lang: "en",
    domain: "airborne_pods",
    subdomain: null,
    report_kind: "verified_report",
    trl: "prototype",
    geography: "US",
    score: 7,
    level: "orange",
    triage_reason: "נימוק בדיקה",
    summary_he: null,
    so_what_he: null,
    entities_mentioned: ["General Atomics", "Some Random Person"],
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
    clean_text: "",
    events: [],
    edges: [{ src: 100, dst: 101, label: "COMPETITOR_OF", item_id: 3, evidence: "עדות כלשהי" }],
    investigations: [
      {
        job_id: "28",
        item_id: 3,
        question: "מי הזוכה במכרז?",
        item_title: null,
        error: null,
        state: "queued",
        rounds: 0,
        queries: 0,
        pages_read: 0,
        outcome: null,
        started_at: null,
        finished_at: null,
      },
    ],
    ...over,
  };
}

const ENTITIES: EntitySummary[] = [
  {
    id: 100,
    name: "General Atomics",
    kind: "company",
    country: "US",
    aliases: [],
    focus: [],
    item_count: 5,
    last_seen: null,
    relevance: 0.8,
    is_watchlist: false,
    mentions_7d: 1,
    mentions_30d: 5,
    is_israeli: false,
  },
  {
    id: 101,
    name: "MQ-9B SkyGuardian",
    kind: "program",
    country: null,
    aliases: [],
    focus: [],
    item_count: 2,
    last_seen: null,
    relevance: 0.6,
    is_watchlist: false,
    mentions_7d: 0,
    mentions_30d: 2,
    is_israeli: false,
  },
];

function renderPage(path = "/items/3") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/items/:id" element={<ItemDetailPage />} />
          <Route path="/entities/:id" element={<div data-testid="entity-page-stub">entity page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getItem.mockReset();
  getEntities.mockReset();
  postItemFeedback.mockReset();
  postItemInvestigate.mockReset();
  getEntities.mockResolvedValue(ENTITIES);
  postItemFeedback.mockResolvedValue(baseItem());
  postItemInvestigate.mockResolvedValue({ job_id: "99" });
});

describe("ItemDetailPage", () => {
  it("shows an explicit 'not yet summarized' state instead of a blank area when summary_he/so_what_he are null", async () => {
    getItem.mockResolvedValue(baseItem());
    renderPage();
    await screen.findByText("General Atomics MQ-9B upgrade");
    const placeholders = await screen.findAllByText("טרם סוכם — יופק בריצה הלילית");
    expect(placeholders).toHaveLength(2); // summary_he + so_what_he
  });

  it("renders summary_he/so_what_he text when present", async () => {
    getItem.mockResolvedValue(
      baseItem({ summary_he: "תקציר אמיתי", so_what_he: "השלכה אמיתית" }),
    );
    renderPage();
    expect(await screen.findByText("תקציר אמיתי")).toBeInTheDocument();
    expect(screen.getByText("השלכה אמיתית")).toBeInTheDocument();
    expect(screen.queryByText("טרם סוכם — יופק בריצה הלילית")).not.toBeInTheDocument();
  });

  it("links a resolved entity chip to /entities/:id and leaves an unresolved name as plain text", async () => {
    getItem.mockResolvedValue(baseItem());
    renderPage();
    const link = await screen.findByRole("link", { name: "General Atomics" });
    expect(link).toHaveAttribute("href", "/entities/100");
    expect(screen.getByText("Some Random Person")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Some Random Person" })).not.toBeInTheDocument();
  });

  it("resolves edge src/dst entity ids to names and shows the evidence", async () => {
    getItem.mockResolvedValue(baseItem());
    renderPage();
    await screen.findByText("General Atomics MQ-9B upgrade");
    // "General Atomics" legitimately appears twice: once as the entity chip
    // (entities_mentioned), once as the edge's resolved src name.
    expect(await screen.findAllByText("General Atomics")).toHaveLength(2);
    expect(screen.getByText("MQ-9B SkyGuardian")).toBeInTheDocument();
    expect(screen.getByText("COMPETITOR_OF")).toBeInTheDocument();
    expect(screen.getByText("עדות כלשהי")).toBeInTheDocument();
  });

  it("links an item investigation to /investigations/:job_id using the numeric job_id from the API", async () => {
    getItem.mockResolvedValue(baseItem());
    renderPage();
    const link = await screen.findByRole("link", { name: /מי הזוכה במכרז/ });
    expect(link).toHaveAttribute("href", "/investigations/28");
  });

  it("shows the uncertainty_he block only when present", async () => {
    getItem.mockResolvedValue(baseItem({ uncertainty_he: "לא ברור מי הספק הסופי" }));
    renderPage();
    expect(await screen.findByText("לא ברור מי הספק הסופי")).toBeInTheDocument();
  });

  it("fires the investigate mutation from the 'חקור לעומק' button", async () => {
    getItem.mockResolvedValue(baseItem());
    renderPage();
    const btn = await screen.findByText("חקור לעומק");
    btn.click();
    await waitFor(() => expect(postItemInvestigate).toHaveBeenCalledWith(3, { question: null }));
  });
});
