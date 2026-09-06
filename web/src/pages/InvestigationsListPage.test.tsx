import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { InvestigationSummary } from "@/types/api";

const getInvestigations = vi.fn();
const postInvestigationNew = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getInvestigations: (...args: unknown[]) => getInvestigations(...args),
    postInvestigationNew: (...args: unknown[]) => postInvestigationNew(...args),
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
  getInvestigations.mockResolvedValue([makeSummary("1")]);
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
