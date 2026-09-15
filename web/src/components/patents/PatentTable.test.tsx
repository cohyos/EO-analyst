import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { PatentRecord } from "@/types/api";
import { PatentTable } from "./PatentTable";

/** Round-2 mobile fix (UI-MOBILE-iphone.md #6): stubs `window.matchMedia` so
 * `useIsNarrowViewport(768)` reports "narrow" -- `PatentTable` picks the `<md:` card list over
 * the `<table>` in that state. Every pre-existing test in this file doesn't call this, so
 * `matches` stays `false` and they keep exercising the desktop table path. */
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

// `setNarrowViewport` replaces `window.matchMedia` for the rest of the file (jsdom's `window`
// persists across tests within one file) unless undone -- restore the original stub (installed by
// src/test/setup.ts, always "not narrow") after every test so a later test that doesn't call
// `setNarrowViewport` itself isn't silently left in whatever state the previous test set.
const originalMatchMedia = window.matchMedia;
afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

function makePatent(overrides: Partial<PatentRecord> = {}): PatentRecord {
  return {
    id: 1,
    pub_number: "US1234567A1",
    kind: null,
    title: "Example patent title",
    abstract: null,
    assignees: ["Acme Corp"],
    inventors: [],
    cpc: [],
    priority_date: null,
    filing_date: null,
    publication_date: "2026-09-05",
    grant_date: null,
    family_id: null,
    jurisdictions: [],
    forward_citations: null,
    backward_citations: null,
    url: null,
    source: "epo",
    subdomain: "image_processing",
    claims_summary_he: null,
    so_what_he: null,
    israel_relevance: null,
    value_score: null,
    value_reasons: [],
    created_at: "2026-09-05T00:00:00Z",
    updated_at: "2026-09-05T00:00:00Z",
    ...overrides,
  };
}

// Content review (docs/qa/content_review/CR-ui.md): the "תת-תחום" column used to render the raw
// taxonomy slug (`image_processing`) as-is -- an untranslated English key sitting next to every
// other properly-labeled column. `subdomainLabel` (lib/taxonomy.ts) now translates it, and
// `formatDate` replaces the bare ISO string previously shown for the publication date.
describe("PatentTable", () => {
  it("translates the subdomain slug instead of showing it raw", () => {
    render(<PatentTable patents={[makePatent()]} expandedId={null} onToggleExpand={() => {}} />);
    expect(screen.queryByText("image_processing")).not.toBeInTheDocument();
    expect(screen.getAllByText(/עיבוד תמונה/).length).toBeGreaterThan(0);
  });

  it("falls back to the raw id for a subdomain not in the taxonomy mirror", () => {
    render(
      <PatentTable
        patents={[makePatent({ subdomain: "future_subdomain" })]}
        expandedId={null}
        onToggleExpand={() => {}}
      />,
    );
    expect(screen.getAllByText("future_subdomain").length).toBeGreaterThan(0);
  });

  it("shows an em dash for a missing subdomain", () => {
    render(
      <PatentTable patents={[makePatent({ subdomain: null })]} expandedId={null} onToggleExpand={() => {}} />,
    );
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("formats the publication date through the shared date formatter, not as a bare ISO string", () => {
    render(<PatentTable patents={[makePatent()]} expandedId={null} onToggleExpand={() => {}} />);
    expect(screen.queryByText("2026-09-05")).not.toBeInTheDocument();
  });
});

describe("PatentTable phone card mode (round-2 mobile fix #6)", () => {
  it("renders a card list instead of a <table> on a narrow viewport", () => {
    setNarrowViewport();
    render(<PatentTable patents={[makePatent()]} expandedId={null} onToggleExpand={() => {}} />);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("Example patent title")).toBeInTheDocument();
    expect(screen.getByText("US1234567A1")).toBeInTheDocument();
    expect(screen.getByText("Acme Corp")).toBeInTheDocument();
  });

  it("keeps the desktop <table> when the viewport is not narrow", () => {
    render(<PatentTable patents={[makePatent()]} expandedId={null} onToggleExpand={() => {}} />);
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("expands the same claims/so-what detail panel on card tap", () => {
    setNarrowViewport();
    const patent = makePatent({ claims_summary_he: "תביעה 1: מערכת כיול", so_what_he: "משמעות עסקית" });
    render(<PatentTable patents={[patent]} expandedId={patent.id} onToggleExpand={() => {}} />);
    expect(screen.getByText("תביעה 1: מערכת כיול")).toBeInTheDocument();
    expect(screen.getByText("משמעות עסקית")).toBeInTheDocument();
  });

  it("toggles expansion via the card's own click handler, same target id as the table row", () => {
    setNarrowViewport();
    const onToggleExpand = vi.fn();
    const patent = makePatent();
    render(<PatentTable patents={[patent]} expandedId={null} onToggleExpand={onToggleExpand} />);
    fireEvent.click(screen.getByText("Example patent title"));
    expect(onToggleExpand).toHaveBeenCalledWith(patent.id);
  });
});
