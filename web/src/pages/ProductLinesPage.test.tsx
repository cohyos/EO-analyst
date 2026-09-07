import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { ProductLine } from "@/types/api";

const getProductLines = vi.fn();
const postProductLineReport = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getProductLines: (...args: unknown[]) => getProductLines(...args),
    postProductLineReport: (...args: unknown[]) => postProductLineReport(...args),
  },
}));

import { ProductLinesPage } from "./ProductLinesPage";

function productLine(over: Partial<ProductLine> = {}): ProductLine {
  return {
    id: "targeting_pods",
    name_he: "פודי ציון מטרות / תקיפה",
    name_en: "Targeting Pods",
    subdomains: ["airborne_pods/targeting_pods"],
    exemplar_systems: ["Litening"],
    competitors: ["Lockheed Martin"],
    stats: {
      items_7d: 3,
      items_30d: 12,
      events_30d: 5,
      open_tenders: 2,
      forecasts: 1,
      patents_90d: 4,
      active_competitors: 3,
    },
    latest_report: {
      id: 960,
      created_at: "2026-09-06T07:00:00+03:00",
      qa_passed: true,
      path_html: "/reports/pl_targeting_pods_960.html",
    },
    ...over,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/product-lines"]}>
        <Routes>
          <Route path="/product-lines" element={<ProductLinesPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getProductLines.mockReset();
  postProductLineReport.mockReset();
});

describe("ProductLinesPage (PL-ui)", () => {
  it("renders one card per product line with its KPI values", async () => {
    getProductLines.mockResolvedValue([
      productLine({ id: "targeting_pods" }),
      productLine({ id: "mws_eo", name_he: "MWS" }),
    ]);
    renderPage();

    expect(await screen.findByTestId("product-line-card-targeting_pods")).toBeInTheDocument();
    expect(screen.getByTestId("product-line-card-mws_eo")).toBeInTheDocument();
    // KPI grid renders the raw stat values for the first card.
    const grid = screen.getAllByTestId("product-line-stats-grid")[0];
    expect(grid).toHaveTextContent("3");
    expect(grid).toHaveTextContent("12");
  });

  it("shows the latest report's QA status and an 'open report' link", async () => {
    getProductLines.mockResolvedValue([productLine()]);
    renderPage();

    expect(await screen.findByText("QA ✓")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "פתח דוח" });
    expect(link).toHaveAttribute("href", "/reports?id=960");
  });

  it("shows an empty state when no product lines are returned", async () => {
    getProductLines.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText("לא נמצאו קווי מוצר")).toBeInTheDocument();
  });

  it("shows an error state with a retry control on failure", async () => {
    getProductLines.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "נסה שוב" })).toBeInTheDocument();
  });

  it("queues a report build and shows the queued status when 'צור דוח' is clicked", async () => {
    const user = userEvent.setup();
    getProductLines.mockResolvedValue([productLine({ latest_report: null })]);
    postProductLineReport.mockResolvedValue({ job_id: "mock-pl-job-1" });
    renderPage();

    const button = await screen.findByTestId("product-line-create-report-targeting_pods");
    await user.click(button);

    await waitFor(() => expect(postProductLineReport).toHaveBeenCalledWith("targeting_pods"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "הדוח בבנייה ברקע — יופיע ברשימה כשיושלם",
    );
  });

  it("shows 'no report yet' when a product line has never had a report built", async () => {
    getProductLines.mockResolvedValue([productLine({ latest_report: null })]);
    renderPage();
    expect(await screen.findByText("אין עדיין דוח לקו מוצר זה")).toBeInTheDocument();
  });

  it("links each card's title to its detail route", async () => {
    getProductLines.mockResolvedValue([productLine({ id: "lorop_pods", name_he: "LOROP" })]);
    renderPage();

    const link = await screen.findByRole("link", { name: "LOROP" });
    expect(link).toHaveAttribute("href", "/product-lines/lorop_pods");
  });
});
