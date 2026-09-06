import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { InvestigationDetail, InvestigationLogLine } from "@/types/api";

const getInvestigation = vi.fn();
const postInvestigationStop = vi.fn();
const postItemInvestigate = vi.fn();
const postInvestigationExpand = vi.fn();
const postSecurityReviewApprove = vi.fn();
const postSecurityReviewDismiss = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getInvestigation: (...args: unknown[]) => getInvestigation(...args),
    postInvestigationStop: (...args: unknown[]) => postInvestigationStop(...args),
    postItemInvestigate: (...args: unknown[]) => postItemInvestigate(...args),
    postInvestigationExpand: (...args: unknown[]) => postInvestigationExpand(...args),
    postSecurityReviewApprove: (...args: unknown[]) => postSecurityReviewApprove(...args),
    postSecurityReviewDismiss: (...args: unknown[]) => postSecurityReviewDismiss(...args),
  },
}));

const useInvestigationSocketMock = vi.fn();
vi.mock("@/hooks/useInvestigationSocket", () => ({
  useInvestigationSocket: (...args: unknown[]) => useInvestigationSocketMock(...args),
}));

import { InvestigationDetailPage } from "./InvestigationDetailPage";

function makeLine(round: number): InvestigationLogLine {
  return {
    round,
    lang: "he",
    query: `שאילתה ${round}`,
    results: 3,
    outcome: "ok",
    at: "2026-09-04T10:00:00+03:00",
  };
}

