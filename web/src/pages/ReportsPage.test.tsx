import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ReportDetail, ReportSummary } from "@/types/api";

const getReports = vi.fn();
const getReport = vi.fn();
const getReportFileUrl = vi.fn();
const getReportCitations = vi.fn();
// R10-links: "חקירות בדוח" side list.
const getReportInvestigations = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getReports: (...args: unknown[]) => getReports(...args),
    getReport: (...args: unknown[]) => getReport(...args),
    getReportFileUrl: (...args: unknown[]) => getReportFileUrl(...args),
    getReportCitations: (...args: unknown[]) => getReportCitations(...args),
    getReportInvestigations: (...args: unknown[]) => getReportInvestigations(...args),
  },
}));

import { ReportsPage } from "./ReportsPage";

// W14 (docs/REVIEW_2026-09-06_evening.md, user finding 2026-09-06 19:10): report list rows now
// carry a descriptive title_he/preview_he/chips + version grouping (group_key/is_latest) instead
// of the old flat "<kind> — <date>" rows.
function report(overrides: Partial<ReportSummary> = {}): ReportSummary {
  return {
    id: 1,
    kind: "patent_survey",
    period_start: "",
    period_end: "2026-09-06",
    path_docx: null,
    path_md: null,
    path_html: null,
    qa_passed: true,
    created_at: "2026-09-06T19:03:13+03:00",
    headline_count: 17,
    territory: null,
    title_he: "סקר פטנטים: FPA עם פיקסל דיגיטלי (DROIC) — 06.09 19:03",
    subject_he: "FPA עם פיקסל דיגיטלי (DROIC)",
    built_at: "2026-09-06T19:03:13+03:00",
    preview_he: "ל-17 מתוך 17 הפטנטים אין נתוני מקצה. לא ניתן להסיק בלעדיות או נתח שוק.",
    source_count: 17,
    qa_issues: 0,
    group_key: "patent_survey:fpa עם פיקסל דיגיטלי (droic)",
    is_latest: true,
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/reports"]}>
        <Routes>
          <Route path="/reports" element={<ReportsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getReports.mockReset();
  getReport.mockReset();
  getReportFileUrl.mockReset();
  getReportCitations.mockReset();
  getReportInvestigations.mockReset();
  getReportFileUrl.mockReturnValue("#mock");
  getReportCitations.mockResolvedValue({ report_id: 1, citations: {} });
  getReportInvestigations.mockResolvedValue([]);
});

describe("ReportsPage list rows (W14)", () => {
  it("shows the descriptive title, preview line, and chips for a report", async () => {
    getReports.mockResolvedValue([report()]);
    renderPage();

    expect(await screen.findByText("סקר פטנטים: FPA עם פיקסל דיגיטלי (DROIC) — 06.09 19:03")).toBeInTheDocument();
    // appears twice: the visible one-line preview and the hover/focus tooltip carrying the same text.
    expect(
      screen.getAllByText("ל-17 מתוך 17 הפטנטים אין נתוני מקצה. לא ניתן להסיק בלעדיות או נתח שוק."),
    ).toHaveLength(2);
    expect(screen.getByText("QA ✓")).toBeInTheDocument();
    expect(screen.getByText("17 מקורות")).toBeInTheDocument();
    expect(screen.getByText("17 כותרות")).toBeInTheDocument();
  });

  it("marks a failed QA report distinctly from a passed one", async () => {
    getReports.mockResolvedValue([report({ id: 2, qa_passed: false, title_he: "דוח יומי — 06.09" })]);
    renderPage();

    expect(await screen.findByText("QA ✗")).toBeInTheDocument();
  });

  it("gives each row an accessible name combining title, preview, and QA status", async () => {
    getReports.mockResolvedValue([report()]);
    renderPage();

    const row = await screen.findByRole("button", {
      name: /סקר פטנטים: FPA עם פיקסל דיגיטלי \(DROIC\).*QA עבר/,
    });
    expect(row).toBeInTheDocument();
  });
});

describe("ReportsPage version grouping (W14 point 3)", () => {
  it("shows only the latest row per group by default, with an expander for older versions", async () => {
    getReports.mockResolvedValue([
      report({
        id: 50,
        title_he: "סקר פטנטים: Anduril — 06.09 19:04",
        group_key: "patent_survey:anduril",
        is_latest: true,
      }),
      report({
        id: 45,
        title_he: "סקר פטנטים: Anduril — 06.09 17:33",
        group_key: "patent_survey:anduril",
        is_latest: false,
      }),
    ]);
    renderPage();

    expect(await screen.findByText("סקר פטנטים: Anduril — 06.09 19:04")).toBeInTheDocument();
    expect(screen.queryByText("סקר פטנטים: Anduril — 06.09 17:33")).not.toBeInTheDocument();

    const expander = screen.getByRole("button", { name: /גרסאות קודמות \(1\)/ });
    expect(expander).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(expander);
    expect(expander).toHaveAttribute("aria-expanded", "true");
    expect(await screen.findByText("סקר פטנטים: Anduril — 06.09 17:33")).toBeInTheDocument();

    fireEvent.click(expander);
    await waitFor(() =>
      expect(screen.queryByText("סקר פטנטים: Anduril — 06.09 17:33")).not.toBeInTheDocument(),
    );
  });

  it("renders every distinct group as its own row with no expander when there are no older versions", async () => {
    getReports.mockResolvedValue([
      report({ id: 1, title_he: "דוח יומי — 06.09", group_key: "daily:2026-09-06_2026-09-06" }),
      report({
        id: 2,
        title_he: 'דוח פיתוח עסקי — ארה"ב — 06.09 17:28',
        group_key: "bd_territory:US",
      }),
    ]);
    renderPage();

    expect(await screen.findByText("דוח יומי — 06.09")).toBeInTheDocument();
    expect(screen.getByText('דוח פיתוח עסקי — ארה"ב — 06.09 17:28')).toBeInTheDocument();
    expect(screen.queryByText(/גרסאות קודמות/)).not.toBeInTheDocument();
  });

  it("does not show a version list for a report kind lacking a subject (grouped by period)", async () => {
    getReports.mockResolvedValue([
      report({
        id: 1,
        kind: "daily",
        subject_he: null,
        title_he: "דוח יומי — 06.09",
        group_key: "daily:2026-09-05_2026-09-06",
      }),
      report({
        id: 2,
        kind: "daily",
        subject_he: null,
        title_he: "דוח יומי — 05.09",
        group_key: "daily:2026-09-04_2026-09-05",
      }),
    ]);
    renderPage();

    expect(await screen.findByText("דוח יומי — 06.09")).toBeInTheDocument();
    expect(screen.getByText("דוח יומי — 05.09")).toBeInTheDocument();
    expect(screen.queryByText(/גרסאות קודמות/)).not.toBeInTheDocument();
  });
});

describe("ReportsPage detail selection", () => {
  it("selecting a row opens the report and shows its title in the header", async () => {
    const summary = report();
    getReports.mockResolvedValue([summary]);
    const detail: ReportDetail = {
      ...summary,
      html: "<section><h2>תקציר מנהלים</h2><p>תוכן</p></section>",
      open_points: [],
      items_included: [1, 2, 3],
    };
    getReport.mockResolvedValue(detail);
    renderPage();

    const row = await screen.findByRole("button", {
      name: /סקר פטנטים: FPA עם פיקסל דיגיטלי \(DROIC\)/,
    });
    fireEvent.click(row);

    await waitFor(() => expect(getReport).toHaveBeenCalledWith(1));
    expect(await screen.findByText(/סקר פטנטים: FPA עם פיקסל דיגיטלי \(DROIC\)/, { selector: "h2" })).toBeInTheDocument();
  });
});

describe("ReportsPage 'חקירות בדוח' side list (R10-links)", () => {
  it("shows the investigations the report cites, each linking to its own detail page", async () => {
    const summary = report();
    getReports.mockResolvedValue([summary]);
    getReport.mockResolvedValue({
      ...summary,
      html: "<section><h2>תקציר מנהלים</h2><p>תוכן</p></section>",
      open_points: [],
      items_included: [1, 2, 3],
    });
    getReportInvestigations.mockResolvedValue([
      {
        job_id: "17",
        item_id: 1,
        trigger_title: "פריט מקור לדוגמה",
        question: "מה סטטוס ההזמנה?",
        outcome: "found",
        confidence: 0.9,
        rerun_of_job_id: null,
      },
    ]);
    renderPage();

    const row = await screen.findByRole("button", {
      name: /סקר פטנטים: FPA עם פיקסל דיגיטלי \(DROIC\)/,
    });
    fireEvent.click(row);

    const link = await screen.findByRole("link", { name: /מה סטטוס ההזמנה/ });
    expect(link).toHaveAttribute("href", "/investigations/17");
    expect(screen.getByText("חקירות בדוח (1)")).toBeInTheDocument();
  });

  it("shows a link to the trigger item alongside the investigation", async () => {
    const summary = report();
    getReports.mockResolvedValue([summary]);
    getReport.mockResolvedValue({
      ...summary,
      html: "<section><h2>תקציר מנהלים</h2><p>תוכן</p></section>",
      open_points: [],
      items_included: [1],
    });
    getReportInvestigations.mockResolvedValue([
      {
        job_id: "17",
        item_id: 1,
        trigger_title: "פריט מקור לדוגמה",
        question: "מה סטטוס ההזמנה?",
        outcome: "found",
        confidence: 0.9,
        rerun_of_job_id: null,
      },
    ]);
    renderPage();

    fireEvent.click(
      await screen.findByRole("button", { name: /סקר פטנטים: FPA עם פיקסל דיגיטלי \(DROIC\)/ }),
    );
    const itemLink = await screen.findByRole("link", { name: "פריט מקור" });
    expect(itemLink).toHaveAttribute("href", "/items/1");
  });

  it("does not render the side list when the report cites no investigations", async () => {
    const summary = report();
    getReports.mockResolvedValue([summary]);
    getReport.mockResolvedValue({
      ...summary,
      html: "<section><h2>תקציר מנהלים</h2><p>תוכן</p></section>",
      open_points: [],
      items_included: [1, 2, 3],
    });
    getReportInvestigations.mockResolvedValue([]);
    renderPage();

    fireEvent.click(
      await screen.findByRole("button", { name: /סקר פטנטים: FPA עם פיקסל דיגיטלי \(DROIC\)/ }),
    );
    await waitFor(() => expect(getReportInvestigations).toHaveBeenCalledWith(1));
    expect(screen.queryByText(/חקירות בדוח/)).not.toBeInTheDocument();
  });
});

describe("ReportsPage empty/error states", () => {
  it("shows an empty state when there are no reports", async () => {
    getReports.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText("אין דוחות")).toBeInTheDocument();
  });

  it("shows a prompt to pick a report before any selection", async () => {
    getReports.mockResolvedValue([report()]);
    renderPage();
    expect(await screen.findByText("בחר דוח מהרשימה")).toBeInTheDocument();
  });
});
