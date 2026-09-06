import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ItemCard, ItemDetail } from "@/types/api";

const getItems = vi.fn();
const getItem = vi.fn();
const postItemFeedback = vi.fn();
const postItemInvestigate = vi.fn();
const getInvestigations = vi.fn();

const { MockApiError } = vi.hoisted(() => {
  class MockApiError extends Error {
    code: string;
    detail: unknown;
    constructor(code: string, message: string, detail: unknown) {
      super(message);
      this.code = code;
      this.detail = detail;
    }
  }
  return { MockApiError };
});

vi.mock("@/api", () => ({
  api: {
    getItems: (...args: unknown[]) => getItems(...args),
    getItem: (...args: unknown[]) => getItem(...args),
    postItemFeedback: (...args: unknown[]) => postItemFeedback(...args),
    postItemInvestigate: (...args: unknown[]) => postItemInvestigate(...args),
    getInvestigations: (...args: unknown[]) => getInvestigations(...args),
  },
  ApiError: MockApiError,
  USE_MOCKS: false,
}));

import { FeedPage } from "./FeedPage";

function makeItem(id: number, title: string): ItemCard {
  return {
    id,
    title,
    url: `https://example.test/${id}`,
    source_name: "Test Source",
    published_at: "2026-09-03T10:00:00+03:00",
    lang: "he",
    domain: "airborne_pods",
    subdomain: "targeting_pods",
    report_kind: "verified_report",
    trl: "prototype",
    geography: "IL",
    score: 90 - id,
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
  };
}

const ITEMS = [makeItem(1, "פריט ראשון"), makeItem(2, "פריט שני"), makeItem(3, "פריט שלישי")];

function renderFeedPage(initialPath = "/feed") {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/feed" element={<FeedPage />} />
          <Route path="/items/:id" element={<div data-testid="items-page-stub">item page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getItems.mockReset();
  getItem.mockReset();
  postItemFeedback.mockReset();
  postItemInvestigate.mockReset();
  getInvestigations.mockReset();
  getItems.mockResolvedValue({ total: ITEMS.length, items: ITEMS });
  postItemFeedback.mockResolvedValue(ITEMS[0]);
  postItemInvestigate.mockResolvedValue({ job_id: "inv-1", existing: false });
  getInvestigations.mockResolvedValue([]);
  const detail: ItemDetail = {
    ...ITEMS[0],
    clean_text: "טקסט מלא",
    events: [],
    edges: [],
    investigations: [],
  };
  getItem.mockResolvedValue(detail);
});

