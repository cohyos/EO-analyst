import type { ReactElement } from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { CitationText, type CitationLike } from "./CitationText";

const citations: CitationLike[] = [
  { n: 1, item_id: 101, title: "Elbit Systems חושפת דור חדש", url: "https://example.test/1" },
  { n: 2, item_id: 102, title: "Rafael מרחיבה מעקבים", url: "https://example.test/2" },
  { n: 3, item_id: null, title: "מקור חיצוני ללא פריט", url: "https://external.test/press" },
];

function renderWithRouter(ui: ReactElement) {
  return render(
    <MemoryRouter initialEntries={["/ask"]}>
      <Routes>
        <Route path="/ask" element={ui} />
        <Route path="/items/:id" element={<div data-testid="item-page">item page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("CitationText", () => {
  it("renders plain text unchanged when there are no [n] markers", () => {
    renderWithRouter(<CitationText text="אין כאן ציטוטים" citations={citations} />);
    expect(screen.getByText("אין כאן ציטוטים")).toBeInTheDocument();
  });

  it("turns [n] markers into clickable citation chips", () => {
    renderWithRouter(<CitationText text="ממצא ראשון [1] וממצא שני [2]." citations={citations} />);
    expect(screen.getByRole("button", { name: "1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "2" })).toBeInTheDocument();
  });

  it("leaves an [n] marker as plain text when no matching citation exists", () => {
    renderWithRouter(<CitationText text="ציטוט חסר [9]" citations={citations} />);
    expect(screen.queryByRole("button", { name: "9" })).not.toBeInTheDocument();
    expect(screen.getByText(/\[9\]/)).toBeInTheDocument();
  });

  it("shows a hover tooltip with the source title and URL", () => {
    renderWithRouter(<CitationText text="ראה [1]" citations={citations} />);
    const chip = screen.getByRole("button", { name: "1" });
    fireEvent.mouseEnter(chip);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Elbit Systems חושפת דור חדש");
    fireEvent.mouseLeave(chip);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("calls onOpenItem with the cited item id when a chip is clicked, if one is given", () => {
    const onOpenItem = vi.fn();
    renderWithRouter(<CitationText text="ראה [2]" citations={citations} onOpenItem={onOpenItem} />);
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    expect(onOpenItem).toHaveBeenCalledWith(102);
  });

  it("U3: navigates to /items/:id on click when no onOpenItem is given (the default click behavior)", () => {
    renderWithRouter(<CitationText text="ראה [2]" citations={citations} />);
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    expect(screen.getByTestId("item-page")).toBeInTheDocument();
  });

  describe("U3: citation with no item_id (only a source URL)", () => {
    const openSpy = vi.fn();
    beforeEach(() => {
      vi.stubGlobal("open", openSpy);
    });
    afterEach(() => {
      openSpy.mockReset();
      vi.unstubAllGlobals();
    });

    it("opens the source URL in a new tab instead of navigating", () => {
      renderWithRouter(<CitationText text="ראה [3]" citations={citations} />);
      fireEvent.click(screen.getByRole("button", { name: "3" }));
      expect(openSpy).toHaveBeenCalledWith("https://external.test/press", "_blank", "noopener,noreferrer");
      expect(screen.queryByTestId("item-page")).not.toBeInTheDocument();
    });
  });
});
