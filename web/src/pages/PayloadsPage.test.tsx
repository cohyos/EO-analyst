import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import type { PayloadFacetsResponse, PayloadRecord, PayloadsResponse } from "@/types/api";

const getPayloads = vi.fn();
const getPayload = vi.fn();
const getPayloadDiff = vi.fn();
const getPayloadFacets = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getPayloads: (...args: unknown[]) => getPayloads(...args),
    getPayload: (...args: unknown[]) => getPayload(...args),
    getPayloadDiff: (...args: unknown[]) => getPayloadDiff(...args),
    getPayloadFacets: (...args: unknown[]) => getPayloadFacets(...args),
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

function facetsResponse(over: Partial<PayloadFacetsResponse> = {}): PayloadFacetsResponse {
  return { vendors: [], categories: [], ...over };
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
  getPayloadFacets.mockReset();
  getPayloads.mockResolvedValue(payloadsResponse([]));
  getPayloadFacets.mockResolvedValue(facetsResponse());
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

  // F33 review follow-up (SOL-REVIEW-2026-09-24): `q` is now forwarded to `api.getPayloads`
  // (server-side ILIKE match, applied before the row cap) instead of re-filtering the already-
  // fetched, already-capped page client-side -- a match sitting outside the cap could otherwise
  // never be found no matter how exact the search text.
  it("filters by free-text search", async () => {
    const toplite = makePayload({ id: 2, canonical_name: "Rafael Toplite", vendor_entity_name: "Rafael" });
    getPayloads.mockImplementation((params: { q?: string } = {}) =>
      Promise.resolve(payloadsResponse(params.q === "Toplite" ? [toplite] : [makePayload()])),
    );
    renderPage();
    await screen.findByText("WESCAM MX-15");
    const search = screen.getByLabelText('חיפוש במטע"דים');
    fireEvent.change(search, { target: { value: "Toplite" } });
    expect(await screen.findByText("Rafael Toplite")).toBeInTheDocument();
    expect(screen.queryByText("WESCAM MX-15")).not.toBeInTheDocument();
    expect(getPayloads).toHaveBeenCalledWith(expect.objectContaining({ q: "Toplite" }));
  });

  it("a text match outside the row cap is still found (F33: q is not a client-side re-filter)", async () => {
    // Only the server's q-filtered response ever contains this row -- the unfiltered baseline
    // fetch never does, so a client-side-only filter could not possibly have found it.
    const beyondCap = makePayload({ id: 3, canonical_name: "Beyond the cap MX-30" });
    getPayloads.mockImplementation((params: { q?: string } = {}) =>
      Promise.resolve(payloadsResponse(params.q === "Beyond" ? [beyondCap] : [makePayload()])),
    );
    renderPage();
    await screen.findByText("WESCAM MX-15");
    fireEvent.change(screen.getByLabelText('חיפוש במטע"דים'), { target: { value: "Beyond" } });
    expect(await screen.findByText("Beyond the cap MX-30")).toBeInTheDocument();
    expect(getPayloads).toHaveBeenCalledWith(expect.objectContaining({ q: "Beyond" }));
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
    // "Rafael" must exist as a dropdown option (from the uncapped facets fetch, F33 review
    // follow-up) before it can be selected -- a native <select> silently ignores a value with no
    // matching <option>.
    getPayloadFacets.mockResolvedValue(facetsResponse({ vendors: ["L3Harris WESCAM", "Rafael"] }));
    const beyondCap = makePayload({ id: 2, canonical_name: "Beyond the cap", vendor_entity_name: "Rafael" });
    getPayloads.mockImplementation((params: { vendor?: string } = {}) =>
      Promise.resolve(payloadsResponse(params.vendor === "Rafael" ? [beyondCap] : [makePayload()])),
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

  // R06 (SOL-REVIEW2-2026-09-24 review): a payload the SERVER matched only via its `notes` field
  // (`eoa.api.routes.payloads.list_payloads` searches notes) must still be visible in the default
  // tree view -- before this fix, the client's `filterPayloadTree(rawTree, filters.q)` re-filter
  // only checked canonical_name/variant, so a notes-only server match was silently dropped from
  // the tree even though `getPayloads` had already returned it.
  it("a notes-only server match stays visible in tree view (R06)", async () => {
    const notesMatch = makePayload({
      id: 5,
      canonical_name: "Generic EO Pod",
      vendor_entity_name: "Acme",
      notes: "Also marketed under the codename Nightjar in export literature.",
    });
    getPayloads.mockImplementation((params: { q?: string } = {}) =>
      Promise.resolve(payloadsResponse(params.q === "Nightjar" ? [notesMatch] : [makePayload()])),
    );
    renderPage();
    await screen.findByText("WESCAM MX-15");
    fireEvent.change(screen.getByLabelText('חיפוש במטע"דים'), { target: { value: "Nightjar" } });
    expect(await screen.findByText("Generic EO Pod")).toBeInTheDocument();
  });

  // R06/F33: server-side "load more" pagination -- a second page's rows must be reachable and
  // appended to what's already on screen, not replace it.
  it("a load-more click fetches page 2 and appends its rows", async () => {
    const page1 = makePayload({ id: 1, canonical_name: "WESCAM MX-15" });
    const page2 = makePayload({ id: 2, canonical_name: "Rafael Toplite", vendor_entity_name: "Rafael" });
    getPayloads.mockImplementation((params: { page?: number } = {}) =>
      Promise.resolve({
        payloads: params.page === 2 ? [page2] : [page1],
        total: 2,
        page: params.page ?? 1,
        limit: 200,
        has_more: (params.page ?? 1) === 1,
      }),
    );
    renderPage();
    await screen.findByText("WESCAM MX-15");
    const loadMore = await screen.findByRole("button", { name: /טען עוד/ });
    fireEvent.click(loadMore);
    expect(await screen.findByText("Rafael Toplite")).toBeInTheDocument();
    // The first page's row must still be visible -- "load more" appends, it doesn't replace.
    expect(screen.getByText("WESCAM MX-15")).toBeInTheDocument();
    expect(getPayloads).toHaveBeenCalledWith(expect.objectContaining({ page: 2 }));
  });

  // R09 (SOL-REVIEW2-2026-09-24 review): the "database empty" empty-state must key off the
  // facets endpoint's unfiltered `total`, not off facet VALUE presence -- a table where every
  // existing row has a null vendor must not be reported as empty.
  it("does not show the 'database empty' state when facets.total > 0 but every vendor is null (R09)", async () => {
    getPayloadFacets.mockResolvedValue(facetsResponse({ vendors: [], categories: [], total: 3 }));
    getPayloads.mockResolvedValue(payloadsResponse([makePayload({ vendor_entity_name: null })]));
    renderPage();
    expect(await screen.findByText("WESCAM MX-15")).toBeInTheDocument();
    expect(screen.queryByText('לא זוהו מטע"דים עדיין')).not.toBeInTheDocument();
  });
});
