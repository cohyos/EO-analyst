import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { LevelBadge } from "./LevelBadge";

describe("LevelBadge", () => {
  it("renders the Hebrew label and an accessible name for each triage level", () => {
    render(<LevelBadge level="red" />);
    expect(screen.getByText("קריטי")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /קריטי/ })).toBeInTheDocument();
  });

  it("renders orange, yellow and archive levels with distinct labels", () => {
    const { rerender } = render(<LevelBadge level="orange" />);
    expect(screen.getByText("חשוב")).toBeInTheDocument();

    rerender(<LevelBadge level="yellow" />);
    expect(screen.getByText("רקע")).toBeInTheDocument();

    rerender(<LevelBadge level="archive" />);
    expect(screen.getByText("ארכיון")).toBeInTheDocument();
  });

  it("marks the level via data-level so tests/styling never depend on color alone", () => {
    render(<LevelBadge level="red" />);
    expect(screen.getByText("קריטי").closest("[data-level]")).toHaveAttribute(
      "data-level",
      "red",
    );
  });

  it("supports a compact size variant", () => {
    render(<LevelBadge level="orange" size="sm" />);
    expect(screen.getByText("חשוב").closest("[data-level]")?.className).toMatch(/text-xs/);
  });
});
