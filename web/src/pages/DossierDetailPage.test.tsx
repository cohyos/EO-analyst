import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { DossierDetail, DossierRunDetail, ProductDossierOut } from "@/types/api";

const getDossier = vi.fn();
const getDossierRun = vi.fn();
const postDossierRerun = vi.fn();
// PD-vocab-ui (2026-09-09): DossierDetailPage now also fetches the full dossier list (for the
// competitors table's "השווה" / "הרץ סקירה למוצר זה" match, docs/PLAN_SPEC_VOCABULARY.md §5.2
// entry point 2) and can POST a new dossier for an unmatched competitor -- both mocked here so
// every pre-existing test in this file keeps working unchanged.
const getDossiers = vi.fn();
const postDossier = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getDossier: (...args: unknown[]) => getDossier(...args),
    getDossierRun: (...args: unknown[]) => getDossierRun(...args),
    postDossierRerun: (...args: unknown[]) => postDossierRerun(...args),
    getDossiers: (...args: unknown[]) => getDossiers(...args),
    postDossier: (...args: unknown[]) => postDossier(...args),
  },
}));

import { DossierDetailPage } from "./DossierDetailPage";

function emptyDossierOut(): ProductDossierOut {
  return {
    identity: {
      product_name: "SPECTRO XR",
      vendor: "Elbit Systems",
      product_family: null,
      category_he: null,
      first_announced: null,
      status_he: "בייצור",
      cites: [],
    },
    summary: [{ text_he: "תקציר בדיקה.", cites: [1] }],
    specifications: [],
    variants_and_versions: [],
    performance: [],
    maturity: {
      trl: 9,
      operational_users: [],
      platforms_integrated: [],
      first_fielding: null,
      assessment_he: null,
      cites: [],
    },
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
  };
}

function detail(over: Partial<DossierDetail> = {}): DossierDetail {
  return {
    product_key: "elbit-systems-spectro-xr",
    product_name: "SPECTRO XR",
    vendor: "Elbit Systems",
    aliases: ["Spectro"],
    dossiers: [
      { id: 501, created_at: "2026-09-06T21:10:00+03:00", outcome: "found", confidence: 0.82, report_id: 940, llm_leg: "local" },
    ],
    latest: {
      ...emptyDossierOut(),
      sources: [
        { n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: "2026-09-07T06:00:00+03:00" },
      ],
    },
    pending_job: null,
    ...over,
  };
}

