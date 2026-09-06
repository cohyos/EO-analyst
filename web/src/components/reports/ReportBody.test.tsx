import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ItemDetail, ReportCitationsResponse } from "@/types/api";

const getItem = vi.fn();
const getReportCitations = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getItem: (...args: unknown[]) => getItem(...args),
    getReportCitations: (...args: unknown[]) => getReportCitations(...args),
  },
}));

import { ReportBody } from "./ReportBody";

function renderBody(html: string, citations: ReportCitationsResponse["citations"]) {
  getReportCitations.mockResolvedValue({ report_id: 40, citations });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ReportBody html={html} reportId={40} />
    </QueryClientProvider>,
  );
}

// W4 (docs/REVIEW_2026-09-06_evening.md round 4): a `[n]` marker must scroll to and highlight its
// sources-appendix row, and both the hover tooltip and the appendix row itself must offer a real
// "פתח מקור" link that opens the source URL in a new tab -- these tests cover that, superseding
// the old U3 "click navigates to /items/:id" behavior that ReportBody.tsx used to have.
describe("ReportBody (W4 footnote-to-source behavior)", () => {
  const html =
    '<p>עובדה חשובה <a href="#src-1" class="cite">[1]</a>.</p>' +
    '<h2>נספח מקורות</h2>' +
    '<table><tbody><tr id="src-1"><td>1</td><td>כותרת</td><td>מקור</td><td>2026-09-05</td>' +
    '<td><a href="https://example.test/source-1"><bdi dir="ltr">https://example.test/source-1</bdi></a></td></tr></tbody></table>';
  const citations = {
    "1": { item_id: 42, url: "https://example.test/source-1", title: "כותרת הפריט" },
  };

  it("clicking [n] scrolls to and highlights the appendix row instead of navigating away", async () => {
    renderBody(html, citations);
    // The marker renders immediately from the raw (not-yet-linkified) html, then React swaps in
    // a brand-new DOM node once the `report-citations` query resolves and `data-n` gets attached
    // -- so the test must wait for *that* settled node rather than grabbing a possibly-stale
    // reference to the pre-query one via `findByText`.
    await waitFor(() => expect(document.querySelector("a.eo-citation[data-n]")).not.toBeNull());
    const marker = document.querySelector("a.eo-citation[data-n]")!;
    const row = document.getElementById("src-1")!;
    const scrollSpy = vi.fn();
    row.scrollIntoView = scrollSpy;

    fireEvent.click(marker);

    expect(scrollSpy).toHaveBeenCalled();
    expect(row.classList.contains("eo-appendix-highlight")).toBe(true);
  });

  it("the appendix row's link opens the real source in a new tab with a 'פתח מקור' label", async () => {
    renderBody(html, citations);
    await screen.findByText("[1]");
    const openLink = screen.getByRole("link", { name: /פתח מקור/ });
    expect(openLink).toHaveAttribute("href", "https://example.test/source-1");
    expect(openLink).toHaveAttribute("target", "_blank");
    expect(openLink).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("hovering [n] shows a tooltip with a 'פתח מקור' link to the real source URL", async () => {
    getItem.mockResolvedValue({
      title: "כותרת הפריט",
      source_name: "מקור בדיקה",
      published_at: "",
    } as Partial<ItemDetail>);
    renderBody(html, citations);
    await waitFor(() => expect(document.querySelector("a.eo-citation[data-n]")).not.toBeNull());
    const marker = document.querySelector("a.eo-citation[data-n]")!;
    fireEvent.mouseOver(marker);

    const tooltip = await screen.findByRole("tooltip");
    const openLink = tooltip.querySelector("a[href='https://example.test/source-1']");
    expect(openLink).not.toBeNull();
    expect(openLink).toHaveAttribute("target", "_blank");
    expect(openLink).toHaveAttribute("rel", "noopener noreferrer");
  });
});
