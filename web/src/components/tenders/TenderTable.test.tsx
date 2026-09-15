import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { TenderCard } from "@/types/api";
import { TenderTable } from "./TenderTable";

const getItem = vi.fn().mockResolvedValue({
  title: "מכרז לרכש פודי ציון מטרות",
  source_name: "SAM.gov",
  published_at: "2026-09-01T00:00:00Z",
  url: "https://example.test/tender-9",
});

vi.mock("@/api", () => ({
  api: { getItem: (...args: unknown[]) => getItem(...args) },
}));

/** Stubs `window.matchMedia("(hover: hover) and (pointer: fine)")` -- same helper as
 * SourcePreviewPopover.test.tsx, needed here to exercise both the desktop and touch branches of
 * the source-link preview wired into the title cell. Also answers `useIsNarrowViewport`'s
 * `(max-width: ...)` query as `false` (not narrow) unless a test opts into the mobile card list
 * via `setNarrowViewport` below -- so every pre-existing test in this file keeps exercising the
 * desktop `<table>` path unchanged. */
function setHoverCapable(matches: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes("hover: hover") ? matches : false,
    media: query,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

/** Mobile fix (UI-MOBILE-iphone.md #5): stubs `window.matchMedia` so `useIsNarrowViewport(768)`
 * reports "narrow" -- `TenderTable` picks the `<md:` card list over the `<table>` in that state. */
function setNarrowViewport() {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes("max-width"),
    media: query,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

const tender: TenderCard = {
  id: 9,
  source: "SAM.gov",
  external_ref: "REF-1",
  title: "מכרז לרכש פודי ציון מטרות",
  agency: "US Army",
  country: "US",
  published_at: "2026-09-01T00:00:00Z",
  deadline: "2026-10-01T00:00:00Z",
  url: "https://example.test/tender-9",
  cpv_naics: [],
  summary_he: null,
  relevance: 4,
  relevance_score: 0.8,
  intake: "accepted",
  matched_terms: ["EO", "pod"],
  entities: [],
  status: "open",
  item_id: 55,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-01T00:00:00Z",
};

function renderTable(hoverCapable: boolean) {
  setHoverCapable(hoverCapable);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onToggleExpand = vi.fn();
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <TenderTable
          tenders={[tender]}
          expandedId={null}
          onToggleExpand={onToggleExpand}
          onFeedback={() => {}}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { onToggleExpand };
}

function renderNarrowTable(over: Partial<{ expandedId: number | null }> = {}) {
  setNarrowViewport();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onToggleExpand = vi.fn();
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <TenderTable
          tenders={[tender]}
          expandedId={over.expandedId ?? null}
          onToggleExpand={onToggleExpand}
          onFeedback={() => {}}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { onToggleExpand };
}

describe("TenderTable mobile card list (UI-MOBILE-iphone.md #5)", () => {
  it("renders a card (no <table>) below md, with the full title and no min-width table wrapper", () => {
    renderNarrowTable();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("מכרז לרכש פודי ציון מטרות")).toBeInTheDocument();
    expect(screen.getByText("US Army")).toBeInTheDocument();
  });

  it("clicking the card body toggles the expanded detail panel, same as the table row", () => {
    const { onToggleExpand } = renderNarrowTable();
    fireEvent.click(screen.getByText("US Army"));
    expect(onToggleExpand).toHaveBeenCalledWith(9);
  });

  it("shows the expanded detail content inside the card when expanded", () => {
    renderNarrowTable({ expandedId: 9 });
    expect(screen.getByText(/פתח פריט מקושר/)).toBeInTheDocument();
  });

  it("renders the table (no cards) at desktop width", () => {
    renderTable(true);
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});

describe("TenderTable source link preview (R10-preview)", () => {
  it("desktop: clicking the source link still opens it directly and does not toggle the row", () => {
    const { onToggleExpand } = renderTable(true);
    const link = screen.getByRole("link", { name: /מכרז לרכש פודי ציון מטרות/ });
    expect(link).toHaveAttribute("href", "https://example.test/tender-9");
    expect(link).toHaveAttribute("target", "_blank");

    fireEvent.click(link);
    expect(onToggleExpand).not.toHaveBeenCalled();
  });

  it("touch: the first tap opens the preview instead of the row toggle or the link's own navigation", () => {
    const { onToggleExpand } = renderTable(false);
    const link = screen.getByRole("link", { name: /מכרז לרכש פודי ציון מטרות/ });

    fireEvent.click(link);

    expect(onToggleExpand).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("clicking elsewhere in the row still toggles the expanded detail row", () => {
    const { onToggleExpand } = renderTable(true);
    fireEvent.click(screen.getByText("US Army"));
    expect(onToggleExpand).toHaveBeenCalledWith(9);
  });
});
