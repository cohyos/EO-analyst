import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { ErrorBoundary } from "./ErrorBoundary";

function Bomb(): never {
  // Simulates the real crash shape: unguarded access on a null API field,
  // e.g. `night_summary.items_ingested` when night_summary is null.
  const data: { items_ingested: number } | null = null;
  return data!.items_ingested as never;
}

describe("ErrorBoundary", () => {
  const consoleErrorSpy = vi.spyOn(console, "error").mockImplementation(() => {});

  afterEach(() => {
    consoleErrorSpy.mockClear();
  });

  it("renders children normally when nothing throws", () => {
    render(
      <ErrorBoundary>
        <p>תוכן תקין</p>
      </ErrorBoundary>,
    );
    expect(screen.getByText("תוכן תקין")).toBeInTheDocument();
  });

  it("catches a render error and shows the Hebrew fallback with a reload button instead of blanking the app", () => {
    render(
      <ErrorBoundary>
        <Bomb />
      </ErrorBoundary>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("משהו השתבש בטעינת המסך הזה");
    expect(screen.getByRole("button", { name: "טען מחדש" })).toBeInTheDocument();
  });
});
