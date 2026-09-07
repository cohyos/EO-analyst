import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { InvestigationSummary } from "@/types/api";

const getInvestigations = vi.fn();
const postInvestigationNew = vi.fn();
// R10-links: the list page fetches each row's own `provenance` (trigger item + last report) via
// `api.getInvestigation` -- mocked here the same way `InvestigationDetailPage.test.tsx` does.
const getInvestigation = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getInvestigations: (...args: unknown[]) => getInvestigations(...args),
    postInvestigationNew: (...args: unknown[]) => postInvestigationNew(...args),
    getInvestigation: (...args: unknown[]) => getInvestigation(...args),
  },
}));

import { InvestigationsListPage } from "./InvestigationsListPage";

function makeSummary(jobId: string): InvestigationSummary {
  return {
    job_id: jobId,
    item_id: null,
    question: "שאלת בדיקה קיימת",
    item_title: null,
    error: null,
    state: "done",
    rounds: 2,
    queries: 4,
    pages_read: 6,
    outcome: "found",
    started_at: "2026-09-04T09:00:00+03:00",
    finished_at: "2026-09-04T09:10:00+03:00",
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/investigations"]}>
        <Routes>
          <Route path="/investigations" element={<InvestigationsListPage />} />
          <Route
            path="/investigations/:jobId"
            element={<div data-testid="investigation-detail-stub">detail page</div>}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getInvestigations.mockReset();
  postInvestigationNew.mockReset();
  getInvestigation.mockReset();
  getInvestigations.mockResolvedValue([makeSummary("1")]);
  // Default: no provenance (older-backend shape) -- the "פריט מקור"/"דוח אחרון" columns fall
  // back to the "—" placeholder unless a test overrides this.
  getInvestigation.mockResolvedValue({ ...makeSummary("1"), log: [], answer: null, provenance: null });
});

// Round-5 P7 (docs/REPORT_TEMPLATE_BENCHMARK.md DS3): `blocked` must render as a distinct amber
// chip in the list, not the grey `not_found` tone.
describe("InvestigationsListPage blocked outcome chip (Round-5 P7)", () => {
  it("shows a distinct 'נחסם' chip for a blocked investigation, not 'לא נמצא'", async () => {
    getInvestigations.mockResolvedValue([{ ...makeSummary("1"), outcome: "blocked" }]);
    renderPage();
    expect(await screen.findByText("נחסם")).toBeInTheDocument();
    expect(screen.queryByText("לא נמצא")).not.toBeInTheDocument();
  });
});

describe("InvestigationsListPage new-investigation flow (Q5-6)", () => {
  it("toasts success and navigates to the new investigation once it starts", async () => {
    postInvestigationNew.mockResolvedValue({ job_id: "42" });
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "חקירה חדשה" }));
    const textarea = screen.getByLabelText(/הקלד שאלת מחקר/);
    fireEvent.change(textarea, { target: { value: "מה היקף החוזה של אלביט בצרפת?" } });
    fireEvent.click(screen.getByRole("button", { name: "התחל חקירה" }));

    await waitFor(() => expect(postInvestigationNew).toHaveBeenCalledWith({ question: "מה היקף החוזה של אלביט בצרפת?" }));
    // The toast appears immediately -- before the (deliberately delayed) navigate away.
    expect(await screen.findByText(/חקירה חדשה נפתחה/)).toBeInTheDocument();
    expect(screen.queryByTestId("investigation-detail-stub")).not.toBeInTheDocument();
    expect(await screen.findByTestId("investigation-detail-stub", {}, { timeout: 2000 })).toBeInTheDocument();
  });

  it("toasts an error and stays on the page if starting the investigation fails", async () => {
    postInvestigationNew.mockRejectedValue(new Error("boom"));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "חקירה חדשה" }));
    const textarea = screen.getByLabelText(/הקלד שאלת מחקר/);
    fireEvent.change(textarea, { target: { value: "מה היקף החוזה של אלביט בצרפת?" } });
    fireEvent.click(screen.getByRole("button", { name: "התחל חקירה" }));

    expect(await screen.findByText(/נכשל/)).toBeInTheDocument();
    expect(screen.queryByTestId("investigation-detail-stub")).not.toBeInTheDocument();
  });
});

// R10-links: "פריט מקור" (trigger item) and "דוח אחרון" (last report) columns.
describe("InvestigationsListPage trigger item / last report columns (R10-links)", () => {
  it("shows a '—' placeholder in both new columns while there is no provenance", async () => {
    renderPage();
    await screen.findByText("שאלת בדיקה קיימת");
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(2);
  });

  it("links the trigger item column to /items/:id once its provenance loads", async () => {
    getInvestigation.mockResolvedValue({
      ...makeSummary("1"),
      log: [],
      answer: null,
      provenance: {
        job: { job_id: "1", state: "done", question: null, started_at: null, finished_at: null },
        trigger_item: { id: 42, title: "כותרת הפריט", url: null, source_name: null, published_at: null },
        lineage: [],
        reports: [],
      },
    });
    renderPage();
    const link = await screen.findByRole("link", { name: /כותרת הפריט/ });
    expect(link).toHaveAttribute("href", "/items/42");
  });

  it("links the last-report column to /reports?id= (reports[0], the newest)", async () => {
    getInvestigation.mockResolvedValue({
      ...makeSummary("1"),
      log: [],
      answer: null,
      provenance: {
        job: { job_id: "1", state: "done", question: null, started_at: null, finished_at: null },
        trigger_item: null,
        lineage: [],
        reports: [
          { id: 9, kind: "daily", period_end: "2026-09-05", territory: null, title_he: "דוח יומי — 05.09", path_html: null },
        ],
      },
    });
    renderPage();
    const link = await screen.findByRole("link", { name: "דוח יומי — 05.09" });
    expect(link).toHaveAttribute("href", "/reports?id=9");
  });
});
