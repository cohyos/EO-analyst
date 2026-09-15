import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { renderBidiRuns, renderBidiText } from "./bidiText";

// Content review (docs/qa/content_review/CR-ui.md): a plain-text field like
// `investigation.question` embeds a quoted English title inline inside Hebrew prose
// (`אמת והרחב את הדיווח "The all-new 15-300 mm..." : מי הצדדים...`). With no markup at all, the
// quote marks are bidi-neutral and can land in a visually confusing spot in RTL flow.
// renderBidiText wraps just the quoted Latin span in a real <bdi dir="ltr">.
describe("renderBidiText", () => {
  it("wraps a quoted Latin span in a dir=ltr bdi, keeping the quote marks as plain text", () => {
    const { container } = render(
      <div>{renderBidiText('אמת "The all-new zoom" ומה המשמעות?')}</div>,
    );
    const bdi = container.querySelector("bdi");
    expect(bdi).not.toBeNull();
    expect(bdi!.getAttribute("dir")).toBe("ltr");
    expect(bdi!.textContent).toBe("The all-new zoom");
    expect(container.textContent).toBe('אמת "The all-new zoom" ומה המשמעות?');
  });

  it("wraps multiple quoted Latin spans independently", () => {
    const { container } = render(<div>{renderBidiText('בדוק "Alpha" וגם "Beta" בבקשה')}</div>);
    const bdis = container.querySelectorAll("bdi");
    expect(bdis.length).toBe(2);
    expect(bdis[0].textContent).toBe("Alpha");
    expect(bdis[1].textContent).toBe("Beta");
  });

  it("leaves a Hebrew-quoted phrase untouched (no Latin-starting quoted span)", () => {
    const { container } = render(<div>{renderBidiText('הוא אמר "שלום" לי')}</div>);
    expect(container.querySelectorAll("bdi").length).toBe(0);
    expect(container.textContent).toBe('הוא אמר "שלום" לי');
  });

  it("leaves plain text with no quotes untouched", () => {
    const { container } = render(<div>{renderBidiText("טקסט רגיל בלי מרכאות")}</div>);
    expect(container.querySelectorAll("bdi").length).toBe(0);
    expect(container.textContent).toBe("טקסט רגיל בלי מרכאות");
  });

  it("passes through null/undefined/empty", () => {
    expect(renderBidiText(null)).toBe(null);
    expect(renderBidiText(undefined)).toBe(undefined);
    expect(renderBidiText("")).toBe("");
  });
});

// CR-invest.md: investigation answers embed unquoted Latin/number runs constantly ("Ophir
// Optronics", "MWIR", "15-300 mm") with no quotes to anchor a bidi fix to -- renderBidiRuns
// isolates every such run, not just a quoted one.
describe("renderBidiRuns", () => {
  it("wraps a bare (unquoted) Latin run adjacent to Hebrew in a dir=ltr bdi", () => {
    // Round-2 mobile fix (UI-MOBILE-iphone.md #5): the trailing space right before the next
    // Hebrew word is rendered as plain text *outside* the isolate, not inside it -- a `<bdi>` is
    // atomic, so a space swallowed inside it collapses against the run's own edge and never
    // produces a visible gap before the following word ("Ophir OptronicsנמכרXRהיא"-style gluing).
    const { container } = render(<div>{renderBidiRuns("המוצר Ophir Optronics נמכר")}</div>);
    const bdi = container.querySelector("bdi");
    expect(bdi).not.toBeNull();
    expect(bdi!.getAttribute("dir")).toBe("ltr");
    expect(bdi!.textContent).toBe("Ophir Optronics");
    expect(container.textContent).toBe("המוצר Ophir Optronics נמכר");
  });

  it("wraps a bare digit/number run the same way", () => {
    const { container } = render(<div>{renderBidiRuns("טווח 15-300 מ\"מ")}</div>);
    const bdi = container.querySelector("bdi");
    expect(bdi).not.toBeNull();
    expect(bdi!.textContent).toBe("15-300");
  });

  it("keeps the trailing space of an English run outside the bdi isolate so it stays a real gap before the next Hebrew word (round-2 #5: \"XRהיא\" glued-together regression)", () => {
    const { container } = render(<div>{renderBidiRuns("מערכת SPECTRO XR היא מכ\"ם")}</div>);
    const bdis = container.querySelectorAll("bdi");
    for (const bdi of bdis) {
      expect(bdi.textContent).not.toMatch(/\s$/);
      expect(bdi.textContent).not.toMatch(/^\s/);
    }
    expect(container.textContent).toBe('מערכת SPECTRO XR היא מכ"ם');
  });

  it("keeps a parenthesized English term's brackets in the surrounding Hebrew run", () => {
    // Mirrors eoa.search.deep_search._split_bidi_runs' bracket-pair symmetry: "(" and ")" land in
    // the Hebrew run, and the isolated run is the bare "Targeting Pods" with no bracket attached.
    const { container } = render(<div>{renderBidiRuns("פודים (Targeting Pods) חדשים")}</div>);
    const bdis = container.querySelectorAll("bdi");
    expect(bdis.length).toBe(1);
    expect(bdis[0].textContent).toBe("Targeting Pods");
    expect(container.textContent).toBe("פודים (Targeting Pods) חדשים");
  });

  it("wraps multiple separate Latin runs independently", () => {
    const { container } = render(<div>{renderBidiRuns("Elbit זכתה מול Rafael בתחרות")}</div>);
    const bdis = container.querySelectorAll("bdi");
    expect(bdis.length).toBe(2);
    expect(bdis[0].textContent).toBe("Elbit");
    expect(bdis[1].textContent).toBe("Rafael");
  });

  it("leaves pure Hebrew text completely untouched (returns the original string)", () => {
    const result = renderBidiRuns("טקסט עברי בלבד ללא תוכן לועזי");
    expect(result).toBe("טקסט עברי בלבד ללא תוכן לועזי");
  });

  it("passes through null/undefined/empty", () => {
    expect(renderBidiRuns(null)).toBe(null);
    expect(renderBidiRuns(undefined)).toBe(undefined);
    expect(renderBidiRuns("")).toBe("");
  });
});
