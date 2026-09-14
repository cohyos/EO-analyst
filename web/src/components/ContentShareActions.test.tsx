import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ContentShareActions } from "./ContentShareActions";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function show(text = "תוכן הניתוח") {
  return render(
    <section data-share-content>
      <h1>דוח</h1>
      <p>{text}</p>
      <ContentShareActions
        title="דוח"
        links={[{ url: "https://example.com/source", title: "מקור" }]}
      />
    </section>,
  );
}

describe("ContentShareActions", () => {
  it("copies content with source URLs and confirms success", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    show();
    fireEvent.click(screen.getByRole("button", { name: "העתק תוכן וקישורים" }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toContain("תוכן הניתוח");
    expect(writeText.mock.calls[0][0]).toContain("https://example.com/source");
    expect(await screen.findByRole("status")).toHaveTextContent("הועתקו");
  });

  it("opens a message composer without sending anything", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    show();
    fireEvent.click(screen.getByRole("button", { name: "שתף בוואטסאפ" }));
    expect(open.mock.calls[0][0]).toMatch(/^https:\/\/wa.me\/\?text=/);
    expect(decodeURIComponent(String(open.mock.calls[0][0]))).toContain(
      "https://example.com/source",
    );
  });

  it("keeps the complete long report available when automatic copying fails", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    const long = "טקסט מלא ".repeat(3000);
    show(long);
    fireEvent.click(screen.getByRole("button", { name: "שתף במייל" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("לא ניתן"));
    expect(
      (
        (screen.getByRole("textbox") as HTMLTextAreaElement).value.match(/טקסט מלא/g) ||
        []
      ).length,
    ).toBe(3000);
    expect(screen.getByRole("link", { name: "פתח מייל להדבקה" })).toHaveAttribute(
      "href",
      expect.stringContaining("mailto:"),
    );
    expect(open).not.toHaveBeenCalled();
  });
});
