import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ErrorState } from "./states";

// W13 (docs/REVIEW_2026-09-06_evening.md round 4): ErrorState shows a dedicated Hebrew message +
// retry button for the API client's timeout error, instead of the generic "אירעה שגיאה" copy.
describe("ErrorState timeout handling (W13)", () => {
  it("shows the generic message by default", () => {
    render(<ErrorState />);
    expect(screen.getByRole("alert")).toHaveTextContent("אירעה שגיאה בטעינת הנתונים");
  });

  it("shows the timeout-specific message when the given error has code 'timeout'", () => {
    render(<ErrorState error={{ code: "timeout" }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("השרת לא הגיב, נסה שוב");
  });

  it("an explicit message prop always wins over the error-derived one", () => {
    render(<ErrorState error={{ code: "timeout" }} message="הודעה מפורשת" />);
    expect(screen.getByRole("alert")).toHaveTextContent("הודעה מפורשת");
    expect(screen.queryByText("השרת לא הגיב, נסה שוב")).not.toBeInTheDocument();
  });

  it("renders a working retry button", () => {
    const onRetry = vi.fn();
    render(<ErrorState error={{ code: "timeout" }} onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "נסה שוב" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("a non-timeout error still falls back to the generic message", () => {
    render(<ErrorState error={{ code: "not_found" }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("אירעה שגיאה בטעינת הנתונים");
  });
});
