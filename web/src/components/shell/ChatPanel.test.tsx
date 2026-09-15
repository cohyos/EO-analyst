import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useUiStore } from "@/store/uiStore";

vi.mock("@/api", () => ({
  api: {
    postAsk: vi.fn(),
    getLlmProviders: vi.fn().mockResolvedValue([]),
  },
}));

import { ChatPanel } from "./ChatPanel";

function resetUiStore() {
  useUiStore.setState({ chatOpen: false, chatContext: [] } as never);
}

function renderChatPanel() {
  const queryClient = new QueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <main>
        <ChatPanel />
      </main>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  resetUiStore();
});

// Round-3 mobile fix (UI-MOBILE-iphone-r3.md #1): below `md:` the closed-state trigger is a
// compact icon-only round button instead of the labelled pill, and hides itself while `<main>`
// scrolls down, reappearing on scroll-up.
describe("ChatPanel compact (phone) FAB", () => {
  it("renders an icon-only button whose aria-label is just the chat label, and opens the panel on click", () => {
    resetUiStore();
    renderChatPanel();

    const fab = screen.getByTestId("chat-panel-fab-compact");
    expect(fab).toHaveAttribute("aria-label", "שאל את האנליסט");
    expect(fab.querySelector("svg")).toBeInTheDocument();
    // Icon-only: no visible label text node inside the compact button.
    expect(fab.textContent).toBe("");

    fireEvent.click(fab);
    expect(screen.getByTestId("chat-panel-dropzone")).toBeInTheDocument();
  });

  it("also renders the original labelled pill for md:+ (still in the DOM, hidden via CSS below md:)", () => {
    resetUiStore();
    renderChatPanel();
    const pill = screen.getByTestId("chat-panel-fab-dropzone");
    expect(pill).toHaveAttribute("aria-label", "פתח את פאנל שאל את האנליסט — גרור לכאן פריט או ישות כדי להוסיף להקשר");
    expect(pill.className).toContain("md:flex");
    expect(pill.className).toContain("hidden");
  });

  it("hides the compact FAB (translates it off-screen) while scrolling <main> down, and shows it again on scroll-up", () => {
    resetUiStore();
    const { container } = renderChatPanel();
    const mainEl = container.querySelector("main")!;
    const fab = screen.getByTestId("chat-panel-fab-compact");

    expect(fab.className).toContain("translate-y-0");

    Object.defineProperty(mainEl, "scrollTop", { value: 0, writable: true, configurable: true });
    mainEl.scrollTop = 80;
    fireEvent.scroll(mainEl);
    expect(fab.className).toContain(
      "translate-y-[calc(100%+var(--eoa-statusbar-clear-h)+env(safe-area-inset-bottom)+var(--eoa-tabbar-h)+1rem)]",
    );

    mainEl.scrollTop = 10;
    fireEvent.scroll(mainEl);
    expect(fab.className).toContain("translate-y-0");
  });
});
