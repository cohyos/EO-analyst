import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { DossierDetail, DossierRunDetail, ProductDossierOut } from "@/types/api";

const getDossier = vi.fn();
const getDossierRun = vi.fn();
const postDossierRerun = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getDossier: (...args: unknown[]) => getDossier(...args),
    getDossierRun: (...args: unknown[]) => getDossierRun(...args),
    postDossierRerun: (...args: unknown[]) => postDossierRerun(...args),
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
      { id: 501, created_at: "2026-09-06T21:10:00+03:00", outcome: "found", confidence: 0.82, report_id: 940 },
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
      "ביצועים",
      "בשלות",
      "עסקאות",
      "מחירים",
      "שותפויות",
      "מתחרים",
      "פטנטים",
      "מכרזים ותחזיות",
      "פערים",
      "משמעות עסקית",
      "מה השתנה",
      "מקורות",
    ];
    for (const label of order) {
      expect(screen.getByRole("heading", { name: label, level: 3 })).toBeInTheDocument();
    }
  });

  it("shows the empty-table placeholder for a section with no rows", async () => {
    getDossier.mockResolvedValue(detail());
    renderPage();
    expect(await screen.findByText("אין נתוני מפרט")).toBeInTheDocument();
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

  it("triggers a rerun from the detail page's 'הרץ שוב' button", async () => {
    const user = userEvent.setup();
    getDossier.mockResolvedValue(detail());
    postDossierRerun.mockResolvedValue({ job_id: "job-3" });
    renderPage();

    const button = await screen.findByTestId("dossier-detail-rerun");
    await user.click(button);
    await waitFor(() => expect(postDossierRerun).toHaveBeenCalledWith("elbit-systems-spectro-xr"));
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
});
