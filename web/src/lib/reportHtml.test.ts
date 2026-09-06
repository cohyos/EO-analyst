import { describe, expect, it } from "vitest";
import { enhanceSourceAppendixLinks, linkifyReportCitations } from "./reportHtml";
import type { ReportCitation } from "@/types/api";

describe("linkifyReportCitations", () => {
  it("augments a server-rendered .cite anchor in place instead of nesting a new <a> (F23)", () => {
    const html = '<p>עובדה חשובה <a href="#src-1" class="cite">[1]</a>.</p>';
    const citations: Record<string, ReportCitation> = {
      "1": { item_id: 42, url: "https://example.com/x", title: "Example" },
    };
    const out = linkifyReportCitations(html, citations);
    // Exactly one <a> for this citation -- no nested anchor.
    expect((out.match(/<a\b/g) || []).length).toBe(1);
    expect(out).toContain('href="#src-1"');
    expect(out).toContain('class="cite eo-citation"');
    expect(out).toContain('data-item-id="42"');
    expect(out).toContain('title="Example"');
  });

  it("W4: sets both data-item-id and data-url when the citation resolves to both, plus data-n for the scroll/highlight target", () => {
    const html = '<a href="#src-1" class="cite">[1]</a>';
    const citations: Record<string, ReportCitation> = {
      "1": { item_id: 42, url: "https://example.com/x", title: "Example" },
    };
    const out = linkifyReportCitations(html, citations);
    expect(out).toContain('data-item-id="42"');
    expect(out).toContain('data-url="https://example.com/x"');
    expect(out).toContain('data-n="1"');
  });

  it("leaves an unresolved .cite anchor's href alone so it still jumps to the appendix", () => {
    const html = '<a href="#src-9" class="cite">[9]</a>';
    const out = linkifyReportCitations(html, {});
    expect(out).toContain('href="#src-9"');
    expect(out).not.toContain("eo-citation");
  });

  it("still wraps a bare [n] marker from an older stored report", () => {
    const html = "<p>עובדה חשובה [3].</p>";
    const citations: Record<string, ReportCitation> = {
      "3": { item_id: 7, url: null, title: null },
    };
    const out = linkifyReportCitations(html, citations);
    expect(out).toContain('data-item-id="7"');
    expect(out).toContain('href="/items/7"');
  });

  it("opens the source URL for a bare marker with no resolvable item", () => {
    const html = "[5]";
    const citations: Record<string, ReportCitation> = {
      "5": { item_id: null, url: "https://example.com/y", title: null },
    };
    const out = linkifyReportCitations(html, citations);
    expect(out).toContain('href="https://example.com/y"');
    expect(out).toContain('target="_blank"');
  });

  it("passes through a bare marker with no citation data at all", () => {
    expect(linkifyReportCitations("[2]", {})).toBe("[2]");
    expect(linkifyReportCitations(null, null)).toBe("");
  });
});

describe("enhanceSourceAppendixLinks (W4)", () => {
  it("adds target=_blank/rel=noopener and a real 'פתח מקור' label to an appendix row's link", () => {
    const html =
      '<tr id="src-1"><td>1</td><td>כותרת</td><td>מקור</td><td>2026-09-05</td>' +
      '<td><a href="https://example.com/a"><bdi dir="ltr">https://example.com/a</bdi></a></td></tr>';
    const out = enhanceSourceAppendixLinks(html);
    expect(out).toContain('href="https://example.com/a"');
    expect(out).toContain('target="_blank"');
    expect(out).toContain('rel="noopener noreferrer"');
    expect(out).toContain("פתח מקור");
    // Still exactly one <a> in the row -- rewritten in place, not appended alongside the old one.
    expect((out.match(/<a\b/g) || []).length).toBe(1);
  });

  it("leaves a linkless appendix row (no source URL) untouched", () => {
    const html = '<tr id="src-2"><td>2</td><td>כותרת</td><td>מקור</td><td>—</td><td>—</td></tr>';
    expect(enhanceSourceAppendixLinks(html)).toBe(html);
  });

  it("is idempotent -- running it twice doesn't double-wrap an already-enhanced row", () => {
    const html =
      '<tr id="src-1"><td>1</td><td>t</td><td>s</td><td>d</td>' +
      '<td><a href="https://example.com/a"><bdi dir="ltr">https://example.com/a</bdi></a></td></tr>';
    const once = enhanceSourceAppendixLinks(html);
    const twice = enhanceSourceAppendixLinks(once);
    expect(twice).toBe(once);
  });

  it("passes through null/undefined", () => {
    expect(enhanceSourceAppendixLinks(null)).toBe("");
    expect(enhanceSourceAppendixLinks(undefined)).toBe("");
  });
});
