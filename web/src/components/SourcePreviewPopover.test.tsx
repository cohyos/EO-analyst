import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const getItem = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getItem: (...args: unknown[]) => getItem(...args),
  },
}));

import { SourcePreviewPopover } from "./SourcePreviewPopover";

/** Stubs `window.matchMedia("(hover: hover) and (pointer: fine)")` so `SourcePreviewPopover` can
 * tell "desktop" (hover-capable) apart from "touch" (the jsdom default, matches: false for every
 * query -- see `src/test/setup.ts`) the same way it does in a real browser. */
function setHoverCapable(matches: boolean) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes("hover: hover") ? matches : false,
    media: query,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

function renderPopover(children = <a href="https://example.test/x">כותרת המקור</a>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <SourcePreviewPopover itemId={7} fallback={{ title: "כותרת המקור", url: "https://example.test/x" }}>
          {children}
        </SourcePreviewPopover>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("SourcePreviewPopover", () => {
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    getItem.mockReset();
    getItem.mockResolvedValue({
      title: "כותרת המקור",
      source_name: "מקור",
      published_at: "2026-09-05T00:00:00Z",
      url: "https://example.test/x",
    });
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  describe("desktop (hover-capable)", () => {
    beforeEach(() => setHoverCapable(true));

    it("does not intercept the trigger's own click", () => {
      const onClick = vi.fn((e: React.MouseEvent) => e.preventDefault());
      renderPopover(
        <a href="https://example.test/x" onClick={onClick}>
          כותרת המקור
        </a>,
      );
      fireEvent.click(screen.getByRole("link", { name: "כותרת המקור" }));
      expect(onClick).toHaveBeenCalledTimes(1);
    });

    it("opens a role=tooltip preview on hover, after a short delay, and closes on mouse-leave", async () => {
      renderPopover();
      const trigger = screen.getByRole("link", { name: "כותרת המקור" });

      fireEvent.mouseEnter(trigger);
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument(); // delayed, not instant
      await screen.findByRole("tooltip");

      fireEvent.mouseLeave(trigger);
      await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
    });

    it("opens the same preview on keyboard focus (a11y) and closes on blur", async () => {
      renderPopover();
      const trigger = screen.getByRole("link", { name: "כותרת המקור" });

      fireEvent.focus(trigger);
      await screen.findByRole("tooltip");

      fireEvent.blur(trigger);
      await waitFor(() => expect(screen.queryByRole("tooltip")).not.toBeInTheDocument());
    });

    it("closes an open tooltip on Escape", async () => {
      renderPopover();
      fireEvent.mouseEnter(screen.getByRole("link", { name: "כותרת המקור" }));
      await screen.findByRole("tooltip");

      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    });
  });

  describe("mobile / touch", () => {
    beforeEach(() => setHoverCapable(false));

    it("intercepts the first tap on the trigger and opens a bottom sheet instead of navigating", () => {
      const onClick = vi.fn();
      renderPopover(
        <a href="https://example.test/x" onClick={onClick}>
          כותרת המקור
        </a>,
      );
      fireEvent.click(screen.getByRole("link", { name: "כותרת המקור" }));

      expect(onClick).not.toHaveBeenCalled();
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("moves focus into the sheet, and Escape closes it and restores focus to the trigger", async () => {
      renderPopover();
      const trigger = screen.getByRole("link", { name: "כותרת המקור" });
      trigger.focus();
      fireEvent.click(trigger);

      const closeButton = await screen.findByRole("button", { name: "סגור" });
      expect(closeButton).toHaveFocus();

      fireEvent.keyDown(document, { key: "Escape" });
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(trigger).toHaveFocus();
    });

    it("closes on backdrop click", () => {
      renderPopover();
      fireEvent.click(screen.getByRole("link", { name: "כותרת המקור" }));
      expect(screen.getByRole("dialog")).toBeInTheDocument();

      const backdrop = document.querySelector('[aria-hidden="true"].absolute.inset-0');
      expect(backdrop).not.toBeNull();
      fireEvent.click(backdrop as Element);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("does not shift surrounding layout -- the wrapper renders with display: contents", () => {
      const { container } = renderPopover();
      const wrapper = container.querySelector("span.contents");
      expect(wrapper).not.toBeNull();
    });
  });
});
