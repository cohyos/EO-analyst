import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { BdTerritoryOption, ReportSummary } from "@/types/api";

const getBdTerritories = vi.fn();
const getBdReports = vi.fn();
const postBdReport = vi.fn();
const getReport = vi.fn();
const getReportFileUrl = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getBdTerritories: (...args: unknown[]) => getBdTerritories(...args),
    getBdReports: (...args: unknown[]) => getBdReports(...args),
    postBdReport: (...args: unknown[]) => postBdReport(...args),
    getReport: (...args: unknown[]) => getReport(...args),
    getReportFileUrl: (...args: unknown[]) => getReportFileUrl(...args),
  },
}));

import { BdPage } from "./BdPage";

function territoryOption(over: Partial<BdTerritoryOption> = {}): BdTerritoryOption {
  return { territory: "US", items: 5, tenders: 2, forecasts: 1, configured: true, ...over };
}

function report(over: Partial<ReportSummary> = {}): ReportSummary {
  return {
    id: 1,
    kind: "bd_territory",
    period_start: "2026-06-09",
    period_end: "2026-09-06",
    path_docx: null,
    path_md: null,
    path_html: null,
    qa_passed: true,
    created_at: "2026-09-06T19:03:13+03:00",
    headline_count: 5,
    territory: "US",
    title_he: 'דוח פיתוח עסקי — ארה"ב',
    subject_he: 'ארה"ב',
    built_at: "2026-09-06T19:03:13+03:00",
    preview_he: "תקציר",
    source_count: 5,
    qa_issues: 0,
    group_key: "bd_territory:US",
    is_latest: true,
    ...over,
  };
}

function renderPage(initialEntries: string[] = ["/bd"]) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={initialEntries}>
        <Routes>
          <Route path="/bd" element={<BdPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getBdTerritories.mockReset();
  getBdReports.mockReset();
  postBdReport.mockReset();
  getReport.mockReset();
  getReportFileUrl.mockReset();
  getReportFileUrl.mockReturnValue("#mock");
  getBdTerritories.mockResolvedValue([territoryOption({ territory: "US" }), territoryOption({ territory: "DE" })]);
});

describe("BdPage territory selector (W15)", () => {
  it("fetches every territory's reports by default, with no territory picked", async () => {
    getBdReports.mockResolvedValue([report({ id: 1, territory: "US" }), report({ id: 2, territory: "DE" })]);
    renderPage();

    await waitFor(() => expect(getBdReports).toHaveBeenCalledWith(""));
    expect(await screen.findAllByRole("button", { name: /QA/ })).toHaveLength(2);
  });

  it("offers a selectable, non-disabled 'all territories' option alongside real territories", async () => {
    getBdReports.mockResolvedValue([]);
    renderPage();

    const select = await screen.findByLabelText("טריטוריה");
    const options = Array.from(select.querySelectorAll("option"));
    const allOption = options[0] as HTMLOptionElement;
    expect(allOption.value).toBe("");
    expect(allOption.disabled).toBe(false);
  });

  it("filters to one territory's reports after picking it, and shows the create-report controls", async () => {
    const user = userEvent.setup();
    getBdReports.mockResolvedValue([report({ id: 1, territory: "US" })]);
    renderPage();

    // Wait for the territories query to resolve (the select starts disabled with only a loading
    // placeholder option) before interacting with it.
    await screen.findByRole("option", { name: /US/ });
    const select = screen.getByLabelText("טריטוריה");
    await user.selectOptions(select, "US");

    await waitFor(() => expect(getBdReports).toHaveBeenLastCalledWith("US"));
    expect(await screen.findByRole("button", { name: /צור דוח/ })).toBeInTheDocument();
  });

  it("selecting the 'all' option again after picking a territory restores the unfiltered list", async () => {
    const user = userEvent.setup();
    getBdReports.mockResolvedValue([report({ id: 1, territory: "US" }), report({ id: 2, territory: "DE" })]);
    renderPage();

    await screen.findByRole("option", { name: /US/ });
    const select = screen.getByLabelText("טריטוריה");
    await user.selectOptions(select, "US");
    await waitFor(() => expect(getBdReports).toHaveBeenLastCalledWith("US"));

    // W15: the clear/"הכל" control -- selecting the first (all-territories) option again.
    await user.selectOptions(select, "");
    await waitFor(() => expect(getBdReports).toHaveBeenLastCalledWith(""));

    // Back in "all" mode: the create-report controls (which require one specific territory) hide.
    expect(screen.queryByRole("button", { name: /צור דוח/ })).not.toBeInTheDocument();
  });

  it("shows a territory flag/badge on each row while browsing all territories", async () => {
    getBdReports.mockResolvedValue([report({ id: 1, territory: "US" })]);
    renderPage();

    await screen.findByRole("button", { name: /QA/ });
    // The row is annotated with its territory (an aria-hidden flag glyph) when no single
    // territory is selected -- otherwise there would be no way to tell rows apart.
    const row = screen.getByRole("button", { name: /QA/ });
    expect(row.querySelector('[aria-hidden="true"]')).not.toBeNull();
  });
});

describe("BdPage empty/error states", () => {
  it("shows a neutral 'pick a report' prompt in the all-territories view with no selection", async () => {
    getBdReports.mockResolvedValue([report()]);
    renderPage();
    expect(await screen.findByText("בחר דוח מהרשימה או צור דוח חדש")).toBeInTheDocument();
  });

  it("shows the empty-territories message when nothing exists anywhere", async () => {
    getBdTerritories.mockResolvedValue([]);
    getBdReports.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText("לא נמצאו טריטוריות פעילות")).toBeInTheDocument();
  });
});
