import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CitationText, type CitationLike } from "./CitationText";

const citations: CitationLike[] = [
  { n: 1, item_id: 101, title: "Elbit Systems חושפת דור חדש", url: "https://example.test/1" },
  { n: 2, item_id: 102, title: "Rafael מרחיבה מעקבים", url: "https://example.test/2" },
];

describe("CitationText", () => {
  it("renders plain text unchanged when there are no [n] markers", () => {
    render(<CitationText text="אין כאן ציטוטים" citations={citations} />);
    expect(screen.getByText("אין כאן ציטוטים")).toBeInTheDocument();
  });

  it("turns [n] markers into clickable citation chips", () => {
    render(<CitationText text="ממצא ראשון [1] וממצא שני [2]." citations={citations} />);
    expect(screen.getByRole("button", { name: "1" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "2" })).toBeInTheDocument();
  });

  it("leaves an [n] marker as plain text when no matching citation exists", () => {
    render(<CitationText text="ציטוט חסר [9]" citations={citations} />);
    expect(screen.queryByRole("button", { name: "9" })).not.toBeInTheDocument();
    expect(screen.getByText(/\[9\]/)).toBeInTheDocument();
  });

  it("shows a hover tooltip with the source title and URL", () => {
    render(<CitationText text="ראה [1]" citations={citations} />);
    const chip = screen.getByRole("button", { name: "1" });
    fireEvent.mouseEnter(chip);
    expect(screen.getByRole("tooltip")).toHaveTextContent("Elbit Systems חושפת דור חדש");
    fireEvent.mouseLeave(chip);
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("calls onOpenItem with the cited item id when a chip is clicked", () => {
    const onOpenItem = vi.fn();
    render(<CitationText text="ראה [2]" citations={citations} onOpenItem={onOpenItem} />);
    fireEvent.click(screen.getByRole("button", { name: "2" }));
    expect(onOpenItem).toHaveBeenCalledWith(102);
  });
});