function renderPage(key = "elbit-systems-spectro-xr") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/dossiers/${key}`]}>
        <Routes>
          <Route path="/dossiers/:key" element={<DossierDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getDossier.mockReset();
  getDossierRun.mockReset();
  postDossierRerun.mockReset();
  getDossiers.mockReset();
  postDossier.mockReset();
  getDossiers.mockResolvedValue([]);
});

describe("DossierDetailPage (PD-ui)", () => {
  it("renders the header identity, status, TRL and confidence", async () => {
    getDossier.mockResolvedValue(detail());
    renderPage();

    expect(await screen.findByRole("heading", { name: "SPECTRO XR" })).toBeInTheDocument();
    expect(screen.getByText("Elbit Systems")).toBeInTheDocument();
    expect(screen.getByText("בייצור")).toBeInTheDocument();
    expect(screen.getAllByText(/TRL/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/82%/).length).toBeGreaterThan(0);
  });

  it("renders every fixed section heading in order", async () => {
    getDossier.mockResolvedValue(detail());
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });

    const order = [
      "תקציר",
      "מפרט",
      "גרסאות",
      "פלטפורמות",
      "ביצועים",
      "בשלות",
      "ציר זמן",
      "עסקאות",
      "מחירים",
      "אומדן תמחור אנליטי (ביטחון נמוך)",
      "שותפויות",
      "מתחרים",
      "ניתוח ביקורתי של טענות היצרן",
      "פטנטים",
      "מכרזים ותחזיות",
      "פערים",
      "מעקב פערים",
      "משמעות עסקית",
      "מה השתנה",
      "מקורות",
    ];
    for (const label of order) {
      expect(screen.getByRole("heading", { name: label, level: 3 })).toBeInTheDocument();
    }
  });

  it("shows the empty-table placeholder for a still-flat section with no rows", async () => {
    // PD-vocab-ui (2026-09-09): specifications/performance now render through DossierSpecTable,
    // whose own required-vocabulary-key placeholders mean those two sections are no longer
    // "empty" even with zero rows (see the dedicated grouped-table test below) -- versions stays
    // a plain DossierTable, so it's the one still exercising the flat empty-label path.
    getDossier.mockResolvedValue(detail());
    renderPage();
    expect(await screen.findByText("אין גרסאות מתועדות")).toBeInTheDocument();
  });

  it("PD-vocab-ui: specifications table renders required-vocabulary placeholders when no data was found", async () => {
    getDossier.mockResolvedValue(detail());
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    // "משקל" (weight) is a required common.weight vocabulary key -- with no product_line and no
    // matching row it must still render as a distinct "not found, required" placeholder, not be
    // silently omitted the way a non-required missing param would be.
    expect(await screen.findByText("משקל")).toBeInTheDocument();
  });

  it("PD-vocab-ui: keyed specification/performance rows render under their vocabulary group with the canonical label", async () => {
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          specifications: [
            { parameter_he: "משקל", value: "95", unit: "ק\"ג", variant: null, source_kind: "datasheet", cites: [1], key: "weight" },
          ],
          performance: [
            {
              metric_he: "DRI בטווח ארוך",
              claimed_value: "26 ק\"מ",
              tested_value: null,
              conditions_he: null,
              cites: [1],
              key: "dri_at_long_range",
            },
          ],
          sources: [{ n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null }],
        },
      }),
    );
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    // Group headers per docs/PLAN_SPEC_VOCABULARY.md §2's fixed group_he order.
    expect(await screen.findByText("מכניקה וסביבה")).toBeInTheDocument();
    expect(screen.getByText("ביצועי מערכת")).toBeInTheDocument();
    expect(screen.getByText("95")).toBeInTheDocument();
    // renderBidiRuns can split "26 ק"מ" across sibling spans -- assert on aggregate text.
    await waitFor(() => expect(document.body.textContent ?? "").toContain('26 ק"מ'));
  });

  it("PD-vocab-ui: an unkeyed (legacy) specification row still renders, in the appendix table", async () => {
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          specifications: [
            { parameter_he: "טווח גילוי (אדם)", value: "26", unit: 'ק"מ', variant: null, source_kind: "datasheet", cites: [1] },
          ],
          sources: [{ n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null }],
        },
      }),
    );
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    expect(await screen.findByText("פרמטרים נוספים")).toBeInTheDocument();
    expect(screen.getByText("טווח גילוי (אדם)")).toBeInTheDocument();
  });

  it("shows the pending-run banner while a job is in flight", async () => {
    getDossier.mockResolvedValue(detail({ pending_job: { job_id: "77", state: "running", progress: [] } }));
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    expect(
      await screen.findByText("הסקירה נבנית ברקע — הדף יתעדכן אוטומטית כשתושלם"),
    ).toBeInTheDocument();
  });

  it("PD-fix item 5: lists per-topic progress with status and elapsed time", async () => {
    getDossier.mockResolvedValue(
      detail({
        pending_job: {
          job_id: "77",
          state: "running",
          progress: [
            { topic: "specifications", title_he: "מפרט ודף נתונים", status: "done", seconds: 12, sources_found: 3 },
            { topic: "versions", title_he: "גרסאות וציר זמן", status: "running", seconds: null, sources_found: null },
            { topic: "regulatory", title_he: "רגולציה וייצוא", status: "pending", seconds: null, sources_found: null },
          ],
        },
      }),
    );
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });

    const list = await screen.findByTestId("dossier-progress-list");
    expect(list).toBeInTheDocument();
    expect(await screen.findByTestId("dossier-progress-topic-specifications")).toHaveTextContent("מפרט");
    expect(screen.getByTestId("dossier-progress-topic-specifications")).toHaveTextContent("הושלם");
    // "regulatory" has no dedicated top-level nav section -- falls back to the backend's own
    // title_he rather than a raw i18n key.
    expect(screen.getByTestId("dossier-progress-topic-regulatory")).toHaveTextContent("רגולציה וייצוא");
  });

  it("PD-fix-2 item 3: deals table shows date-only, Hebrew kind label, and region fallback", async () => {
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          deals: [
            {
              date: "2026-09-02 09:04:00+03:00",
              date_kind: "published",
              customer: "לקוח בינלאומי (לא מזוהה)",
              country: "",
              region_he: "אסיה-פסיפיק",
              kind: "FMS",
              amount: "כ-80 מיליון דולר",
              currency: "USD",
              quantity: null,
              platform: null,
              cites: [1],
              confidence: 0.7,
            },
          ],
          sources: [
            { n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null },
          ],
        },
      }),
    );
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });

    // The date/kind-label text can be split across bidi-run spans (`renderBidiRuns`), so assert
    // on the page's own aggregate text rather than a single element's text content.
    await waitFor(() => expect(document.body.textContent ?? "").toContain("2026-09-02"));
    const bodyText = document.body.textContent ?? "";
    // date only -- no time-of-day/timezone -- with the "published" (backfilled) marker.
    expect(bodyText).toContain("תאריך פרסום");
    expect(bodyText).not.toContain("09:04");
    // region_he shown in place of an empty country.
    expect(bodyText).toContain("אסיה-פסיפיק");
    // Hebrew label for a deal-only kind (FMS), not the raw enum value alone.
    expect(bodyText).toContain("מכירת ציוד ביטחוני זר");
  });

  it("shows a not-found state for an unknown product key", async () => {
    getDossier.mockRejectedValue(new Error("not_found"));
    renderPage("unknown");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("triggers a rerun from the detail page's 'הרץ שוב' button only after confirming", async () => {
    const user = userEvent.setup();
    getDossier.mockResolvedValue(detail());
    postDossierRerun.mockResolvedValue({ job_id: "job-3" });
    renderPage();

    const button = await screen.findByTestId("dossier-detail-rerun");
    await user.click(button);

    // Round-3 mobile fix (UI-MOBILE-iphone-r3.md #3): the button now opens a confirm dialog
    // ("הרצה מחדש אורכת כ-30–60 דקות") instead of firing the mutation directly.
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(postDossierRerun).not.toHaveBeenCalled();

    await user.click(screen.getByText("אישור"));
    await waitFor(() => expect(postDossierRerun).toHaveBeenCalledWith("elbit-systems-spectro-xr"));
  });

  it("does not trigger a rerun when the confirm dialog is cancelled", async () => {
    const user = userEvent.setup();
    getDossier.mockResolvedValue(detail());
    renderPage();

    const button = await screen.findByTestId("dossier-detail-rerun");
    await user.click(button);
    await user.click(await screen.findByText("ביטול"));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(postDossierRerun).not.toHaveBeenCalled();
  });

  it("PD-vocab-ui §5.2 entry point 2: competitor row offers 'הרץ סקירה למוצר זה' when unmatched, launches a dossier on click", async () => {
    const user = userEvent.setup();
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          competitors: [{ product: "WESCAM MX-25", vendor: "L3Harris", comparison_he: "מוצר מתחרה.", cites: [] }],
          sources: [],
        },
      }),
    );
    getDossiers.mockResolvedValue([]);
    postDossier.mockResolvedValue({ job_id: "job-9", product_key: "l3harris-wescam-mx-25" });
    renderPage();

    const runButton = await screen.findByText("הרץ סקירה למוצר זה");
    await user.click(runButton);
    await waitFor(() =>
      expect(postDossier).toHaveBeenCalledWith({ product_name: "WESCAM MX-25", vendor: "L3Harris" }),
    );
  });

  it("PD-vocab-ui §5.2 entry point 2: competitor row offers a 'השווה' link when a matching dossier already exists", async () => {
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          competitors: [{ product: "WESCAM MX-25", vendor: "L3Harris", comparison_he: "מוצר מתחרה.", cites: [] }],
          sources: [],
        },
      }),
    );
    getDossiers.mockResolvedValue([
      { product_key: "l3harris-wescam-mx-25", product_name: "WESCAM MX-25", vendor: "L3Harris", latest: null, count: 0 },
    ]);
    renderPage();

    const link = await screen.findByRole("link", { name: "השווה" });
    expect(link).toHaveAttribute(
      "href",
      "/dossiers/compare?keys=elbit-systems-spectro-xr,l3harris-wescam-mx-25",
    );
  });

  it("expands a run-history row's 'השווה' to show its what_changed sentences", async () => {
    const user = userEvent.setup();
    getDossier.mockResolvedValue(detail());
    const runDetail: DossierRunDetail = {
      id: 501,
      created_at: "2026-09-06T21:10:00+03:00",
      outcome: "found",
      confidence: 0.82,
      report_id: 940,
      llm_leg: "local",
      data: { ...emptyDossierOut(), what_changed: [{ text_he: "התווספה עסקה חדשה.", cites: [1] }] },
      sources: [{ n: 1, url: "https://example.test/1", title: "Source 1", kind: null, reliability: null, accessed_at: null }],
      path_docx: null,
      path_md: null,
      path_html: null,
    };
    getDossierRun.mockResolvedValue(runDetail);
    renderPage();

    await screen.findByTestId("dossier-run-history");
    await user.click(screen.getByTestId("dossier-run-compare-501"));

    await waitFor(() => expect(getDossierRun).toHaveBeenCalledWith("elbit-systems-spectro-xr", 501));
    expect(await screen.findByText("התווספה עסקה חדשה.")).toBeInTheDocument();
  });

  // --------------------------------------------------------------------------
  // LESSONS-2 (2026-09-09, docs/qa/content_review/LESSONS-fable-dossier.md).
  // --------------------------------------------------------------------------

  it("LESSONS-2 item 1: renders a timeline row with its Hebrew kind label", async () => {
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          timeline: [{ date: "2020-05-01", event_he: "עסקה: US Air Force.", kind: "contract", cites: [1] }],
          sources: [{ n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null }],
        },
      }),
    );
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    // mixed Hebrew/Latin text is split across bidi-run spans (renderBidiRuns) -- assert on the
    // page's own aggregate text rather than a single element's text content.
    await waitFor(() => expect(document.body.textContent ?? "").toContain("US Air Force"));
    expect(document.body.textContent ?? "").toContain("עסקה");
  });

  it("LESSONS-2 item 2: shows the pricing-estimate placeholder when null, and the disclaimer when present", async () => {
    getDossier.mockResolvedValue(detail());
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    expect(
      await screen.findByText("אין מספיק נתונים מגובים (חוזה עם היקף ועוגן שוק) להפקת אומדן"),
    ).toBeInTheDocument();
  });

  it("LESSONS-2 item 2: renders the mandatory disclaimer and market anchors when a pricing_estimate is present", async () => {
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          pricing_estimate: {
            method_he: "נגזר מהיקפי חוזים לפי כמות משוערת.",
            assumptions: [{ text_he: "כמות משוערת 10 יחידות.", cites: [1] }],
            market_anchors: [{ product_he: "MX-15", price_range_he: "1-2M$", cites: [1] }],
            range_low: 1_000_000,
            range_high: 2_000_000,
            currency: "USD",
            basis_he: "ליחידה",
            confidence: "low",
          },
          sources: [{ n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null }],
        },
      }),
    );
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    expect(await screen.findByText("אומדן אנליטי, לא נתון ממקור")).toBeInTheDocument();
    expect(screen.getByText("MX-15")).toBeInTheDocument();
  });

  it("LESSONS-2 item 3: renders a claims_review row with its verdict label", async () => {
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          claims_review: [
            {
              claim_he: "20 אינץ' בגוף 15.",
              basis_he: "מבוסס פיזיקלית.",
              verifiability_he: "מדידה עצמאית.",
              comparability_he: "לא בר השוואה ל-Toplite.",
              verdict: "unverified",
              cites: [1],
            },
          ],
          sources: [{ n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null }],
        },
      }),
    );
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    await waitFor(() => expect(document.body.textContent ?? "").toContain("20 אינץ'"));
    expect(screen.getByText("לא מאומת")).toBeInTheDocument();
  });

  it("LESSONS-2 item 5: renders a gaps_tracking row with its status label", async () => {
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          gaps_tracking: [{ gap_he: "מחיר יחידה לא ידוע.", status: "closed", cites: [1] }],
          sources: [{ n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null }],
        },
      }),
    );
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    expect(await screen.findByText("מחיר יחידה לא ידוע.")).toBeInTheDocument();
    expect(screen.getByText("נסגר")).toBeInTheDocument();
  });

  it("LESSONS-2 item 7: renders a platforms row and the variants table's evidence/confidence columns", async () => {
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          variants_and_versions: [
            { name: "Block II", year: null, changes_he: "", platforms: [], cites: [1], evidence_he: "מוזכר בעלון 2023.", confidence: "high" },
          ],
          platforms: [{ platform: "Hermes 900", domain: "אווירי", integration_evidence_he: "מוזכר בעסקה עם US Air Force.", cites: [1] }],
          sources: [{ n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null }],
        },
      }),
    );
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    expect(await screen.findByText("Hermes 900")).toBeInTheDocument();
    await waitFor(() => expect(document.body.textContent ?? "").toContain("מוזכר בעלון 2023"));
    expect(document.body.textContent ?? "").toContain("גבוה");
  });

  it("LESSONS-2 item 4: deals table shows the row confidence alongside its citations", async () => {
    getDossier.mockResolvedValue(
      detail({
        latest: {
          ...emptyDossierOut(),
          deals: [
            {
              date: "2020-05-01",
              date_kind: "deal",
              customer: "US Air Force",
              country: "US",
              kind: "contract_award",
              amount: "$1M",
              currency: "USD",
              quantity: null,
              platform: null,
              cites: [1, 2],
              confidence: 0.7,
              confidence_level: "high",
            },
          ],
          sources: [
            { n: 1, url: "https://example.test/1", title: "Source 1", kind: "official", reliability: "high", accessed_at: null },
            { n: 2, url: "https://example.test/2", title: "Source 2", kind: "press", reliability: "secondary", accessed_at: null },
          ],
        },
      }),
    );
    renderPage();
    await screen.findByRole("heading", { name: "SPECTRO XR" });
    await waitFor(() => expect(document.body.textContent ?? "").toContain("גבוה"));
  });
});
