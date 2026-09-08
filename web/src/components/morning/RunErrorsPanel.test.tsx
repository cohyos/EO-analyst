import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { RecentErrorLogEntry } from "@/types/api";
import { RunErrorsPanel } from "./RunErrorsPanel";

function error(overrides: Partial<RecentErrorLogEntry> = {}): RecentErrorLogEntry {
  return {
    id: 279,
    job_id: 180,
    stage: "tenders",
    message: "KeyError: 0",
    error_type: "KeyError",
    traceback_tail: ["scan.py:992:_candidate_duplicate_exists"],
    item_id: null,
    link: "/morning#pipeline-replay",
    cause_he: "שגיאת קוד בשלב 'מכרזים'",
    action_he: "דווח למפתח; ההרצה המשיכה לשלב הבא",
    impact_he: "השלב 'מכרזים' נכשל; ההרצה המשיכה לשלב הבא",
    at: "2026-09-08T01:31:51+03:00",
    ...overrides,
  };
}

function renderPanel(errors: RecentErrorLogEntry[], onClose = vi.fn()) {
  return render(
    <MemoryRouter>
      <RunErrorsPanel errors={errors} onClose={onClose} />
    </MemoryRouter>,
  );
}

describe("RunErrorsPanel", () => {
  it("is an accessible modal dialog with the required title", () => {
    renderPanel([error()]);
    const dialog = screen.getByRole("dialog", { name: "שגיאות בריצה האחרונה" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
  });

  it("renders cause, recommended action and impact for each error -- never a dead end", () => {
    renderPanel([error()]);
    expect(screen.getByText("KeyError: 0")).toBeInTheDocument();
    expect(screen.getByText("שגיאת קוד בשלב 'מכרזים'")).toBeInTheDocument();
    expect(screen.getByText("דווח למפתח; ההרצה המשיכה לשלב הבא")).toBeInTheDocument();
    expect(screen.getByText("השלב 'מכרזים' נכשל; ההרצה המשיכה לשלב הבא")).toBeInTheDocument();
  });

  it("hides the exception type and traceback behind a 'פרטים טכניים' expander", () => {
    renderPanel([error()]);
    expect(screen.getByText("פרטים טכניים")).toBeInTheDocument();
    expect(screen.getByText(/סוג שגיאה/)).toHaveTextContent("KeyError");
    expect(screen.getByText("scan.py:992:_candidate_duplicate_exists")).toBeInTheDocument();
  });

  it("shows a green empty state when there are no errors", () => {
    renderPanel([]);
    expect(screen.getByText("אין שגיאות ב-24 השעות האחרונות")).toBeInTheDocument();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    renderPanel([error()], onClose);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("closes via the close button", () => {
    const onClose = vi.fn();
    renderPanel([error()], onClose);
    fireEvent.click(screen.getByRole("button", { name: "סגור" }));
    expect(onClose).toHaveBeenCalled();
  });

  it("traps Tab focus within the dialog (Tab from the last control wraps to the first)", () => {
    renderPanel([error()]);
    const dialog = screen.getByRole("dialog");
    const focusables = dialog.querySelectorAll<HTMLElement>(
      'button, a[href], [tabindex]:not([tabindex="-1"])',
    );
    expect(focusables.length).toBeGreaterThan(1);
    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    last.focus();
    expect(document.activeElement).toBe(last);
    fireEvent.keyDown(window, { key: "Tab" });
    expect(document.activeElement).toBe(first);
  });

  it("renders a deep link to the error's item when item_id is known", () => {
    renderPanel([error({ item_id: 42, link: "/items/42" })]);
    expect(screen.getByRole("link", { name: /פתח פרטים/ })).toHaveAttribute("href", "/items/42");
  });
});
