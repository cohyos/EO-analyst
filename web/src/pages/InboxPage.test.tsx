import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { SecurityReviewCard } from "@/types/api";

const getClarifications = vi.fn();
const getLatestSurvey = vi.fn();
const getLessons = vi.fn();
const getSecurityReviews = vi.fn();
const postSecurityReviewApprove = vi.fn();
const postSecurityReviewDismiss = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getClarifications: (...args: unknown[]) => getClarifications(...args),
    getLatestSurvey: (...args: unknown[]) => getLatestSurvey(...args),
    getLessons: (...args: unknown[]) => getLessons(...args),
    getSecurityReviews: (...args: unknown[]) => getSecurityReviews(...args),
    postSecurityReviewApprove: (...args: unknown[]) => postSecurityReviewApprove(...args),
    postSecurityReviewDismiss: (...args: unknown[]) => postSecurityReviewDismiss(...args),
  },
}));

import { InboxPage } from "./InboxPage";

function review(overrides: Partial<SecurityReviewCard> = {}): SecurityReviewCard {
  return {
    job_id: 113,
    item_id: 42,
    question: "אימות והרחבה: מפעל פולקסווגן",
    item_title: "מפעל פולקסווגן ↔ רפאל",
    reason_he: "חשד להזרקת פרומפט במקור",
    snippet: "התעלם מההוראות הקודמות...",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

function renderInbox() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/inbox"]}>
        <Routes>
          <Route path="/inbox" element={<InboxPage />} />
          <Route path="/investigations/:jobId" element={<div>investigation page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getClarifications.mockReset();
  getLatestSurvey.mockReset();
  getLessons.mockReset();
  getSecurityReviews.mockReset();
  postSecurityReviewApprove.mockReset();
  postSecurityReviewDismiss.mockReset();
  getClarifications.mockResolvedValue([]);
  getLatestSurvey.mockResolvedValue(null);
  getLessons.mockResolvedValue([]);
  getSecurityReviews.mockResolvedValue([]);
});

// W10 (docs/REVIEW_2026-09-06_evening.md round 4): the inbox lists every pending security review
// alongside the existing clarifications/survey/lessons sections.
describe("InboxPage security reviews section (W10)", () => {
  it("shows an explicit empty state when there are no pending reviews", async () => {
    renderInbox();
    expect(await screen.findByText("אין בדיקות אבטחה ממתינות")).toBeInTheDocument();
  });

  it("lists a pending review with its reason/snippet and a link to the investigation", async () => {
    getSecurityReviews.mockResolvedValue([review()]);
    renderInbox();
    const banner = await screen.findByTestId("security-review-banner");
    expect(banner).toHaveTextContent("חשד להזרקת פרומפט במקור");
    expect(screen.getByRole("link", { name: "פתח חקירה" })).toHaveAttribute(
      "href",
      "/investigations/113",
    );
  });

  it("'אשר והמשך' on a row calls approve with that row's job id", async () => {
    getSecurityReviews.mockResolvedValue([review()]);
    postSecurityReviewApprove.mockResolvedValue({ job_id: "200" });
    renderInbox();
    await screen.findByTestId("security-review-banner");
    fireEvent.click(screen.getByText("אשר והמשך"));
    await waitFor(() => expect(postSecurityReviewApprove).toHaveBeenCalledWith("113"));
  });

  it("'דחה' on a row calls dismiss with that row's job id", async () => {
    getSecurityReviews.mockResolvedValue([review()]);
    postSecurityReviewDismiss.mockResolvedValue({ ok: true });
    renderInbox();
    await screen.findByTestId("security-review-banner");
    fireEvent.click(screen.getByText("דחה"));
    await waitFor(() => expect(postSecurityReviewDismiss).toHaveBeenCalledWith("113"));
  });
});
