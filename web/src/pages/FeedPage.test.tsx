import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ItemCard, ItemDetail } from "@/types/api";

const getItems = vi.fn();
const getItem = vi.fn();
const postItemFeedback = vi.fn();
const postItemInvestigate = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getItems: (...args: unknown[]) => getItems(...args),
    getItem: (...args: unknown[]) => getItem(...args),
    postItemFeedback: (...args: unknown[]) => postItemFeedback(...args),
    postItemInvestigate: (...args: unknown[]) => postItemInvestigate(...args),
  },
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
  };
}

const ITEMS = [makeItem(1, "פריט ראשון"), makeItem(2, "פריט שני"), makeItem(3, "פריט שלישי")];

function renderFeedPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/feed"]}>
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
  getItems.mockResolvedValue({ total: ITEMS.length, items: ITEMS });
  postItemFeedback.mockResolvedValue(ITEMS[0]);
  postItemInvestigate.mockResolvedValue({ job_id: "inv-1" });
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

  it("triggers a deep-search investigation with 'i'", async () => {
    renderFeedPage();
    await screen.findByTestId("feed-row-1");

    fireEvent.keyDown(window, { key: "i" });
    await waitFor(() => expect(postItemInvestigate).toHaveBeenCalledWith(1, { question: null }));
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
