import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { AskCitation } from "@/types/api";
import { AskAnswer } from "./AskAnswer";

const citations: AskCitation[] = [
  { n: 1, item_id: 101, title: "Elbit Systems חושפת דור חדש", url: "https://example.test/1" },
];

function renderWithRouter(text: string, cites: AskCitation[] = citations) {
  return render(
    <MemoryRouter initialEntries={["/ask"]}>
      <Routes>
        <Route path="/ask" element={<AskAnswer text={text} citations={cites} />} />
        <Route path="/items/:id" element={<div data-testid="item-page">item page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("AskAnswer", () => {
  it("renders markdown headings/bold/lists as real elements, never literal ### or ** markers", () => {
    const { container } = renderWithRouter(
      "### עובדות מרכזיות\n\n**חשוב**: יש התפתחות [1].\n\n- נקודה ראשונה\n",
    );
    expect(container.querySelector("h3")).not.toBeNull();
    expect(container.querySelector("strong")).not.toBeNull();
    expect(container.querySelector("li")).not.toBeNull();
    expect(container.textContent).not.toMatch(/###/);
    expect(container.textContent).not.toMatch(/\*\*/);
  });

  it("navigates to /items/:id when a resolvable citation chip is clicked", () => {
    const { container } = renderWithRouter("ממצא [1].");
    const chip = container.querySelector(".eo-citation") as HTMLElement;
    expect(chip).not.toBeNull();
    fireEvent.click(chip);
    expect(screen.getByTestId("item-page")).toBeInTheDocument();
  });

  it("never renders a raw ### / ** marker for a full per-source-dump-shaped answer", () => {
    const { container } = renderWithRouter(
      "### תשובה\n\nתשובה ישירה כאן.\n\n### עובדות מרכזיות\n\n- עובדה [1]\n\n### הערכת האנליסט\n\nהערכה כללית.\n\n### פערים / מה לא ידוע\n\n- לא ידוע דבר\n",
    );
    expect(container.textContent).not.toContain("###");
    expect(container.querySelectorAll("h3").length).toBeGreaterThanOrEqual(4);
  });
});
