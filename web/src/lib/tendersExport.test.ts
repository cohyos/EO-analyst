import { describe, expect, it } from "vitest";
import { buildTendersHtml, buildTendersShareText } from "./tendersExport";
import type { ForecastCard, TenderCard } from "@/types/api";

function makeTender(over: Partial<TenderCard> = {}): TenderCard {
  return {
    id: 1,
    source: "sam_gov_api",
    external_ref: "REF-1",
    title: "Targeting Pod Sustainment IDIQ",
    agency: "US Air Force",
    country: "US",
    published_at: "2026-08-20T14:00:00+00:00",
    deadline: "2026-09-10",
    url: "https://sam.gov/opp/example",
    cpv_naics: ["336413"],
    summary_he: null,
    relevance: 4,
    relevance_score: 0.8,
    intake: "accepted",
    matched_terms: ["targeting pod"],
    entities: [],
    status: "open",
    item_id: 42,
    created_at: "2026-08-20T14:05:00+00:00",
    updated_at: "2026-09-01T06:00:00+00:00",
    product_lines: ["targeting_pods"],
    ...over,
  };
}

function makeForecast(over: Partial<ForecastCard> = {}): ForecastCard {
  return {
    id: 1,
    platform: "F-35 Block 4 sustainment",
    buyer_country: "US",
    trigger_event_id: null,
    trigger_item_id: 12,
    payload_need: "פוד כיוון מהדור הבא",
    candidate_vendors: ["Elbit Systems"],
    likelihood: 0.78,
    window_from: "2026-10-01",
    window_to: "2027-01-31",
    rationale_he: "בהתבסס על ההכרזה צפוי פרסום RFI רשמי.",
    sources: ["https://example-source.test/articles/2000"],
    created_at: "2026-09-03T20:30:00+00:00",
    updated_at: "2026-09-03T20:30:00+00:00",
    ...over,
  };
}

const filters = {
  status: "" as const,
  country: "",
  q: "",
  productLines: [],
  showClosedArchived: false,
};

const baseOptions = {
  filters,
  locale: "he" as const,
  appUrl: "https://eoa.internal.tailnet/tenders",
  generatedAt: new Date("2026-09-15T10:00:00Z"),
};

describe("buildTendersHtml", () => {
  it("returns a complete standalone RTL Hebrew document with no external assets/scripts", () => {
    const html = buildTendersHtml({ tenders: [makeTender()], ...baseOptions });
    expect(html).toMatch(/^<!doctype html>/);
    expect(html).toContain('<html lang="he" dir="rtl">');
    expect(html).toContain("מכרזים והזדמנויות — EO-Analyst");
    expect(html).not.toMatch(/<script/);
    expect(html).not.toMatch(/<link[^>]+href=/);
    expect(html).not.toMatch(/src="https?:/);
  });

  it("escapes a hostile title instead of injecting it as markup", () => {
    const html = buildTendersHtml({
      tenders: [makeTender({ title: '<script>alert(1)</script>' })],
      ...baseOptions,
    });
    expect(html).not.toContain("<script>alert(1)</script>");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
  });

  it("links a public notice URL but renders a private/local one as plain text", () => {
    const html = buildTendersHtml({
      tenders: [
        makeTender({ id: 1, title: "Public notice", url: "https://sam.gov/opp/public" }),
        makeTender({ id: 2, title: "Local notice", url: "http://127.0.0.1:8765/items/1" }),
        makeTender({ id: 3, title: "Private LAN notice", url: "http://10.0.0.5/opp" }),
      ],
      ...baseOptions,
    });
    expect(html).toContain('<a href="https://sam.gov/opp/public"');
    expect(html).not.toMatch(/href="[^"]*127\.0\.0\.1/);
    expect(html).not.toMatch(/href="[^"]*10\.0\.0\.5/);
    expect(html).toContain("Local notice");
    expect(html).toContain("Private LAN notice");
  });

  it("always keeps the appUrl footer link even when it is a private/tailnet address", () => {
    const html = buildTendersHtml({ tenders: [makeTender()], ...baseOptions });
    expect(html).toContain('<a href="https://eoa.internal.tailnet/tenders">');
    expect(html).toContain("פתח באפליקציה (רשת פנימית)");
  });

  it("summarizes the active filters and the visible count", () => {
    const html = buildTendersHtml({
      tenders: [makeTender(), makeTender({ id: 2 })],
      ...baseOptions,
      filters: {
        status: "open",
        country: "US",
        q: "pod",
        productLines: ["targeting_pods"],
        showClosedArchived: true,
      },
    });
    expect(html).toContain("סטטוס: פתוח");
    expect(html).toContain("מדינה: US");
    expect(html).toContain("חיפוש: &quot;pod&quot;");
    expect(html).toContain("כולל סגורים/ארכיון: כן");
    expect(html).toContain("2 מכרזים בתצוגה");
  });

  it("omits the forecasts section entirely when none are given, and renders it when provided", () => {
    const withoutForecasts = buildTendersHtml({ tenders: [makeTender()], ...baseOptions });
    expect(withoutForecasts).not.toContain("תחזית מכרזים");

    const withForecasts = buildTendersHtml({
      tenders: [makeTender()],
      forecasts: [makeForecast()],
      ...baseOptions,
    });
    expect(withForecasts).toContain("תחזית מכרזים");
    expect(withForecasts).toContain("F-35 Block 4 sustainment");
    expect(withForecasts).toContain("Elbit Systems");
    expect(withForecasts).toContain('href="https://example-source.test/articles/2000"');
  });

  it("renders both a table and a stacked-card fallback gated by a max-width media query", () => {
    const html = buildTendersHtml({ tenders: [makeTender()], ...baseOptions });
    expect(html).toContain("<table>");
    expect(html).toContain('class="cards"');
    expect(html).toContain("@media (max-width: 640px)");
  });

  it("renders empty optional fields as an em dash", () => {
    const html = buildTendersHtml({
      tenders: [makeTender({ agency: null, country: null, product_lines: [] })],
      ...baseOptions,
    });
    // agency/country cells fall back to "—" (bdi() returns the dash unwrapped for empty input).
    expect(html).toMatch(/<td>—<\/td>/);
  });
});

describe("buildTendersShareText", () => {
  it("renders one line per tender: title — buyer — deadline — URL", () => {
    const text = buildTendersShareText([makeTender()]);
    expect(text).toBe(
      "Targeting Pod Sustainment IDIQ — US Air Force — ⁦10.09.2026⁩ — https://sam.gov/opp/example",
    );
  });

  it("caps the list at ~60 tenders and appends a count of the remainder", () => {
    const tenders = Array.from({ length: 65 }, (_, i) => makeTender({ id: i + 1, title: `Tender ${i + 1}` }));
    const text = buildTendersShareText(tenders);
    const lines = text.split("\n");
    expect(lines).toHaveLength(61);
    expect(lines.at(-1)).toBe("…ועוד 5");
  });
});
