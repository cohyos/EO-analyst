import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CorroborationBadge } from "./CorroborationBadge";
import type { Corroboration } from "@/types/api";

function corr(over: Partial<Corroboration> = {}): Corroboration {
  return {
    status: "unknown",
    count: 0,
    sources: [],
    checked_at: null,
    ...over,
  };
}

describe("CorroborationBadge", () => {
  it("renders the amber 'מקור יחיד' chip with the correct tooltip for single_source", () => {
    render(<CorroborationBadge corroboration={corr({ status: "single_source" })} />);
    expect(screen.getByText("מקור יחיד")).toBeInTheDocument();
    expect(screen.getByTestId("corroboration-badge-single_source")).toHaveAttribute(
      "title",
      "לא נמצאו מקורות עצמאיים נוספים המאמתים את הדיווח",
    );
  });

  it("renders the green 'מאומת ב-N מקורות' chip for corroborated, with the actual count", () => {
    render(
      <CorroborationBadge
        corroboration={corr({
          status: "corroborated",
          count: 3,
          sources: [
            { item_id: 11, source_name: "TASS", url: "https://a.test", published_at: null, kind: "duplicate" },
            { item_id: 12, source_name: "Yonhap", url: "https://b.test", published_at: null, kind: "same_event" },
            { item_id: 13, source_name: "MoD press release", url: "https://c.test", published_at: null, kind: "official" },
          ],
        })}
      />,
    );
    expect(screen.getByText("מאומת ב-3 מקורות")).toBeInTheDocument();
  });

  it("renders the blue 'מקור ראשוני רשמי' chip for official_primary", () => {
    render(<CorroborationBadge corroboration={corr({ status: "official_primary" })} />);
    expect(screen.getByText("מקור ראשוני רשמי")).toBeInTheDocument();
  });

  it("renders nothing for unknown when showUnknown is not set", () => {
    const { container } = render(<CorroborationBadge corroboration={corr({ status: "unknown" })} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a subtle grey 'לא נבדק' chip for unknown when showUnknown is set", () => {
    render(<CorroborationBadge corroboration={corr({ status: "unknown" })} showUnknown />);
    expect(screen.getByText("לא נבדק")).toBeInTheDocument();
  });

  it("treats a missing/undefined corroboration prop exactly like status 'unknown'", () => {
    const { container, rerender } = render(<CorroborationBadge corroboration={undefined} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<CorroborationBadge corroboration={null} showUnknown />);
    expect(screen.getByText("לא נבדק")).toBeInTheDocument();
  });

  it("is closed by default and opens the source list on click, showing each source's name/kind label", () => {
    render(
      <CorroborationBadge
        corroboration={corr({
          status: "corroborated",
          count: 2,
          sources: [
            { item_id: 21, source_name: "כפילות מקור", url: "https://d.test", published_at: null, kind: "duplicate" },
            { item_id: 22, source_name: "רשמי מקור", url: "https://e.test", published_at: null, kind: "official" },
          ],
        })}
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    const toggle = screen.getByTestId("corroboration-badge-corroborated");
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const dialog = screen.getByRole("dialog", { name: "רשימת מקורות מאמתים" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText("כפילות מקור")).toBeInTheDocument();
    expect(screen.getByText("רשמי מקור")).toBeInTheDocument();
    expect(screen.getByText("כפילות")).toBeInTheDocument();
    expect(screen.getByText("רשמי")).toBeInTheDocument();
  });

  it("closes the source list when Escape is pressed", () => {
    render(
      <CorroborationBadge
        corroboration={corr({
          status: "corroborated",
          count: 1,
          sources: [{ item_id: 31, source_name: "מקור", url: "https://f.test", published_at: null, kind: "same_event" }],
        })}
      />,
    );
    fireEvent.click(screen.getByTestId("corroboration-badge-corroborated"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("closes the source list on an outside click", () => {
    render(
      <div>
        <div data-testid="outside">מחוץ לתג</div>
        <CorroborationBadge
          corroboration={corr({
            status: "corroborated",
            count: 1,
            sources: [{ item_id: 41, source_name: "מקור", url: "https://g.test", published_at: null, kind: "same_event" }],
          })}
        />
      </div>,
    );
    fireEvent.click(screen.getByTestId("corroboration-badge-corroborated"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.mouseDown(screen.getByTestId("outside"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders each source link with target=_blank and rel=noopener", () => {
    render(
      <CorroborationBadge
        corroboration={corr({
          status: "corroborated",
          count: 1,
          sources: [{ item_id: 51, source_name: "מקור", url: "https://h.test", published_at: null, kind: "same_event" }],
        })}
      />,
    );
    fireEvent.click(screen.getByTestId("corroboration-badge-corroborated"));
    const link = screen.getByRole("link", { name: /פתח מקור/ });
    expect(link).toHaveAttribute("href", "https://h.test");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("does not toggle the list when a double-click bubbles from the button (guards the feed row's own double-click handler)", () => {
    const onDoubleClickOutside = vi.fn();
    render(
      <div onDoubleClick={onDoubleClickOutside}>
        <CorroborationBadge
          corroboration={corr({
            status: "corroborated",
            count: 1,
            sources: [{ item_id: 61, source_name: "מקור", url: "https://i.test", published_at: null, kind: "same_event" }],
          })}
        />
      </div>,
    );
    fireEvent.doubleClick(screen.getByTestId("corroboration-badge-corroborated"));
    expect(onDoubleClickOutside).not.toHaveBeenCalled();
  });

  it("supports a compact size variant without changing the rendered label", () => {
    render(<CorroborationBadge corroboration={corr({ status: "single_source" })} size="sm" />);
    expect(screen.getByText("מקור יחיד").closest("[data-testid]")?.className).toMatch(/text-\[10px\]/);
  });
});
