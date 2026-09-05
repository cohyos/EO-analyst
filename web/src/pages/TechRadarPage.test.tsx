import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { ItemCard, TechRadarResponse } from "@/types/api";

const getTechRadar = vi.fn();
const getTechItems = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getTechRadar: (...args: unknown[]) => getTechRadar(...args),
    getTechItems: (...args: unknown[]) => getTechItems(...args),
  },
}));

import { TechRadarPage } from "./TechRadarPage";

function makeRadar(over: Partial<TechRadarResponse> = {}): TechRadarResponse {
  return {
    weeks: 12,
    maturities: ["lab", "prototype", "qualified", "fielded"],
    subdomains: [
      {
        subdomain: "droic_digital_pixel",
        label_he: "FPA עם פיקסל דיגיטלי",
        counts: { lab: 2, prototype: 1, qualified: 0, fielded: 0 },
        total: 3,
        sparkline: [1, 0, 1, 2],
      },
      {
        subdomain: "swir_eswir",
        label_he: "SWIR/eSWIR",
        counts: { lab: 0, prototype: 0, qualified: 0, fielded: 0 },
        total: 0,
        sparkline: [],
      },
    ],
    ...over,
  };
}

function makeItem(over: Partial<ItemCard> = {}): ItemCard {
  return {
    id: 11,
    title: "Novel digital-pixel FPA readout architecture",
    url: "https://arxiv.test/1",
    source_name: "arXiv eess.IV",
    published_at: "2026-09-01T10:00:00Z",
    lang: "en",
    domain: "tech_dev",
    subdomain: "droic_digital_pixel",
    report_kind: "science",
    trl: "academic",
    geography: "other",
    score: 12,
    level: "archive",
    triage_reason: null,
    summary_he: "מאמר על ארכיטקטורת קריאה חדשה",
    so_what_he: "להערכתנו, עשוי לקצר את זמן ההבשלה של פיקסלים דיגיטליים.",
    entities_mentioned: [],
    tags: [],
    security_status: "clean",
    dedup_of: null,
    key_facts: [],
    uncertainty_he: null,
    tech_maturity: "lab",
    tech_actor_kind: "academia",
    tech_readiness_note_he: "",
    ...over,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <TechRadarPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("TechRadarPage", () => {
  beforeEach(() => {
    getTechRadar.mockReset();
    getTechItems.mockReset();
  });

  it("renders the subdomain x maturity matrix from GET /api/tech/radar", async () => {
    getTechRadar.mockResolvedValue(makeRadar());
    renderPage();
    expect(await screen.findByText("FPA עם פיקסל דיגיטלי")).toBeInTheDocument();
    expect(screen.getByTestId("radar-cell-droic_digital_pixel-lab")).toHaveTextContent("2");
  });

  it("shows an empty state when no subdomain has any activity", async () => {
    getTechRadar.mockResolvedValue(makeRadar({ subdomains: [] }));
    renderPage();
    expect(await screen.findByText("אין עדיין נתוני מעקב טכנולוגי")).toBeInTheDocument();
  });

  it("clicking a radar cell fetches and shows the filtered item list", async () => {
    getTechRadar.mockResolvedValue(makeRadar());
    getTechItems.mockResolvedValue({ total: 1, items: [makeItem()] });
    renderPage();

    const cell = await screen.findByTestId("radar-cell-droic_digital_pixel-lab");
    fireEvent.click(cell);

    await waitFor(() => expect(getTechItems).toHaveBeenCalled());
    expect(getTechItems.mock.calls[0][0]).toMatchObject({
      subdomain: "droic_digital_pixel",
      maturity: "lab",
    });
    expect(await screen.findByTestId("tech-item-11")).toBeInTheDocument();
    expect(screen.getByText(/לקצר את זמן ההבשלה/)).toBeInTheDocument();
  });
});
