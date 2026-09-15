import { describe, expect, it } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MobileNav } from "./MobileNav";

function renderMobileNav(path = "/feed") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <MobileNav />
    </MemoryRouter>,
  );
}

describe("MobileNav (phone bottom tab bar, defect #4)", () => {
  it("renders the 4 primary routes plus a 5th 'more' button in the fixed bottom bar", () => {
    renderMobileNav("/feed");
    const nav = screen.getByRole("navigation", { name: "ניווט ראשי" });
    const links = within(nav).getAllByRole("link");
    expect(links).toHaveLength(4);
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/", "/feed", "/reports", "/dossiers"]);
    expect(within(nav).getByTestId("mobile-nav-more")).toBeInTheDocument();
  });

  it("marks the active primary route with aria-current", () => {
    renderMobileNav("/feed");
    const nav = screen.getByRole("navigation", { name: "ניווט ראשי" });
    const active = within(nav).getByRole("link", { current: "page" });
    expect(active).toHaveAttribute("href", "/feed");
  });

  it("opens a 'more' sheet listing every one of the 16 nav routes, current route highlighted", () => {
    renderMobileNav("/settings");
    fireEvent.click(screen.getByTestId("mobile-nav-more"));

    const sheet = screen.getByTestId("mobile-nav-more-sheet");
    expect(sheet).toHaveAttribute("role", "dialog");
    const links = within(sheet).getAllByRole("link");
    expect(links).toHaveLength(16);

    const current = within(sheet).getByRole("link", { current: "page" });
    expect(current).toHaveAttribute("href", "/settings");
  });

  it("closes the sheet when a route is selected", () => {
    renderMobileNav("/feed");
    fireEvent.click(screen.getByTestId("mobile-nav-more"));
    const sheet = screen.getByTestId("mobile-nav-more-sheet");
    fireEvent.click(within(sheet).getByRole("link", { name: "הגדרות" }));
    expect(screen.queryByTestId("mobile-nav-more-sheet")).not.toBeInTheDocument();
  });

  it("closes the sheet on Escape", () => {
    renderMobileNav("/feed");
    fireEvent.click(screen.getByTestId("mobile-nav-more"));
    expect(screen.getByTestId("mobile-nav-more-sheet")).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("mobile-nav-more-sheet")).not.toBeInTheDocument();
  });

  it("closes the sheet on backdrop tap", () => {
    renderMobileNav("/feed");
    fireEvent.click(screen.getByTestId("mobile-nav-more"));
    fireEvent.click(screen.getByTestId("mobile-nav-more-backdrop"));
    expect(screen.queryByTestId("mobile-nav-more-sheet")).not.toBeInTheDocument();
  });
});
