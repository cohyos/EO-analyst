import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { PayloadRecord, PayloadsResponse } from "@/types/api";

const getPayloads = vi.fn();
const getPayload = vi.fn();
const getPayloadDiff = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getPayloads: (...args: unknown[]) => getPayloads(...args),
    getPayload: (...args: unknown[]) => getPayload(...args),
    getPayloadDiff: (...args: unknown[]) => getPayloadDiff(...args),
  },
}));

import { PayloadsPage } from "./PayloadsPage";

function makePayload(over: Partial<PayloadRecord> = {}): PayloadRecord {
  return {
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
    spec_version_count: 2,
    price_ref_count: 1,
    latest_spec_date: "2026-09-05",
    latest_price_date: "2026-08-20",
    created_at: "2026-08-01T08:00:00+00:00",
    updated_at: "2026-09-05T08:00:00+00:00",
    ...over,
  };
}

function payloadsResponse(payloads: PayloadRecord[]): PayloadsResponse {
  return { payloads, total: payloads.length };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/payloads"]}>
        <PayloadsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getPayloads.mockReset();
  getPayload.mockReset();
  getPayloadDiff.mockReset();
  getPayloads.mockResolvedValue(payloadsResponse([]));
});

describe("PayloadsPage", () => {
  it('shows an honest empty state before any extraction has run', async () => {
    renderPage();
    expect(await screen.findByText('לא זוהו מטע"דים עדיין')).toBeInTheDocument();
    // the empty state must explain *why* (nightly scan not run yet / disabled), not just say "no data"
    expect(screen.getByText(/payload_extract/)).toBeInTheDocument();
  });

  it("renders a fetched payload's canonical name and vendor", async () => {
    getPayloads.mockResolvedValue(payloadsResponse([makePayload()]));
    renderPage();
    expect(await screen.findByText("WESCAM MX-15")).toBeInTheDocument();
    expect(screen.getAllByText("L3Harris WESCAM").length).toBeGreaterThan(0);
  });

  it("opens the detail drawer on row click and fetches the detail endpoint", async () => {
    getPayloads.mockResolvedValue(payloadsResponse([makePayload()]));
    getPayload.mockResolvedValue({
      payload: makePayload(),
      spec_versions: [],
      price_refs: [],
    });
    renderPage();
    const row = await screen.findByText("WESCAM MX-15");
    fireEvent.click(row.closest("tr")!);
    expect(await screen.findByText('פרטי מטע"ד')).toBeInTheDocument();
    expect(getPayload).toHaveBeenCalledWith(1);
  });

  it("filters by free-text search", async () => {
    getPayloads.mockResolvedValue(
      payloadsResponse([makePayload(), makePayload({ id: 2, canonical_name: "Rafael Toplite", vendor_entity_name: "Rafael" })]),
    );
    renderPage();
    await screen.findByText("WESCAM MX-15");
    const search = screen.getByLabelText('חיפוש במטע"דים');
    fireEvent.change(search, { target: { value: "Toplite" } });
    expect(await screen.findByText("Rafael Toplite")).toBeInTheDocument();
    expect(screen.queryByText("WESCAM MX-15")).not.toBeInTheDocument();
  });

  // W19 (docs/REVIEW_2026-09-06_evening.md): manufacturer spec link + honest "not yet
  // documented" state -- image_url/spec_url/spec_source are identity-level (migration 0022).
  it("shows a manufacturer spec link when spec_url is set", async () => {
    getPayloads.mockResolvedValue(payloadsResponse([makePayload()]));
    renderPage();
    await screen.findByText("WESCAM MX-15");
    const link = screen.getByRole("link", { name: "מפרט יצרן" });
    expect(link).toHaveAttribute("href", "https://wescam.com/products/mx-series/");
  });

  it('shows the honest "not yet documented" state when spec_url is null', async () => {
    getPayloads.mockResolvedValue(payloadsResponse([makePayload({ spec_url: null, spec_source: null })]));
    renderPage();
    await screen.findByText("WESCAM MX-15");
    expect(screen.getByText("מפרט/מחיר טרם תועדו")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "מפרט יצרן" })).not.toBeInTheDocument();
  });

  // F33 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md): `vendor` used to be applied client-side
  // against whatever the (500-row-capped) fetch happened to contain -- a match sitting outside
  // that cap could never be found. Asserts the filter is forwarded to `api.getPayloads` itself
  // (server-side filtering, applied before any cap), not just used to re-filter an already-
  // fetched page.
  it("forwards the vendor filter as a getPayloads() call argument", async () => {
    // "Rafael" must exist as a dropdown option (from the vendor-unfiltered baseline/facets fetch)
    // before it can be selected -- a native <select> silently ignores a value with no matching
    // <option>.
    const baseline = [makePayload(), makePayload({ id: 4, canonical_name: "Toplite baseline", vendor_entity_name: "Rafael" })];
    const beyondCap = makePayload({ id: 2, canonical_name: "Beyond the cap", vendor_entity_name: "Rafael" });
    getPayloads.mockImplementation((params: { vendor?: string } = {}) =>
      Promise.resolve(payloadsResponse(params.vendor === "Rafael" ? [beyondCap] : baseline)),
    );
    renderPage();
    await screen.findByText("WESCAM MX-15");

    fireEvent.change(screen.getByRole("combobox", { name: "סינון לפי יצרן" }), {
      target: { value: "Rafael" },
    });

    // The row that only exists in the server's vendor-filtered result must now show up -- it was
    // never present in the earlier (unfiltered) fetch at all, so a client-side-only filter could
    // not possibly have found it.
    expect(await screen.findByText("Beyond the cap")).toBeInTheDocument();
    expect(getPayloads).toHaveBeenCalledWith(expect.objectContaining({ vendor: "Rafael" }));
  });
});
