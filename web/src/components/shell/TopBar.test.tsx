import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/api", () => ({
  api: {
    getRunsCurrent: async () => ({ current: null, other_running: [] }),
    postRun: async () => ({}),
  },
  ApiError: class ApiError extends Error {},
  USE_MOCKS: false,
}));

import { TopBar } from "./TopBar";

function renderTopBar(path = "/feed") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <TopBar nightWindow={false} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("TopBar", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("renders the h1 with the full page title text (defect #2 — used to be squeezed to ~1 char)", () => {
    renderTopBar("/feed");
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("פיד Triage");
    // The fix: the title is the flexible element (flex-1), not a fixed shrink-0
    // sliver squeezed by the other controls, and allows up to 2 lines on phones.
    expect(heading.className).toContain("flex-1");
    expect(heading.className).toContain("line-clamp-2");
  });

  it("keeps the day/night badge, language toggle and theme toggle out of the DOM's default (mobile) flow, moving them into the overflow menu", () => {
    renderTopBar("/");
    // The always-visible desktop controls carry `hidden` (only shown from `md:` up).
    expect(screen.getByTestId("language-toggle").className).toContain("hidden");
    expect(screen.getByTestId("theme-toggle").className).toContain("hidden");
  });

  it("opens a keyboard-accessible overflow menu exposing the night badge, language toggle and theme toggle", () => {
    renderTopBar("/");
    const trigger = screen.getByTestId("topbar-overflow-toggle");
    expect(trigger).toHaveAttribute("aria-haspopup", "menu");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(trigger);
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const menu = screen.getByRole("menu");
    expect(menu).toBeInTheDocument();
    expect(screen.getByTestId("language-toggle-mobile")).toBeInTheDocument();
    expect(screen.getByTestId("theme-toggle-mobile")).toBeInTheDocument();
  });

  it("closes the overflow menu on Escape", () => {
    renderTopBar("/");
    fireEvent.click(screen.getByTestId("topbar-overflow-toggle"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("closes the overflow menu after choosing an item", () => {
    renderTopBar("/");
    fireEvent.click(screen.getByTestId("topbar-overflow-toggle"));
    fireEvent.click(screen.getByTestId("theme-toggle-mobile"));
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
