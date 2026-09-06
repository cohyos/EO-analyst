import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "./I18nContext";
import { useUiStore } from "@/store/uiStore";
import type { PayloadRecord, PayloadsResponse } from "@/types/api";

// W25 (docs/REVIEW_2026-09-06_evening.md): English-mode audit. Scoped to this round's file
// ownership -- PayloadsPage/payloads components, the SettingsPage jobs table, and the shell nav
// rail (nav labels are explicitly named in W25) -- not the whole app, since most other screens
// (BdPage, TendersPage, reports, etc.) belong to other engineers actively editing them this round
// and are out of scope for this file's edits. Content returned by the API is exempt (Hebrew by
// design, e.g. a payload's own `notes` or an item's title) -- these tests mock API responses with
// no Hebrew content so the assertion stays a clean "no hardcoded Hebrew UI string" check.

const HEBREW_LETTERS = /[֐-׿]/;

function findHebrewText(container: HTMLElement): string[] {
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  const hits: string[] = [];
  let node = walker.nextNode();
  while (node) {
    const text = node.textContent ?? "";
    if (HEBREW_LETTERS.test(text)) hits.push(text.trim());
    node = walker.nextNode();
  }
  return hits;
}

const getPayloads = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getPayloads: (...args: unknown[]) => getPayloads(...args),
  },
}));

import { PayloadsPage } from "@/pages/PayloadsPage";
import { NavRail } from "@/components/shell/NavRail";

function payloadsResponse(payloads: PayloadRecord[]): PayloadsResponse {
  return { payloads, total: payloads.length };
}

function renderPayloadsPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/payloads"]}>
        <I18nProvider>
          <PayloadsPage />
        </I18nProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getPayloads.mockReset();
  useUiStore.getState().setLocale("en");
});

afterEach(() => {
  useUiStore.getState().setLocale("he");
});

describe("English mode -- no hardcoded Hebrew (W25)", () => {
  it("NavRail renders every nav label in English", () => {
    const { container } = render(
      <MemoryRouter>
        <I18nProvider>
          <NavRail />
        </I18nProvider>
      </MemoryRouter>,
    );
    expect(findHebrewText(container)).toEqual([]);
    expect(screen.getByRole("navigation", { name: "Main navigation" })).toBeInTheDocument();
    expect(screen.getAllByText("EO Payloads").length).toBeGreaterThan(0);
  });

  it("PayloadsPage's empty state renders in English", async () => {
    getPayloads.mockResolvedValue(payloadsResponse([]));
    const { container } = renderPayloadsPage();
    await screen.findByText("No payloads identified yet");
    expect(findHebrewText(container)).toEqual([]);
  });

  it("PayloadsPage's table + manufacturer spec link render in English", async () => {
    const payload: PayloadRecord = {
      id: 1,
      canonical_name: "WESCAM MX-15",
      vendor_entity_name: "L3Harris WESCAM",
      family: "MX",
      category: "gimbal",
      first_seen: "2026-08-01",
      last_seen: "2026-09-05",
      notes: null,
      image_url: null,
      spec_url: "https://wescam.com/products/mx-series/",
      spec_source: "wescam.com",
      spec_version_count: 0,
      price_ref_count: 0,
      latest_spec_date: null,
      latest_price_date: null,
      created_at: "2026-08-01T08:00:00+00:00",
      updated_at: "2026-09-05T08:00:00+00:00",
    };
    getPayloads.mockResolvedValue(payloadsResponse([payload]));
    const { container } = renderPayloadsPage();
    await screen.findByText("WESCAM MX-15");
    expect(screen.getByRole("link", { name: "Manufacturer spec" })).toBeInTheDocument();
    expect(findHebrewText(container)).toEqual([]);
  });

  it("PayloadsPage's honest 'not yet documented' state renders in English", async () => {
    const payload: PayloadRecord = {
      id: 2,
      canonical_name: "PVP Thermal Core",
      vendor_entity_name: "PVP Photonics",
      family: null,
      category: "detector_core",
      first_seen: null,
      last_seen: null,
      notes: null,
      image_url: null,
      spec_url: null,
      spec_source: null,
      spec_version_count: 0,
      price_ref_count: 0,
      latest_spec_date: null,
      latest_price_date: null,
      created_at: "2026-08-01T08:00:00+00:00",
      updated_at: "2026-09-05T08:00:00+00:00",
    };
    getPayloads.mockResolvedValue(payloadsResponse([payload]));
    const { container } = renderPayloadsPage();
    await screen.findByText("PVP Thermal Core");
    expect(screen.getByText("Spec/price not yet documented")).toBeInTheDocument();
    expect(findHebrewText(container)).toEqual([]);
  });
});
