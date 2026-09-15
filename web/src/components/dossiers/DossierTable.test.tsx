import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { DossierTable, type DossierTableColumn } from "./DossierTable";

/** Round-2 mobile fix (UI-MOBILE-iphone.md #3): stubs `window.matchMedia` so
 * `useIsNarrowViewport(768)` reports "narrow" -- `DossierTable` picks the `<md:` card list over
 * the `<table>` for a wide (>3 column) table in that state. Every pre-existing test in this file
 * doesn't call this, so `matches` stays `false` and they keep exercising the desktop table path. */
function setNarrowViewport() {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: query.includes("max-width"),
    media: query,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

// `setNarrowViewport` replaces `window.matchMedia` for the rest of the file (jsdom's `window`
// persists across tests within one file) unless undone -- restore the original stub (installed by
// src/test/setup.ts, always "not narrow") after every test so a later test that doesn't call
// `setNarrowViewport` itself isn't silently left in whatever state the previous test set.
const originalMatchMedia = window.matchMedia;
afterEach(() => {
  window.matchMedia = originalMatchMedia;
});

interface Row {
  key: string;
  value: string;
}

function col(key: string, label: string): DossierTableColumn<Row> {
  return { key, label, render: (r) => r.value };
}
function paramCol(): DossierTableColumn<Row> {
  return { key: "param", label: "פרמטר", render: (r) => r.key };
}
function valueCol(): DossierTableColumn<Row> {
  return { key: "value", label: "ערך", render: (r) => r.value };
}

const rows: Row[] = [{ key: "שדה ראייה", value: "Wide field of view with Dual FOV optics" }];

describe("DossierTable mobile wrap behaviour (UI-MOBILE-iphone.md #4)", () => {
  it("a 2-column table wraps instead of forcing a min-width (no horizontal scroll needed)", () => {
    render(
      <DossierTable
        columns={[paramCol(), valueCol()]}
        rows={rows}
        rowKey={(r) => r.key}
        emptyLabel="אין נתונים"
        caption="מפרט"
      />,
    );
    const table = screen.getByRole("table");
    expect(table.className).not.toContain("min-w-max");
    const cell = screen.getByText("Wide field of view with Dual FOV optics");
    expect(cell.className).toContain("whitespace-normal");
    expect(cell.className).toContain("break-words");
    expect(cell.className).not.toContain("sticky");
  });

  it("a table with more than 3 columns keeps min-width + no-wrap and pins the first column sticky", () => {
    render(
      <DossierTable
        columns={[col("a", "א"), col("b", "ב"), col("c", "ג"), col("d", "ד")]}
        rows={[{ key: "row1", value: "x" }]}
        rowKey={(r) => r.key}
        emptyLabel="אין נתונים"
        caption="טבלה רחבה"
      />,
    );
    const table = screen.getByRole("table");
    expect(table.className).toContain("min-w-max");
    const firstHeader = screen.getByText("א");
    expect(firstHeader.className).toContain("sticky");
    expect(firstHeader.className).toContain("start-0");
  });

  it("the scroll container is explicitly RTL so the default scroll position shows the first (rightmost) columns", () => {
    const { container } = render(
      <DossierTable
        columns={[col("param", "פרמטר"), col("value", "ערך")]}
        rows={rows}
        rowKey={(r) => r.key}
        emptyLabel="אין נתונים"
        caption="מפרט"
      />,
    );
    const scrollContainer = container.firstElementChild as HTMLElement;
    expect(scrollContainer).toHaveAttribute("dir", "rtl");
  });
});

describe("DossierTable phone card mode (round-2 mobile fix #3)", () => {
  const wideColumns = [col("a", "תאריך"), col("b", "לקוח"), col("c", "סוג"), col("d", "סכום")];
  const wideRows: Row[] = [{ key: "2026-01-01", value: "x" }];

  it("renders a wide (>3 column) table as stacked cards on a narrow viewport instead of a <table>", () => {
    setNarrowViewport();
    render(
      <DossierTable
        columns={wideColumns}
        rows={wideRows}
        rowKey={(r) => r.key}
        emptyLabel="אין נתונים"
        caption="עסקאות"
      />,
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    // caption is still exposed to assistive tech even without a <table>/<caption>.
    expect(screen.getByText("עסקאות")).toBeInTheDocument();
  });

  it("puts the first column as the card title and the remaining columns (after the second) as label: value chips", () => {
    setNarrowViewport();
    render(
      <DossierTable
        columns={wideColumns}
        rows={wideRows}
        rowKey={(r) => r.key}
        emptyLabel="אין נתונים"
        caption="עסקאות"
      />,
    );
    // titleCol (a) and mainCol (b) both render the row's own value ("x") via `col()`'s render --
    // the remaining columns (c, d) render as "label: value" chips.
    expect(screen.getByText("סוג:")).toBeInTheDocument();
    expect(screen.getByText("סכום:")).toBeInTheDocument();
  });

  it("keeps the desktop <table> for a wide table when the viewport is not narrow", () => {
    render(
      <DossierTable
        columns={wideColumns}
        rows={wideRows}
        rowKey={(r) => r.key}
        emptyLabel="אין נתונים"
        caption="עסקאות"
      />,
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
  });

  it("keeps the desktop <table> for a narrow (<=3 column) table even on a narrow viewport", () => {
    setNarrowViewport();
    render(
      <DossierTable
        columns={[paramCol(), valueCol()]}
        rows={rows}
        rowKey={(r) => r.key}
        emptyLabel="אין נתונים"
        caption="מפרט"
      />,
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});
