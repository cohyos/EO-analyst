import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { DossierDetail, DossierRunDetail, ProductDossierOut } from "@/types/api";

const getDossier = vi.fn();
const getDossierRun = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getDossier: (...args: unknown[]) => getDossier(...args),
    getDossierRun: (...args: unknown[]) => getDossierRun(...args),
  },
}));

import { DossierComparePage } from "./DossierComparePage";

function emptyDossierOut(): ProductDossierOut {
  return {
    identity: { product_name: "", vendor: null, product_family: null, category_he: null, first_announced: null, status_he: null, cites: [] },
    summary: [],
    specifications: [],
    variants_and_versions: [],
    performance: [],
    maturity: { trl: null, operational_users: [], platforms_integrated: [], first_fielding: null, assessment_he: null, cites: [] },
    deals: [],
    pricing: [],
    partnerships: [],
    competitors: [],
    regulatory_export: { export_regime_he: null, restrictions_he: null, cites: [] },
    patents: [],
    tenders_and_forecasts: [],
    risks_and_gaps: [],
    what_changed: [],
    bd_implications: [],
    other_specifications: [],
  };
}

function makeDetail(
  key: string,
  name: string,
  id: number,
  specs: ProductDossierOut["specifications"] = [],
): DossierDetail {
  return {
    product_key: key,
    product_name: name,
    vendor: null,
    aliases: [],
    dossiers: [{ id, created_at: "2026-09-06T21:10:00+03:00", outcome: "found", confidence: 0.8, report_id: null, llm_leg: "local" }],
    latest: {
      ...emptyDossierOut(),
      identity: { ...emptyDossierOut().identity, product_name: name },
      specifications: specs,
      sources: [{ n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null }],
    },
    pending_job: null,
  };
}

function makeRun(key: string, name: string, id: number, productLine: string | null, specs: ProductDossierOut["specifications"]): DossierRunDetail {
  return {
    id,
    created_at: "2026-09-06T21:10:00+03:00",
    outcome: "found",
    confidence: 0.8,
    report_id: null,
    llm_leg: "local",
    data: { ...emptyDossierOut(), specifications: specs },
    sources: [{ n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null }],
    path_docx: null,
    path_md: null,
    path_html: null,
    product_key: key,
    product_name: name,
    vendor: null,
    product_line: productLine,
  };
}

function renderPage(search: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/dossiers/compare${search}`]}>
        <Routes>
          <Route path="/dossiers/compare" element={<DossierComparePage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getDossier.mockReset();
  getDossierRun.mockReset();
});

describe("DossierComparePage (PD-vocab-ui §5.2)", () => {
  it("rejects a keys list outside the 2-3 range", async () => {
    // The page still fires its (fixed-arity) hooks for the one slot before the early-return
    // check runs -- give it something to resolve so no unrelated "query data is undefined"
    // console noise obscures the assertion below.
    getDossier.mockResolvedValue(makeDetail("only-one", "Only One", 1));
    renderPage("?keys=only-one");
    expect(await screen.findByText("יש לבחור בין 2 ל-3 מוצרים להשוואה")).toBeInTheDocument();
  });

  it("warns instead of rendering a table when the selected products don't share a product_line", async () => {
    getDossier.mockImplementation((key: string) =>
      Promise.resolve(key === "a" ? makeDetail("a", "Product A", 1) : makeDetail("b", "Product B", 2)),
    );
    getDossierRun.mockImplementation((key: string) =>
      Promise.resolve(
        key === "a" ? makeRun("a", "Product A", 1, "targeting_pods", []) : makeRun("b", "Product B", 2, "mws_eo", []),
      ),
    );
    renderPage("?keys=a,b");

    expect(await screen.findByText("המוצרים שנבחרו אינם מאותו קו מוצר")).toBeInTheDocument();
  });

  it("renders a grouped comparison table for products sharing one product_line", async () => {
    const specsA: ProductDossierOut["specifications"] = [
      { parameter_he: "משקל", value: "95", unit: 'ק"ג', variant: null, source_kind: "datasheet", cites: [1], key: "weight" },
    ];
    const specsB: ProductDossierOut["specifications"] = [
      { parameter_he: "משקל", value: "60", unit: 'ק"ג', variant: null, source_kind: "datasheet", cites: [1], key: "weight" },
    ];
    getDossier.mockImplementation((key: string) =>
      Promise.resolve(key === "a" ? makeDetail("a", "Product A", 1, specsA) : makeDetail("b", "Product B", 2, specsB)),
    );
    getDossierRun.mockImplementation((key: string) =>
      Promise.resolve(
        key === "a"
          ? makeRun("a", "Product A", 1, "border_long_range_eo", specsA)
          : makeRun("b", "Product B", 2, "border_long_range_eo", specsB),
      ),
    );
    renderPage("?keys=a,b");

    // "Product A"/"Product B" appear twice each -- the page header link and the comparison
    // table's own column header.
    expect((await screen.findAllByText("Product A")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Product B").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("95")).toBeInTheDocument();
    expect(screen.getByText("60")).toBeInTheDocument();
    // Both products' own column headers render under the shared "מכניקה וסביבה" group.
    expect(screen.getByText("מכניקה וסביבה")).toBeInTheDocument();
  });

  it("shows a not-found state when a selected key has no dossiers at all", async () => {
    getDossier.mockImplementation((key: string) =>
      Promise.resolve(key === "a" ? makeDetail("a", "Product A", 1) : { ...makeDetail("b", "Product B", 2), dossiers: [], latest: null }),
    );
    getDossierRun.mockResolvedValue(makeRun("a", "Product A", 1, "targeting_pods", []));
    renderPage("?keys=a,b");

    expect(await screen.findByText("אחד או יותר מהמוצרים שנבחרו לא נמצא")).toBeInTheDocument();
  });
});
