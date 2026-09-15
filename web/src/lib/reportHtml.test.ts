import { describe, expect, it } from "vitest";
import {
  decodeHtmlEntities,
  enhanceSourceAppendixLinks,
  fixBdiSpacing,
  groupAdjacentCitations,
  linkifyReportCitations,
  normalizeReportProse,
  wrapReportTables,
} from "./reportHtml";
import type { ReportCitation } from "@/types/api";

describe("historical report prose", () => {
  it("removes raw headings without changing citation links or interpreting escaped markup", () => {
    const out = normalizeReportProse('<ul><li>ממצא ### הקשר <a href="#src-1">[1]</a> &lt;script&gt;</li></ul>');
    expect(out).not.toContain("###");
    expect(out).toContain("<br>");
    expect(out).toContain('<a href="#src-1">[1]</a>');
    expect(out).toContain("&lt;script&gt;");
  });
});

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
  // Round-2 mobile fix (UI-MOBILE-iphone.md #1): a table with <= 4 columns is wrapped
  // `dir="rtl"` and gets the extra `--narrow` class (globals.css lets it wrap instead of forcing
  // a horizontal scrollbar) -- a 1-column table like this one qualifies.
  it("wraps a narrow (<=4 column) table with the --narrow class and dir=rtl, no scroll hint", () => {
    const html = "<p>before</p><table><tr><td>x</td></tr></table><p>after</p>";
    const out = wrapReportTables(html);
    expect(out).toBe(
      '<p>before</p><div class="report-table-wrap report-table-wrap--narrow" dir="rtl">' +
        '<table><tr><td>x</td></tr></table></div><p>after</p>',
    );
  });

  it("wraps a wide (>4 column) table with the --stacked class, a scroll hint, and no --narrow class", () => {
    const html =
      "<table><tr><th>a</th><th>b</th><th>c</th><th>d</th><th>e</th></tr><tr><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td></tr></table>";
    const out = wrapReportTables(html);
    expect(out).toContain('<div class="report-table-wrap report-table-wrap--stacked" dir="rtl">');
    expect(out).not.toContain("report-table-wrap--narrow");
    expect(out).toContain("report-table-scroll-hint");
    // the hint renders before the <table>, inside the wrapper
    expect(out.indexOf("report-table-scroll-hint")).toBeLessThan(out.indexOf("<table>"));
  });

  it("wraps multiple tables independently", () => {
    const html = "<table><tr><td>1</td></tr></table><table><tr><td>2</td></tr></table>";
    const out = wrapReportTables(html);
    expect((out.match(/class="report-table-wrap/g) || []).length).toBe(2);
  });

  it("passes through html with no table, and null/undefined", () => {
    expect(wrapReportTables("<p>no tables</p>")).toBe("<p>no tables</p>");
    expect(wrapReportTables(null)).toBe("");
    expect(wrapReportTables(undefined)).toBe("");
  });

  // Round-4 mobile fix (fix #1): >4-column tables stack into cards below 768px (globals.css
  // `.report-table-wrap--stacked`) -- each body `<td>` needs `data-label="<header text>"` for the
  // `label: value` line CSS builds via `content: attr(data-label)`.
  describe("data-label attachment for the stacked mobile layout", () => {
    it("labels every body <td> from the matching header <th>, leaving the header row itself untouched", () => {
      const html =
        "<table><thead><tr><th>תאריך</th><th>סוג</th><th>צדדים</th><th>לקוח</th><th>סכום</th></tr></thead>" +
        "<tbody><tr><td>2026-09-01</td><td>עסקה</td><td>א, ב</td><td>לקוח X</td><td>1M$</td></tr></tbody></table>";
      const out = wrapReportTables(html);
      expect(out).toContain('<td data-label="תאריך">2026-09-01</td>');
      expect(out).toContain('<td data-label="סוג">עסקה</td>');
      expect(out).toContain('<td data-label="צדדים">א, ב</td>');
      expect(out).toContain('<td data-label="לקוח">לקוח X</td>');
      expect(out).toContain('<td data-label="סכום">1M$</td>');
      // the header row's own cells are <th>, never rewritten to carry data-label
      expect(out).not.toContain("<th data-label");
    });

    it("labels every row when a table has multiple body rows", () => {
      const html =
        "<table><thead><tr><th>a</th><th>b</th><th>c</th><th>d</th><th>e</th></tr></thead>" +
        "<tbody><tr><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td></tr>" +
        "<tr><td>6</td><td>7</td><td>8</td><td>9</td><td>10</td></tr></tbody></table>";
      const out = wrapReportTables(html);
      // one data-label="a" per body row (2 rows), never on the header row's own <th>
      expect((out.match(/data-label="a"/g) || []).length).toBe(2);
      expect(out).toContain('<td data-label="a">1</td>');
      expect(out).toContain('<td data-label="a">6</td>');
      expect(out).toContain('<td data-label="e">10</td>');
    });

    it("HTML-escapes header text carrying an ampersand/quote in the data-label attribute", () => {
      const html =
        "<table><thead><tr><th>שם &amp; תפקיד</th><th>b</th><th>c</th><th>d</th><th>e</th></tr></thead>" +
        "<tbody><tr><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td></tr></tbody></table>";
      const out = wrapReportTables(html);
      expect(out).toContain('data-label="שם &amp; תפקיד"');
      // decoded once, then re-escaped -- never double-escaped to &amp;amp;
      expect(out).not.toContain("&amp;amp;");
    });

    it("does not attach data-label on a <=4 column (--narrow) table", () => {
      const html = "<table><tr><th>a</th><th>b</th></tr><tr><td>1</td><td>2</td></tr></table>";
      const out = wrapReportTables(html);
      expect(out).toContain("report-table-wrap--narrow");
      expect(out).not.toContain("data-label");
    });
  });
});

// Round-3 mobile fix (UI-MOBILE-iphem-r3.md #6): 2+ adjacent .eo-citation markers get reordered by
// the browser's bidi algorithm inside RTL prose ("[1] [2] [3]" renders as "[1] [3] [2]") because
// each <a> is its own atomic LTR embedding -- wrapping the whole run in one dir="ltr" span makes
// it a single embedding instead, preserving authored (ascending) order.
describe("groupAdjacentCitations", () => {
  const a = (n: number) => `<a class="eo-citation" data-n="${n}">[${n}]</a>`;

  it("wraps 3 adjacent citation markers in one dir=ltr span, gluing to the preceding word with &nbsp;", () => {
    const html = `עובדה חשובה ${a(1)} ${a(3)} ${a(2)} וסיפא.`;
    const out = groupAdjacentCitations(html);
    expect(out).toBe(
      `עובדה חשובה&nbsp;<span dir="ltr" class="eo-citation-group">${a(1)} ${a(3)} ${a(2)}</span> וסיפא.`,
    );
  });

  it("leaves a single, non-adjacent citation marker untouched (order is irrelevant for one)", () => {
    const html = `הושלמה מיזוג ${a(2)}.`;
    expect(groupAdjacentCitations(html)).toBe(html);
  });

  it("does not group two citations separated by real prose text", () => {
    const html = `${a(1)} טקסט באמצע ${a(2)}`;
    expect(groupAdjacentCitations(html)).toBe(html);
  });

  it("groups a run that starts the string (no leading whitespace to glue)", () => {
    const html = `${a(1)} ${a(2)} אחרי`;
    expect(groupAdjacentCitations(html)).toBe(
      `<span dir="ltr" class="eo-citation-group">${a(1)} ${a(2)}</span> אחרי`,
    );
  });

  it("passes through null/undefined and text with no citations", () => {
    expect(groupAdjacentCitations(null)).toBe("");
    expect(groupAdjacentCitations(undefined)).toBe("");
    expect(groupAdjacentCitations("<p>no citations here</p>")).toBe("<p>no citations here</p>");
  });
});
