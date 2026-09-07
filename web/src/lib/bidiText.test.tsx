import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { renderBidiText } from "./bidiText";

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
