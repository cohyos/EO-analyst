import { describe, expect, it } from "vitest";
import { renderAskMarkdown, type AskCitationLike } from "./askMarkdown";

const citations: AskCitationLike[] = [
  { n: 1, item_id: 101, title: "Elbit Systems חושפת דור חדש", url: "https://example.test/1" },
  { n: 2, item_id: null, title: "מקור חיצוני ללא פריט", url: "https://external.test/press" },
];

describe("renderAskMarkdown", () => {
  it("renders headings, bold and lists as real HTML, not literal markdown markers", () => {
    const html = renderAskMarkdown("### כותרת\n\n**מודגש** ורשימה:\n\n- פריט אחד\n- פריט שני\n");
    expect(html).toContain("<h3");
    expect(html).toContain("<strong>מודגש</strong>");
    expect(html).toContain("<ul");
    expect(html).toContain("<li");
    expect(html).not.toContain("###");
    expect(html).not.toContain("**");
  });

  it("turns a resolvable [n] marker into a clickable eo-citation chip with the right target", () => {
    const html = renderAskMarkdown("ממצא ראשון [1] וממצא שני [2].", citations);
    expect(html).toContain('class="eo-citation');
    expect(html).toContain('data-item-id="101"');
    expect(html).toContain('href="/items/101"');
    expect(html).toContain('data-url="https://external.test/press"');
    expect(html).toContain('href="https://external.test/press"');
  });

  it("leaves an unresolved [n] marker as plain text (not a citation chip)", () => {
    const html = renderAskMarkdown("ציטוט חסר [9]", citations);
    expect(html).not.toContain("eo-citation");
    // the digit itself may be <bdi>-wrapped by the bidi pass, so check for the
    // brackets + digit rather than an exact "[9]" substring.
    expect(html).toMatch(/\[(<bdi>)?9(<\/bdi>)?\]/);
  });

  it("sets dir=auto on block-level elements for correct bidi paragraph direction", () => {
    const html = renderAskMarkdown("פסקה רגילה עם טקסט.");
    expect(html).toMatch(/<p[^>]*dir="auto"/);
  });

  it("isolates a Latin/English run inside Hebrew prose with <bdi>", () => {
    const html = renderAskMarkdown("המערכת XM30 היא כטב״ם חדש.");
    expect(html).toContain("<bdi>XM30</bdi>");
  });

  it("isolates a URL with <bdi>", () => {
    const html = renderAskMarkdown("המקור: https://example.test/path נמצא כאן.");
    expect(html).toMatch(/<bdi>https:\/\/example\.test\/path<\/bdi>/);
  });

  it("wraps a Markdown table in a horizontally scrollable container", () => {
    const html = renderAskMarkdown("| א | ב |\n| --- | --- |\n| 1 | 2 |\n");
    expect(html).toContain("eo-table-wrap");
    expect(html).toContain("overflow-x-auto");
    expect(html).toContain("<table");
  });

  it("strips <script> tags and inline event handlers (no dangerouslySetInnerHTML XSS)", () => {
    const html = renderAskMarkdown('טקסט <script>alert(1)</script> ו-<img src=x onerror="alert(1)">');
    expect(html).not.toContain("<script");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("alert(1)");
  });

  it("strips a javascript: URL from a citation source instead of emitting it as href", () => {
    const evil: AskCitationLike[] = [
      { n: 3, item_id: null, title: "רע", url: "javascript:alert(1)" },
    ];
    const html = renderAskMarkdown("ראה [3]", evil);
    expect(html).not.toContain("javascript:");
  });

  it("does not bidi-wrap text inside a code block", () => {
    const html = renderAskMarkdown("```\nconst x = 1;\n```");
    expect(html).not.toContain("<bdi>");
    expect(html).toContain("const x = 1;");
  });

  // R6-ui (06-ask.spec.ts, iphone-safari): the chat backend was observed emitting a heading
  // marker glued onto the end of the previous sentence with no line break at all, which `marked`
  // (correctly, per CommonMark) parses as plain paragraph text -- the literal "###" then leaked
  // into the rendered answer instead of becoming a heading.
  it("renders a ### heading marker as a real heading even when glued onto the previous sentence with no newline", () => {
    const html = renderAskMarkdown("סיכום קצר.### עובדות מרכזיות\n\nפרט ראשון.");
    expect(html).toContain("<h3");
    expect(html).toContain("עובדות מרכזיות");
    expect(html).not.toContain("###");
  });

  it("does not touch a heading that is already correctly on its own line", () => {
    const html = renderAskMarkdown("פסקה.\n\n## כותרת תקינה\n\nעוד פסקה.");
    expect(html).toContain("<h2");
    expect(html).not.toContain("##");
  });

  it("does not mangle a bare '#' with no following space (e.g. a hashtag-like token)", () => {
    const html = renderAskMarkdown("המספר #123 אינו כותרת.");
    expect(html).not.toMatch(/<h[1-6]/);
    // the digits may be <bdi>-wrapped by the bidi pass, same as the "[9]" case above.
    expect(html).toMatch(/#(<bdi>)?123(<\/bdi>)?/);
  });
});
