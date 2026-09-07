import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { Corroboration, ItemCard } from "@/types/api";
import { FeedDetailPanel } from "./FeedDetailPanel";

const postItemFeedback = vi.fn();
const postItemInvestigate = vi.fn();
const postItemCorroborate = vi.fn();

vi.mock("@/api", () => ({
  api: {
    postItemFeedback: (...args: unknown[]) => postItemFeedback(...args),
    postItemInvestigate: (...args: unknown[]) => postItemInvestigate(...args),
    postItemCorroborate: (...args: unknown[]) => postItemCorroborate(...args),
  },
}));

function makeItem(over: Partial<ItemCard> = {}): ItemCard {
  return {
    id: 5,
    title: "פריט לבדיקה",
    url: "https://example.test/5",
    source_name: "Test Source",
    published_at: "2026-09-05T10:00:00+03:00",
    lang: "he",
    domain: "airborne_pods",
    subdomain: null,
    report_kind: "verified_report",
    trl: null,
    geography: null,
    score: 8,
    level: "orange",
    triage_reason: "נימוק",
    summary_he: null,
    so_what_he: null,
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
    ...over,
  };
}

// Mirrors how FeedPage really wires this up: FeedDetailPanel receives `item` as a prop, but
// FeedPage's own `openItemQuery` (queryKey ["item", id]) is what the recheck mutation's
// `onSuccess` patches via `queryClient.setQueryData`. A bare `<FeedDetailPanel item={...} />`
// with no query subscribed to that key would never see the patched value re-render in -- this
// thin wrapper subscribes the same way FeedPage does so the update-propagation tests are real.
function ItemQuerySubscriber({ initial }: { initial: ItemCard }) {
  const { data } = useQuery({
    queryKey: ["item", initial.id],
    queryFn: () => Promise.resolve(initial),
    initialData: initial,
  });
  return <FeedDetailPanel item={data} onClose={() => {}} />;
}

function renderPanel(item: ItemCard) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ItemQuerySubscriber initial={item} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  postItemFeedback.mockReset();
  postItemInvestigate.mockReset();
  postItemCorroborate.mockReset();
});

describe("FeedDetailPanel corroboration (CORR)", () => {
  it("shows the single_source badge in the header when the item has it", () => {
    renderPanel(makeItem({ corroboration: { status: "single_source", count: 0, sources: [], checked_at: null } }));
    expect(screen.getByText("מקור יחיד")).toBeInTheDocument();
  });

  it("shows a subtle 'לא נבדק' chip in the drawer when the item has never been checked", () => {
    renderPanel(makeItem({ corroboration: undefined }));
    expect(screen.getByText("לא נבדק")).toBeInTheDocument();
  });

  it("re-check button calls postItemCorroborate(id) and updates the badge from the response", async () => {
    const updated: Corroboration = {
      status: "corroborated",
      count: 1,
      sources: [
        { item_id: 900, source_name: "Wire Service", url: "https://x.test", published_at: null, kind: "same_event" },
      ],
      checked_at: "2026-09-07T08:00:00+03:00",
    };
    postItemCorroborate.mockResolvedValue(updated);
    renderPanel(makeItem({ corroboration: { status: "single_source", count: 0, sources: [], checked_at: null } }));

    expect(screen.getByText("מקור יחיד")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("corroboration-recheck-button"));

    await waitFor(() => expect(postItemCorroborate).toHaveBeenCalledWith(5));
    expect(await screen.findByText("מאומת ב-1 מקורות")).toBeInTheDocument();
    expect(screen.queryByText("מקור יחיד")).not.toBeInTheDocument();
  });

  it("disables the re-check button while the request is pending", async () => {
    let resolve!: (v: Corroboration) => void;
    postItemCorroborate.mockReturnValue(
      new Promise<Corroboration>((r) => {
        resolve = r;
      }),
    );
    renderPanel(makeItem());
    const button = screen.getByTestId("corroboration-recheck-button");
    fireEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    resolve({ status: "single_source", count: 0, sources: [], checked_at: null });
    await waitFor(() => expect(button).not.toBeDisabled());
  });

  it("shows a Hebrew error toast when the re-check request fails", async () => {
    postItemCorroborate.mockRejectedValue(new Error("network error"));
    renderPanel(makeItem());
    fireEvent.click(screen.getByTestId("corroboration-recheck-button"));
    expect(await screen.findByText("בדיקת האימות נכשלה — נסה שוב")).toBeInTheDocument();
  });
});
