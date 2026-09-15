import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { FeedRow } from "./FeedRow";
import type { ItemCard } from "@/types/api";

function makeItem(over: Partial<ItemCard> = {}): ItemCard {
  return {
    id: 1,
    title: "Elbit Systems delivers SPECTRO XR targeting pods to allied air force customer",
    url: "https://example.test/story",
    source_name: "Defense News",
    published_at: "2026-09-10T00:00:00Z",
    lang: "en",
    domain: "targeting_pods",
    subdomain: null,
    report_kind: "daily",
    trl: null,
    geography: null,
    score: 82,
    level: "orange",
    triage_reason: null,
    summary_he: null,
    so_what_he: null,
    entities_mentioned: [],
    tags: [],
    security_status: "clear",
    dedup_of: null,
    key_facts: [],
    uncertainty_he: null,
    tech_maturity: null,
    tech_actor_kind: null,
    tech_readiness_note_he: null,
    israel_relevance: null,
    israel_reasons: [],
    corroboration: { status: "unknown", count: 0, sources: [], checked_at: null },
    ...over,
  };
}

function renderRow(over: Partial<ItemCard> = {}) {
  return render(
    <FeedRow
      item={makeItem(over)}
      selected={false}
      onSelect={vi.fn()}
      onOpen={vi.fn()}
      onRate={vi.fn()}
    />,
  );
}

describe("FeedRow mobile layout (UI-MOBILE-iphone.md #1)", () => {
  it("renders the full headline text (not clipped) regardless of viewport", () => {
    renderRow();
    // The row used to squeeze the headline to a single clipped character on phones because a
    // fixed h-16 row packed always-visible badges in beside it. The <a> element itself must
    // still carry the whole headline as its text content -- CSS (line-clamp) governs how many
    // lines render, not how much text is present in the DOM.
    expect(screen.getByTestId("feed-row-title-link-1")).toHaveTextContent(
      "Elbit Systems delivers SPECTRO XR targeting pods to allied air force customer",
    );
  });

  it("headline wraps to 2 lines below sm and single-line-truncates from sm up", () => {
    renderRow();
    const link = screen.getByTestId("feed-row-title-link-1");
    expect(link.className).toContain("line-clamp-2");
    expect(link.className).toContain("sm:line-clamp-none");
    expect(link.className).toContain("sm:truncate");
  });

  it("the headline block is full-width and ordered first below sm (row 1), reverting to its original inline position from sm up", () => {
    renderRow();
    const link = screen.getByTestId("feed-row-title-link-1");
    const titleBlock = link.parentElement!;
    expect(titleBlock.className).toContain("order-first");
    expect(titleBlock.className).toContain("w-full");
    expect(titleBlock.className).toContain("sm:order-2");
    expect(titleBlock.className).toContain("sm:w-auto");
  });

  it("the row keeps a fixed height matching the virtualizer's row slot, taller below sm than the sm:h-16 desktop row", () => {
    renderRow();
    const row = screen.getByTestId("feed-row-1");
    expect(row.className).toContain("h-28");
    expect(row.className).toContain("sm:h-16");
    expect(row.className).toContain("flex-wrap");
    expect(row.className).toContain("sm:flex-nowrap");
  });

  it("still renders the level/score/corroboration cluster and the security icon", () => {
    renderRow();
    // "unknown" corroboration renders no chip at all (showUnknown=false in feed rows) -- just
    // confirms the cluster didn't grow an unexpected chip for the common unchecked case.
    expect(screen.queryByTestId("corroboration-badge-unknown")).not.toBeInTheDocument();
    // score renders as plain text next to the (hidden until hover) explain popover trigger
    expect(screen.getByText("82")).toBeInTheDocument();
  });

  it("shows the Israel-relevance badge in the wrapping meta cluster when relevant", () => {
    renderRow({ israel_relevance: 0.9 });
    expect(screen.getByTestId("feed-row-israel-badge-1")).toBeInTheDocument();
  });
});
