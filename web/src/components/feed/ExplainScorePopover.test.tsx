import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ExplainScorePopover } from "./ExplainScorePopover";
import type { ItemCard } from "@/types/api";

function makeItem(over: Partial<ItemCard> = {}): ItemCard {
  return {
    id: 1,
    title: "כותרת בדיקה",
    url: "https://example.test/1",
    source_name: "Test Source",
    published_at: "2026-09-04T10:00:00+03:00",
    lang: "he",
    domain: "airborne_pods",
    subdomain: null,
    report_kind: "verified_report",
    trl: null,
    geography: null,
    score: 7,
    level: "orange",
    triage_reason: "נימוק בדיקה מפורט",
    summary_he: null,
    so_what_he: null,
    entities_mentioned: [],
    tags: [],
    security_status: "clean",
    dedup_of: null,
    key_facts: [],
    uncertainty_he: null,
    ...over,
  };
}

describe("ExplainScorePopover", () => {
  it("is closed by default and opens on click, showing the reason and thresholds", () => {
    render(<ExplainScorePopover item={makeItem()} onRate={() => {}} />);
    expect(screen.queryByRole("dialog", { name: "הסבר ציון" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("למה הציון?"));
    expect(screen.getByRole("dialog", { name: "הסבר ציון" })).toBeInTheDocument();
    expect(screen.getByText("נימוק בדיקה מפורט")).toBeInTheDocument();
    expect(screen.getByText(/קריטי: ציון ≥ 8/)).toBeInTheDocument();
    expect(screen.getByText(/חשוב: ציון ≥ 6/)).toBeInTheDocument();
  });

  it("falls back to a placeholder when there is no triage reason", () => {
    render(<ExplainScorePopover item={makeItem({ triage_reason: null })} onRate={() => {}} />);
    fireEvent.click(screen.getByLabelText("למה הציון?"));
    expect(screen.getByText("אין נימוק זמין.")).toBeInTheDocument();
  });

  it("calls onRate with the chosen level from the quick re-rate buttons", () => {
    const onRate = vi.fn();
    render(<ExplainScorePopover item={makeItem()} onRate={onRate} />);
    fireEvent.click(screen.getByLabelText("למה הציון?"));
    fireEvent.click(screen.getByText("1 קריטי"));
    expect(onRate).toHaveBeenCalledWith("red");
  });

  it("closes when Escape is pressed", () => {
    render(<ExplainScorePopover item={makeItem()} onRate={() => {}} />);
    fireEvent.click(screen.getByLabelText("למה הציון?"));
    expect(screen.getByRole("dialog", { name: "הסבר ציון" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "הסבר ציון" })).not.toBeInTheDocument();
  });

  it("closes on an outside click", () => {
    render(
      <div>
        <div data-testid="outside">מחוץ לפופאובר</div>
        <ExplainScorePopover item={makeItem()} onRate={() => {}} />
      </div>,
    );
    fireEvent.click(screen.getByLabelText("למה הציון?"));
    expect(screen.getByRole("dialog", { name: "הסבר ציון" })).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(screen.queryByRole("dialog", { name: "הסבר ציון" })).not.toBeInTheDocument();
  });
});
