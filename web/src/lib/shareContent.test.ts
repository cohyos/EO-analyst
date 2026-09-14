import { describe, expect, it } from "vitest";
import { composeShareUrl, prepareShareContent } from "./shareContent";

describe("share content", () => {
  it("preserves Hebrew, tables and real sources, excluding controls and local links", () => {
    const element = document.createElement("div");
    element.innerHTML = `<h1>דוח בדיקה</h1><p>ניתוח בעברית</p>
      <table><tr><th>מוצר</th><th>טווח</th></tr><tr><td>EO</td><td>20 km</td></tr></table>
      <a href="https://example.com/source?q=eo&lang=he">מקור</a>
      <a href="http://127.0.0.1:8765/items/1">פריט</a><a href="javascript:alert(1)">bad</a>
      <div data-share-actions>COPY CONTROLS</div><button>DELETE</button><script>alert(1)</script>`;
    const result = prepareShareContent(element);
    expect(result.text).toContain("ניתוח בעברית");
    expect(result.text).toContain("EO\t20 km");
    expect(result.text).toContain("https://example.com/source?q=eo&lang=he");
    expect(result.html).toContain("<table>");
    expect(result.html).toContain('href="https://example.com/source?q=eo&amp;lang=he"');
    expect(result.html).not.toMatch(
      /javascript:|127\.0\.0\.1|<script|COPY CONTROLS|DELETE/,
    );
  });

  it("adds citation URLs supplied separately and rejects private URLs", () => {
    const element = document.createElement("div");
    element.textContent = "תשובה [1]";
    const result = prepareShareContent(element, "ניתוח", [
      { url: "https://example.org/doc", title: "מקור ראשון" },
      { url: "http://localhost:8765/reports/1" },
      { url: "http://192.168.1.2/private" },
    ]);
    expect(result.text).toContain("מקור ראשון: https://example.org/doc");
    expect(result.text).not.toMatch(/localhost|192\.168/);
  });

  it("preserves numbered citations rendered as interactive buttons", () => {
    const element = document.createElement("div");
    element.innerHTML =
      '<p>ממצא <button aria-describedby="citation-2-tip">2</button></p>';
    const result = prepareShareContent(element, "ניתוח", [
      { n: 2, url: "https://example.org/evidence", title: "Evidence" },
    ]);
    expect(result.text).toContain("ממצא [2]");
    expect(result.text).toContain("[2] Evidence: https://example.org/evidence");
    expect(result.html).toContain('href="https://example.org/evidence">[2]</a>');
  });

  it("encodes full short content and offers pasting without truncating long reports", () => {
    const content = {
      title: "דוח & בדיקה",
      text: "תוכן\nhttps://example.com/?a=1&b=2",
      html: "",
    };
    const email = composeShareUrl("email", content);
    expect(new URLSearchParams(email.url.split("?")[1]).get("body")).toBe(content.text);
    expect(
      new URL(composeShareUrl("whatsapp", content).url).searchParams.get("text"),
    ).toBe(content.text);
    const long = { ...content, text: "תוכן מלא ".repeat(3000) };
    expect(composeShareUrl("whatsapp", long)).toEqual({
      needsPaste: true,
      url: "https://wa.me/",
    });
    expect(composeShareUrl("email", long).needsPaste).toBe(true);
    expect(long.text.length).toBeGreaterThan(20000);
  });
});
