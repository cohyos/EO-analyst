import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import type { PatentRecord } from "@/types/api";
import { PatentTable } from "./PatentTable";

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