describe("FeedPage keyboard behavior", () => {
  it("selects the first row by default", async () => {
    renderFeedPage();
    const row1 = await screen.findByTestId("feed-row-1");
    expect(row1).toHaveAttribute("data-selected", "true");
  });

  it("moves selection down with 'j' and up with 'k'", async () => {
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    fireEvent.keyDown(window, { key: "j" });
    await waitFor(() =>
      expect(screen.getByTestId("feed-row-2")).toHaveAttribute("data-selected", "true"),
    );
    expect(screen.getByTestId("feed-row-1")).toHaveAttribute("data-selected", "false");

    fireEvent.keyDown(window, { key: "k" });
    await waitFor(() =>
      expect(screen.getByTestId("feed-row-1")).toHaveAttribute("data-selected", "true"),
    );
  });

  it("does not move selection past the last row", async () => {
    renderFeedPage();
    await screen.findByTestId("feed-row-1");
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "j" });
    fireEvent.keyDown(window, { key: "j" });
    await waitFor(() =>
      expect(screen.getByTestId("feed-row-3")).toHaveAttribute("data-selected", "true"),
    );
  });

  it("sets the triage level via the 1-4 number keys on the selected row", async () => {
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    fireEvent.keyDown(window, { key: "2" });
    await waitFor(() =>
      expect(postItemFeedback).toHaveBeenCalledWith(1, { user_level: "orange", comment: null }),
    );
  });

  it("archives the selected row with 'x'", async () => {
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    fireEvent.keyDown(window, { key: "x" });
    await waitFor(() =>
      expect(postItemFeedback).toHaveBeenCalledWith(1, { user_level: "archive", comment: null }),
    );
  });

  it("navigates to the full item page (/items/:id) for the selected row on Enter", async () => {
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    fireEvent.keyDown(window, { key: "Enter" });
    expect(await screen.findByTestId("items-page-stub")).toBeInTheDocument();
  });

  it("opens the inline quick-preview panel on a row double-click, without navigating", async () => {
    renderFeedPage();
    const row1 = await screen.findByTestId("feed-row-1");

    fireEvent.doubleClick(row1);
    await waitFor(() => expect(getItem).toHaveBeenCalledWith(1));
    expect(await screen.findByTestId("feed-detail-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("items-page-stub")).not.toBeInTheDocument();
  });

  it("opens the inline quick-preview panel on Space, without navigating (same as double-click)", async () => {
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    fireEvent.keyDown(window, { key: " " });
    await waitFor(() => expect(getItem).toHaveBeenCalledWith(1));
    expect(await screen.findByTestId("feed-detail-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("items-page-stub")).not.toBeInTheDocument();
  });

  it("triggers a deep-search investigation with 'i' and toasts a success confirmation (Q5-3)", async () => {
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    fireEvent.keyDown(window, { key: "i" });
    await waitFor(() => expect(postItemInvestigate).toHaveBeenCalledWith(1, { question: null }));
    expect(await screen.findByText(/חקירה נוספה לתור/)).toBeInTheDocument();
  });

  it("does not double-submit 'i' while the request is still in flight (Q5-3)", async () => {
    let resolveInvestigate: ((v: { job_id: string; existing: boolean }) => void) | null = null;
    postItemInvestigate.mockReset();
    postItemInvestigate.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveInvestigate = resolve;
        }),
    );
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    fireEvent.keyDown(window, { key: "i" });
    fireEvent.keyDown(window, { key: "i" });
    fireEvent.keyDown(window, { key: "i" });
    await waitFor(() => expect(postItemInvestigate).toHaveBeenCalledTimes(1));

    resolveInvestigate!({ job_id: "inv-9", existing: false });
    await screen.findByText(/חקירה נוספה לתור/);
  });

  it("toasts a distinct message when the backend reports an existing recent investigation (Q5-3)", async () => {
    postItemInvestigate.mockResolvedValue({ job_id: "inv-old", existing: true });
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    fireEvent.keyDown(window, { key: "i" });
    expect(await screen.findByText(/נמצאה חקירה קיימת/)).toBeInTheDocument();
  });

  it("toasts a conflict message on a 409 without treating it as a generic error (Q5-3)", async () => {
    postItemInvestigate.mockRejectedValue(new MockApiError("conflict", "conflict", { job_id: 5 }));
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    fireEvent.keyDown(window, { key: "i" });
    expect(await screen.findByText(/חקירה כבר רצה/)).toBeInTheDocument();
  });

  it("shows the 'בחקירה' indicator on a row with an active investigation from /api/investigations (Q5-3)", async () => {
    getInvestigations.mockResolvedValue([
      { job_id: "inv-1", item_id: 2, question: "x", state: "running", rounds: 1, queries: 1, pages_read: 1, outcome: null, started_at: null, finished_at: null },
    ]);
    renderFeedPage();
    expect(await screen.findByTestId("feed-row-investigating-2")).toBeInTheDocument();
    expect(screen.queryByTestId("feed-row-investigating-1")).not.toBeInTheDocument();
  });

  it("ignores keyboard shortcuts while typing in the search box", async () => {
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    const search = screen.getByLabelText("חיפוש בפיד");
    search.focus();
    fireEvent.keyDown(search, { key: "j" });

    expect(screen.getByTestId("feed-row-1")).toHaveAttribute("data-selected", "true");
  });
});

describe("FeedPage deep-link query params (U2)", () => {
  it("applies ?level=red from the Morning KPI card as an active level filter", async () => {
    renderFeedPage("/feed?level=red");
    await waitFor(() =>
      expect(getItems).toHaveBeenCalledWith(expect.objectContaining({ level: ["red"] })),
    );
  });

  it("applies ?since=24h from the Morning KPI card as an ISO cutoff roughly 24h ago", async () => {
    renderFeedPage("/feed?since=24h");
    await waitFor(() => expect(getItems).toHaveBeenCalled());
    const call = getItems.mock.calls.find((c) => c[0]?.since);
    expect(call).toBeTruthy();
    const sinceMs = new Date(call![0].since as string).getTime();
    const ageMs = Date.now() - sinceMs;
    expect(ageMs).toBeGreaterThan(23 * 60 * 60 * 1000);
    expect(ageMs).toBeLessThan(25 * 60 * 60 * 1000);
  });

  it("plain /feed (no query params) passes no since/level filter", async () => {
    renderFeedPage("/feed");
    await waitFor(() => expect(getItems).toHaveBeenCalled());
    const lastCall = getItems.mock.calls.at(-1)![0];
    expect(lastCall.since).toBeUndefined();
    expect(lastCall.level).toBeUndefined();
  });
});
