import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { TenderCard } from "@/types/api";
import type { TenderFiltersState } from "./TenderFilters";
import { TendersShareMenu } from "./TendersShareMenu";

function makeTender(over: Partial<TenderCard> = {}): TenderCard {
  return {
    id: 1,
    source: "sam_gov_api",
    external_ref: "REF-1",
    title: "Targeting Pod Sustainment IDIQ",
    agency: "US Air Force",
    country: "US",
    published_at: "2026-08-20T14:00:00+00:00",
    deadline: "2026-09-10",
    url: "https://sam.gov/opp/example",
    cpv_naics: ["336413"],
    summary_he: null,
    relevance: 4,
    relevance_score: 0.8,
    intake: "accepted",
    matched_terms: ["targeting pod"],
    entities: [],
    status: "open",
    item_id: 42,
    created_at: "2026-08-20T14:05:00+00:00",
    updated_at: "2026-09-01T06:00:00+00:00",
    ...over,
  };
}

const filters: TenderFiltersState = { status: "", country: "", q: "", productLines: [] };

function renderMenu(over: Partial<Parameters<typeof TendersShareMenu>[0]> = {}) {
  return render(
    <TendersShareMenu
      tenders={[makeTender()]}
      filters={filters}
      showClosedArchived={false}
      includeForecasts={false}
      {...over}
    />,
  );
}

const createObjectURL = vi.fn(() => "blob:mock-url");
const revokeObjectURL = vi.fn();

beforeEach(() => {
  URL.createObjectURL = createObjectURL as unknown as typeof URL.createObjectURL;
  URL.revokeObjectURL = revokeObjectURL as unknown as typeof URL.revokeObjectURL;
  createObjectURL.mockClear();
  revokeObjectURL.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("TendersShareMenu", () => {
  it("is disabled when there are no visible tenders", () => {
    renderMenu({ tenders: [] });
    expect(screen.getByRole("button", { name: "שתף HTML" })).toBeDisabled();
  });

  it("opens the menu on trigger click and closes it on Escape", async () => {
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "שתף HTML" }));
    expect(await screen.findByRole("menu")).toBeInTheDocument();

    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("closes the menu on an outside click", async () => {
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "שתף HTML" }));
    expect(await screen.findByRole("menu")).toBeInTheDocument();

    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByRole("menu")).not.toBeInTheDocument());
  });

  it("labels the trigger differently when forecasts are bundled in", () => {
    renderMenu({ includeForecasts: true });
    expect(screen.getByRole("button", { name: "שתף HTML (כולל תחזיות)" })).toBeInTheDocument();
  });

  it("hides the Web Share file item when the platform does not support sharing files", async () => {
    vi.stubGlobal("navigator", { clipboard: undefined });
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "שתף HTML" }));
    expect(await screen.findByRole("menu")).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "שתף קובץ HTML" })).not.toBeInTheDocument();
  });

  it("shows the Web Share file item when navigator.canShare reports file support", async () => {
    vi.stubGlobal("navigator", { canShare: () => true, share: vi.fn().mockResolvedValue(undefined) });
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "שתף HTML" }));
    expect(await screen.findByRole("menuitem", { name: "שתף קובץ HTML" })).toBeInTheDocument();
  });

  it("downloads a temporary anchor whose filename ends in .html", () => {
    vi.stubGlobal("navigator", { clipboard: undefined });
    const clicks: Array<{ download: string }> = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicks.push({ download: this.download });
    });
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "שתף HTML" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "הורד קובץ HTML" }));

    expect(createObjectURL).toHaveBeenCalled();
    expect(clicks).toHaveLength(1);
    expect(clicks[0].download).toMatch(/^tenders-\d{4}-\d{2}-\d{2}\.html$/);
  });

  it("opens a new tab with an object URL", () => {
    vi.stubGlobal("navigator", { clipboard: undefined });
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "שתף HTML" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "פתח בלשונית חדשה" }));

    expect(createObjectURL).toHaveBeenCalled();
    expect(open).toHaveBeenCalledWith("blob:mock-url", "_blank", "noopener,noreferrer");
  });

  it("copies both text/html and text/plain via the rich Clipboard API", async () => {
    class FakeClipboardItem {
      constructor(public items: Record<string, Blob>) {}
    }
    const write = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("ClipboardItem", FakeClipboardItem);
    vi.stubGlobal("navigator", { clipboard: { write } });
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "שתף HTML" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "העתק HTML" }));

    await waitFor(() => expect(write).toHaveBeenCalled());
    const [item] = write.mock.calls[0][0] as FakeClipboardItem[];
    expect(Object.keys(item.items)).toEqual(["text/html", "text/plain"]);
    expect(await screen.findByRole("status")).toHaveTextContent("ה-HTML הועתק ללוח");
  });

  it("copies the plain-text link list via writeText", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "שתף HTML" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "העתק קישורים" }));

    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toContain("https://sam.gov/opp/example");
  });

  it("opens a mailto composer with the tender list in the body", () => {
    vi.stubGlobal("navigator", { clipboard: undefined });
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    renderMenu();
    fireEvent.click(screen.getByRole("button", { name: "שתף HTML" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "שלח במייל" }));

    expect(open.mock.calls[0][0]).toMatch(/^mailto:\?subject=/);
    expect(decodeURIComponent(String(open.mock.calls[0][0]))).toContain(
      "https://sam.gov/opp/example",
    );
  });
});
