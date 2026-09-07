import { describe, expect, it } from "vitest";
import {
  decodeHtmlEntities,
  enhanceSourceAppendixLinks,
  fixBdiSpacing,
  linkifyReportCitations,
  wrapReportTables,
} from "./reportHtml";
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

// Content review (docs/qa/content_review/CR-ui.md): `<bdi dir="ltr">JFB </bdi>האמריקאית` renders
// with the space glued to the wrong side of the isolate ("JFBהאמריקאית", no visible gap) because
// an isolate is atomic -- a trailing/leading space *inside* it doesn't separate it from the RTL
// text outside. fixBdiSpacing moves that whitespace to outside the tag.
describe("fixBdiSpacing", () => {
  it("moves a trailing space from inside a <bdi> to just after its closing tag", () => {
    const html = '<bdi dir="ltr">JFB </bdi>האמריקאית';
    expect(fixBdiSpacing(html)).toBe('<bdi dir="ltr">JFB</bdi> האמריקאית');
  });

  it("moves a leading space from inside a <bdi> to just before its opening tag", () => {
    const html = 'עם<bdi dir="ltr"> XTEND</bdi>';
    expect(fixBdiSpacing(html)).toBe('עם <bdi dir="ltr">XTEND</bdi>');
  });

  it("leaves a <bdi> with no leading/trailing whitespace untouched", () => {
    const html = '<bdi dir="ltr">XTEND</bdi> השלימה';
    expect(fixBdiSpacing(html)).toBe(html);
  });

  it("handles multiple bdi runs in the same string independently", () => {
    const html = '<bdi dir="ltr">JFB </bdi>וגם <bdi dir="ltr">XTEND </bdi>מוזגו';
    expect(fixBdiSpacing(html)).toBe('<bdi dir="ltr">JFB</bdi> וגם <bdi dir="ltr">XTEND</bdi> מוזגו');
  });

  it("passes through null/undefined", () => {
    expect(fixBdiSpacing(null)).toBe("");
    expect(fixBdiSpacing(undefined)).toBe("");
  });
});

// A report heading's plain-text TOC label is built by stripping tags from the server's HTML
// (ReportsPage.tsx `addHeadingIds`) -- entities in that HTML (`Airborne Pods &amp; Payloads`,
// correctly escaped for the server's own HTML rendering) must be decoded before landing in a
// React text child, or React re-escapes them and the raw `&amp;` string shows up on screen.
describe("decodeHtmlEntities", () => {
  it("decodes the common named entities", () => {
    expect(decodeHtmlEntities("Airborne Pods &amp; Payloads")).toBe("Airborne Pods & Payloads");
    expect(decodeHtmlEntities("&lt;tag&gt; &quot;q&quot; &apos;a&apos;")).toBe(`<tag> "q" 'a'`);
  });

  it("decodes numeric and hex entities", () => {
    expect(decodeHtmlEntities("&#65;&#66;")).toBe("AB");
    expect(decodeHtmlEntities("&#x41;&#x42;")).toBe("AB");
  });

  it("leaves unknown entities and plain text untouched", () => {
    expect(decodeHtmlEntities("&foo; בדיקה")).toBe("&foo; בדיקה");
    expect(decodeHtmlEntities("no entities here")).toBe("no entities here");
  });
});

// Content review: a raw <table> from the server has nothing around it to scroll -- on a narrow
// viewport it either overflows the page or its columns get crushed. wrapReportTables gives it a
// dedicated horizontal-scroll container (globals.css `.report-table-wrap`).
describe("wrapReportTables", () => {
  it("wraps a table in .report-table-wrap", () => {
    const html = "<p>before</p><table><tr><td>x</td></tr></table><p>after</p>";
    const out = wrapReportTables(html);
    expect(out).toBe(
      '<p>before</p><div class="report-table-wrap"><table><tr><td>x</td></tr></table></div><p>after</p>',
    );
  });

  it("wraps multiple tables independently", () => {
    const html = "<table><tr><td>1</td></tr></table><table><tr><td>2</td></tr></table>";
    const out = wrapReportTables(html);
    expect((out.match(/report-table-wrap/g) || []).length).toBe(2);
  });

  it("passes through html with no table, and null/undefined", () => {
    expect(wrapReportTables("<p>no tables</p>")).toBe("<p>no tables</p>");
    expect(wrapReportTables(null)).toBe("");
    expect(wrapReportTables(undefined)).toBe("");
  });
});
