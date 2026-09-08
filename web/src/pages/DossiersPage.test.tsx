import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { DossierSummary } from "@/types/api";

const getDossiers = vi.fn();
const postDossier = vi.fn();
const postDossierRerun = vi.fn();
const navigateSpy = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigateSpy };
});

vi.mock("@/api", () => ({
  api: {
    getDossiers: (...args: unknown[]) => getDossiers(...args),
    postDossier: (...args: unknown[]) => postDossier(...args),
    postDossierRerun: (...args: unknown[]) => postDossierRerun(...args),
  },
}));

import { DossiersPage } from "./DossiersPage";

function dossier(over: Partial<DossierSummary> = {}): DossierSummary {
  return {
    product_key: "elbit-systems-spectro-xr",
    product_name: "SPECTRO XR",
    vendor: "Elbit Systems",
    latest: {
      id: 501,
      created_at: "2026-09-06T21:10:00+03:00",
      outcome: "found",
      confidence: 0.82,
      report_id: 940,
    },
    count: 3,
    ...over,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/dossiers"]}>
        <Routes>
          <Route path="/dossiers" element={<DossiersPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getDossiers.mockReset();
  postDossier.mockReset();
  postDossierRerun.mockReset();
  navigateSpy.mockReset();
});

describe("DossiersPage (PD-ui)", () => {
  it("renders one card per product with outcome badge and deal count", async () => {
    getDossiers.mockResolvedValue([dossier(), dossier({ product_key: "safran-stratos", product_name: "STRATOS", latest: { id: 502, created_at: "2026-09-05T09:40:00+03:00", outcome: "partial", confidence: 0.28, report_id: null }, count: 0 })]);
    renderPage();

    expect(await screen.findByTestId("dossier-card-elbit-systems-spectro-xr")).toBeInTheDocument();
    expect(screen.getByTestId("dossier-card-safran-stratos")).toBeInTheDocument();
    expect(screen.getByText("נמצא")).toBeInTheDocument();
    expect(screen.getByText("חלקי")).toBeInTheDocument();
    expect(screen.getByText("3 עסקאות")).toBeInTheDocument();
  });

  it("shows an empty state when no dossiers exist", async () => {
    getDossiers.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText("לא נמצאו סקירות מוצר")).toBeInTheDocument();
  });

  it("shows an error state with a retry control on failure", async () => {
    getDossiers.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "נסה שוב" })).toBeInTheDocument();
  });

  it("links each card's title to its detail route", async () => {
    getDossiers.mockResolvedValue([dossier()]);
    renderPage();
    const link = await screen.findByRole("link", { name: "SPECTRO XR" });
    expect(link).toHaveAttribute("href", "/dossiers/elbit-systems-spectro-xr");
  });

  it("opens the new-dossier form, validates the required product name, and submits", async () => {
    const user = userEvent.setup();
    getDossiers.mockResolvedValue([]);
    postDossier.mockResolvedValue({ job_id: "job-1", product_key: "acme-widget" });
    renderPage();

    await screen.findByText("לא נמצאו סקירות מוצר");
    await user.click(screen.getByTestId("dossier-new-button"));
    expect(await screen.findByRole("heading", { name: "סקירת מוצר חדשה" })).toBeInTheDocument();

    // Submitting with an empty required product name shows the inline validation error and
    // never calls the API.
    await user.click(screen.getByTestId("dossier-form-submit"));
    expect(await screen.findByRole("alert")).toHaveTextContent("שם המוצר הוא שדה חובה");
    expect(postDossier).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText(/שם המוצר/), "Widget");
    await user.click(screen.getByTestId("dossier-form-submit"));

    await waitFor(() =>
      expect(postDossier).toHaveBeenCalledWith(expect.objectContaining({ product_name: "Widget" })),
    );
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith("/dossiers/acme-widget"));
  });

  it("adds and removes alias chips in the new-dossier form", async () => {
    const user = userEvent.setup();
    getDossiers.mockResolvedValue([]);
    renderPage();

    await user.click(await screen.findByTestId("dossier-new-button"));
    const aliasInput = screen.getByLabelText("כינויים נוספים");
    await user.type(aliasInput, "Spectro,");
    expect(screen.getByText("Spectro")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "הסר כינוי Spectro" }));
    expect(screen.queryByText("Spectro")).not.toBeInTheDocument();
  });

  it("queues a rerun and shows the queued status", async () => {
    const user = userEvent.setup();
    getDossiers.mockResolvedValue([dossier()]);
    postDossierRerun.mockResolvedValue({ job_id: "job-2" });
    renderPage();

    const button = await screen.findByTestId("dossier-rerun-elbit-systems-spectro-xr");
    await user.click(button);

    await waitFor(() => expect(postDossierRerun).toHaveBeenCalledWith("elbit-systems-spectro-xr"));
    expect(await screen.findByRole("status")).toHaveTextContent("הדוח בבנייה ברקע — יופיע ברשימה כשיושלם");
  });
});
