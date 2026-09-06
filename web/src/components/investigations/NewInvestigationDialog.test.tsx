import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { NewInvestigationDialog } from "./NewInvestigationDialog";

/**
 * Q5-6 (docs/qa/findings_Q5_r1.md): an empty or too-short question used to submit to nothing with
 * no feedback at all. The dialog now validates a minimum length (12 chars) inline and disables
 * submit until the question is long enough.
 */
describe("NewInvestigationDialog validation (Q5-6)", () => {
  function setup() {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(<NewInvestigationDialog onClose={onClose} onSubmit={onSubmit} submitting={false} />);
    return { onSubmit, onClose };
  }

  it("disables submit and shows no error before the analyst has typed anything", () => {
    setup();
    expect(screen.getByRole("button", { name: "התחל חקירה" })).toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("shows an inline validation message and keeps submit disabled for a too-short question", () => {
    const { onSubmit } = setup();
    const textarea = screen.getByLabelText(/הקלד שאלת מחקר/);
    fireEvent.change(textarea, { target: { value: "מה קורה" } }); // 7 chars, below the 12-char minimum
    fireEvent.blur(textarea);

    expect(screen.getByRole("button", { name: "התחל חקירה" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("קצרה מדי");

    fireEvent.submit(textarea.closest("form")!);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("shows a distinct 'empty' validation message when the field is blank but touched", () => {
    const { onSubmit } = setup();
    const textarea = screen.getByLabelText(/הקלד שאלת מחקר/);
    fireEvent.focus(textarea);
    fireEvent.blur(textarea);
    expect(screen.getByRole("alert")).toHaveTextContent("יש להקליד שאלה");
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("enables submit once the question reaches the minimum length, and submits the trimmed text", () => {
    const { onSubmit } = setup();
    const textarea = screen.getByLabelText(/הקלד שאלת מחקר/);
    fireEvent.change(textarea, {
      target: { value: "  מה היקף החוזה של אלביט בצרפת?  " },
    });

    const submitButton = screen.getByRole("button", { name: "התחל חקירה" });
    expect(submitButton).not.toBeDisabled();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();

    fireEvent.click(submitButton);
    expect(onSubmit).toHaveBeenCalledWith("מה היקף החוזה של אלביט בצרפת?");
  });
});