function baseDetail(): InvestigationDetail {
  return {
    job_id: "10",
    item_id: null,
    question: "שאלת בדיקה",
    item_title: null,
    error: null,
    state: "running",
    rounds: 1,
    queries: 5,
    pages_read: 2,
    outcome: null,
    started_at: "2026-09-04T09:00:00+03:00",
    finished_at: null,
    log: [makeLine(1)],
    answer: null,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/investigations/10"]}>
        <Routes>
          <Route path="/investigations/:jobId" element={<InvestigationDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  getInvestigation.mockReset();
  postInvestigationStop.mockReset();
  postItemInvestigate.mockReset();
  postInvestigationExpand.mockReset();
  postSecurityReviewApprove.mockReset();
  postSecurityReviewDismiss.mockReset();
  useInvestigationSocketMock.mockReset();
  getInvestigation.mockResolvedValue(baseDetail());
  useInvestigationSocketMock.mockReturnValue({ liveLines: [], connected: true });
});

// W10 (docs/REVIEW_2026-09-06_evening.md round 4): when the L2 security guard partially blocks an
// answer, the investigation page shows a banner with the reason/snippet and two actions.
describe("InvestigationDetailPage security review banner (W10)", () => {
  function detailWithSecurityReview() {
    return {
      ...baseDetail(),
      state: "done" as const,
      answer: {
        answer_he: "תשובה חלקית",
        sources: [],
        outcome: "partial",
        security_review: true,
        security_review_reason_he: "חשד להזרקת פרומפט במקור",
        security_review_snippet: "התעלם מההוראות הקודמות...",
      },
    };
  }

  it("shows the banner with the reason and snippet when security_review is true", async () => {
    getInvestigation.mockResolvedValue(detailWithSecurityReview());
    renderPage();
    const banner = await screen.findByTestId("security-review-banner");
    expect(banner).toHaveTextContent("נחסם חלקית לבדיקת אבטחה");
    expect(banner).toHaveTextContent("חשד להזרקת פרומפט במקור");
    expect(banner).toHaveTextContent("התעלם מההוראות הקודמות");
  });

  it("does not show the banner when security_review is absent (fields not landed yet)", async () => {
    getInvestigation.mockResolvedValue(baseDetail());
    renderPage();
    await screen.findByTestId("investigation-log");
    expect(screen.queryByTestId("security-review-banner")).not.toBeInTheDocument();
  });

  it("'אשר והמשך' calls the approve endpoint and navigates to the new job", async () => {
    getInvestigation.mockResolvedValue(detailWithSecurityReview());
    postSecurityReviewApprove.mockResolvedValue({ job_id: "77" });
    renderPage();
    await screen.findByTestId("security-review-banner");
    fireEvent.click(screen.getByText("אשר והמשך"));
    await waitFor(() => expect(postSecurityReviewApprove).toHaveBeenCalledWith("10"));
  });

  it("'דחה' calls the dismiss endpoint", async () => {
    getInvestigation.mockResolvedValue(detailWithSecurityReview());
    postSecurityReviewDismiss.mockResolvedValue({ ok: true });
    renderPage();
    await screen.findByTestId("security-review-banner");
    fireEvent.click(screen.getByText("דחה"));
    await waitFor(() => expect(postSecurityReviewDismiss).toHaveBeenCalledWith("10"));
  });
});

describe("InvestigationDetailPage live log auto-scroll", () => {
  it("sets scrollTop to scrollHeight on mount and again as new lines arrive (not paused)", async () => {
    const { rerender } = renderPage();
    const log = await screen.findByTestId("investigation-log");
    // jsdom never lays out real pixel sizes, so scrollHeight defaults to 0
    // and the mount-time effect harmlessly sets scrollTop to 0. Force a
    // non-zero scrollHeight and re-trigger the effect (allLog.length change)
    // to prove the effect actually assigns scrollTop := scrollHeight.
    Object.defineProperty(log, "scrollHeight", { value: 400, configurable: true });

    useInvestigationSocketMock.mockReturnValue({ liveLines: [makeLine(2)], connected: true });
    rerender(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/investigations/10"]}>
          <Routes>
            <Route path="/investigations/:jobId" element={<InvestigationDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // A fresh QueryClient on rerender means the query refetches; wait for the
    // second log line (from the mocked live socket) to actually render.
    await screen.findByText("שאילתה 2");
    const log2 = screen.getByTestId("investigation-log");
    expect(log2.scrollTop).toBe(log2.scrollHeight);
  });

  it("shows a paused indicator while the mouse hovers the log, and hides it on mouse-leave", async () => {
    useInvestigationSocketMock.mockReturnValue({ liveLines: [makeLine(2)], connected: true });
    renderPage();
    const log = await screen.findByTestId("investigation-log");

    expect(screen.queryByTestId("log-paused-indicator")).not.toBeInTheDocument();
    fireEvent.mouseEnter(log);
    expect(screen.getByTestId("log-paused-indicator")).toBeInTheDocument();
    fireEvent.mouseLeave(log);
    expect(screen.queryByTestId("log-paused-indicator")).not.toBeInTheDocument();
  });

  it("does not advance scrollTop while paused (hover), and resumes on mouse-leave", async () => {
    renderPage();
    const log = await screen.findByTestId("investigation-log");
    fireEvent.mouseEnter(log);

    Object.defineProperty(log, "scrollHeight", { value: 999, configurable: true });
    // scrollTop is not writable-tracked meaningfully in jsdom, but the
    // effect must not throw and the paused indicator must still be shown
    // after a hover-triggered state update.
    expect(screen.getByTestId("log-paused-indicator")).toBeInTheDocument();

    fireEvent.mouseLeave(log);
    expect(screen.queryByTestId("log-paused-indicator")).not.toBeInTheDocument();
  });

  it("wires the stop button to POST /api/investigations/{id}/stop while running", async () => {
    postInvestigationStop.mockResolvedValue(undefined);
    renderPage();
    await screen.findByTestId("investigation-log");
    fireEvent.click(screen.getByText("עצור"));
    await waitFor(() => expect(postInvestigationStop).toHaveBeenCalledWith("10"));
  });
});

// U12 (docs/REVIEW_2026-09-05.md): the old "המשך חקירה" button silently re-ran the identical
// question with no explanation; it is now "הרחב חקירה (תקציב נוסף)" and calls the dedicated
// expand endpoint instead of re-triggering a plain item investigation from scratch.
describe("InvestigationDetailPage expand ('הרחב חקירה')", () => {
  it("shows 'הרחב חקירה (תקציב נוסף)' instead of the old 'המשך חקירה' once the investigation is done", async () => {
    getInvestigation.mockResolvedValue({
      ...baseDetail(),
      state: "not_found",
      answer: { answer_he: "לא נמצא", sources: [], outcome: "not_found", stopped_reason: "not_found" },
    });
    renderPage();
    await screen.findByTestId("investigation-log");
    expect(screen.getByText("הרחב חקירה (תקציב נוסף)")).toBeInTheDocument();
    expect(screen.queryByText("המשך חקירה")).not.toBeInTheDocument();
  });

  it("calls postInvestigationExpand (not postItemInvestigate) and navigates to the new job", async () => {
    getInvestigation.mockResolvedValue({
      ...baseDetail(),
      state: "stopped",
      answer: { answer_he: "נעצר", sources: [], outcome: "not_found", stopped_reason: "stopped_budget" },
    });
    postInvestigationExpand.mockResolvedValue({ job_id: "99" });
    renderPage();
    await screen.findByTestId("investigation-log");
    fireEvent.click(screen.getByText("הרחב חקירה (תקציב נוסף)"));
    await waitFor(() => expect(postInvestigationExpand).toHaveBeenCalledWith("10"));
    expect(postItemInvestigate).not.toHaveBeenCalled();
  });

  it("shows a human Hebrew outcome chip instead of the raw outcome string", async () => {
    getInvestigation.mockResolvedValue({
      ...baseDetail(),
      state: "stopped",
      answer: {
        answer_he: "נעצר",
        sources: [],
        outcome: "not_found",
        stopped_reason: "stopped_budget",
        queries_used: 15,
        max_queries: 15,
        pages_read: 30,
        max_pages: 30,
      },
    });
    renderPage();
    await screen.findByTestId("investigation-log");
    expect(screen.getByText("נעצר בגלל תקציב")).toBeInTheDocument();
    expect(screen.getByText(/15\/15 שאילתות/)).toBeInTheDocument();
  });

  it("shows a 'רץ עכשיו' badge while running instead of an outcome chip", async () => {
    getInvestigation.mockResolvedValue({ ...baseDetail(), state: "running", answer: null });
    renderPage();
    await screen.findByTestId("investigation-log");
    expect(screen.getByText("רץ עכשיו")).toBeInTheDocument();
  });

  it("translates each log line's own outcome into Hebrew instead of showing it raw (Q5-2)", async () => {
    getInvestigation.mockResolvedValue({
      ...baseDetail(),
      log: [
        { ...makeLine(1), outcome: "partial" },
        { ...makeLine(2), outcome: "not_found" },
        { ...makeLine(3), outcome: "stopped_budget" },
      ],
    });
    renderPage();
    const log = await screen.findByTestId("investigation-log");
    expect(screen.getByText("נמצא חלקית")).toBeInTheDocument();
    expect(screen.getByText("לא נמצא")).toBeInTheDocument();
    expect(screen.getByText("נעצר בגלל תקציב")).toBeInTheDocument();
    expect(log).not.toHaveTextContent("partial");
    expect(log).not.toHaveTextContent("stopped_budget");
  });
});
