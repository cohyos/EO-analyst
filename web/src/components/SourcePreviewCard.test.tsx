import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ItemDetail } from "@/types/api";

const getItem = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getItem: (...args: unknown[]) => getItem(...args),
  },
}));

import { SourcePreviewCard } from "./SourcePreviewCard";

function renderCard(props: Parameters<typeof SourcePreviewCard>[0]) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <SourcePreviewCard {...props} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

const LONG_SUMMARY =
  "זהו תקציר ארוך מאוד שמיועד לבדוק את מנגנון הקיצוץ לארבע שורות ואת כפתור ההרחבה 'עוד' " +
  "שאמור להופיע כאשר הטקסט חורג מהסף שנקבע לכך בקומפוננטה עצמה, ולכן הוא ממשיך ומתארך עוד ועוד.";

const baseItem: Partial<ItemDetail> = {
  id: 42,
  title: "כותרת הפריט",
  source_name: "מקור בדיקה",
  published_at: "2026-09-05T00:00:00Z",
  url: "https://example.test/article",
  summary_he: LONG_SUMMARY,
  key_facts: ["עובדה ראשונה", "עובדה שנייה", "עובדה שלישית", "עובדה רביעית"],
  product_lines: ["targeting_pods"],
  corroboration: { status: "corroborated", count: 2, sources: [], checked_at: null },
};

describe("SourcePreviewCard", () => {
  beforeEach(() => {
    getItem.mockReset();
  });

  it("shows a loading skeleton while the item fetch is pending", async () => {
    let resolve!: (v: Partial<ItemDetail>) => void;
    getItem.mockReturnValue(new Promise((r) => (resolve = r)));
    renderCard({ itemId: 42 });

    expect(screen.getByText("טוען…")).toBeInTheDocument();
    resolve(baseItem);
    await waitFor(() => expect(screen.queryByText("טוען…")).not.toBeInTheDocument());
  });

  it("renders title, source name, date, summary, key facts (capped at 3) and product-line chips once loaded", async () => {
    getItem.mockResolvedValue(baseItem);
    renderCard({ itemId: 42 });

    await screen.findByText("כותרת הפריט");
    expect(screen.getByText("מקור בדיקה")).toBeInTheDocument();
    expect(screen.getByText(LONG_SUMMARY)).toBeInTheDocument();
    expect(screen.getByText("עובדה ראשונה")).toBeInTheDocument();
    expect(screen.getByText("עובדה שנייה")).toBeInTheDocument();
    expect(screen.getByText("עובדה שלישית")).toBeInTheDocument();
    expect(screen.queryByText("עובדה רביעית")).not.toBeInTheDocument(); // capped at 3
    expect(screen.getByText("פודי ציון מטרות / תקיפה")).toBeInTheDocument();
  });

  it("shows the corroboration badge once the item carries one", async () => {
    getItem.mockResolvedValue(baseItem);
    renderCard({ itemId: 42 });
    expect(await screen.findByText("מאומת ב-2 מקורות")).toBeInTheDocument();
  });

  it("renders no corroboration badge when the field is absent from the item", async () => {
    getItem.mockResolvedValue({ ...baseItem, corroboration: undefined });
    renderCard({ itemId: 42 });
    await screen.findByText("כותרת הפריט");
    expect(screen.queryByTestId(/corroboration-badge-/)).not.toBeInTheDocument();
  });

  it("clamps a long summary and expands it on 'עוד', collapsing again on 'פחות'", async () => {
    getItem.mockResolvedValue(baseItem);
    renderCard({ itemId: 42 });

    const summary = await screen.findByText(LONG_SUMMARY);
    expect(summary.className).toContain("line-clamp-4");

    fireEvent.click(screen.getByRole("button", { name: "עוד" }));
    expect(summary.className).not.toContain("line-clamp-4");

    fireEvent.click(screen.getByRole("button", { name: "פחות" }));
    expect(summary.className).toContain("line-clamp-4");
  });

  it("offers both 'פתח פריט' (internal) and 'פתח מקור' (external, new tab) actions once loaded", async () => {
    getItem.mockResolvedValue(baseItem);
    renderCard({ itemId: 42 });
    await screen.findByText("כותרת הפריט");

    const openItem = screen.getByRole("link", { name: /פתח פריט/ });
    expect(openItem).toHaveAttribute("href", "/items/42");

    const openSource = screen.getByRole("link", { name: /פתח מקור/ });
    expect(openSource).toHaveAttribute("href", "https://example.test/article");
    expect(openSource).toHaveAttribute("target", "_blank");
    expect(openSource).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("renders the 'פתח מקור' action immediately from fallback data even while the item is still loading", () => {
    getItem.mockReturnValue(new Promise(() => {})); // never resolves
    renderCard({ itemId: 42, fallback: { title: "כותרת זמנית", url: "https://example.test/x" } });

    const openSource = screen.getByRole("link", { name: /פתח מקור/ });
    expect(openSource).toHaveAttribute("href", "https://example.test/x");
  });

  it("with no itemId, renders fallback title/source/url and only the external action (no internal item link)", () => {
    renderCard({
      fallback: { title: "כותרת מקור חיצוני", sourceName: "מקור חיצוני", url: "https://example.test/y" },
    });

    expect(screen.getByText("כותרת מקור חיצוני")).toBeInTheDocument();
    expect(screen.getByText("מקור חיצוני")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /פתח פריט/ })).not.toBeInTheDocument();
    const openSource = screen.getByRole("link", { name: /פתח מקור/ });
    expect(openSource).toHaveAttribute("href", "https://example.test/y");
    expect(getItem).not.toHaveBeenCalled();
  });

  it("falls back to a placeholder title when neither the item nor the fallback carries one", () => {
    renderCard({ fallback: { url: "https://example.test/z" } });
    expect(screen.getByText("(ללא כותרת)")).toBeInTheDocument();
  });
});
