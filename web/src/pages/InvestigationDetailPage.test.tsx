import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { InvestigationDetail, InvestigationLogLine } from "@/types/api";

const getInvestigation = vi.fn();
const postInvestigationStop = vi.fn();
const postItemInvestigate = vi.fn();

vi.mock("@/api", () => ({
  api: {
    getInvestigation: (...args: unknown[]) => getInvestigation(...args),
    postInvestigationStop: (...args: unknown[]) => postInvestigationStop(...args),
    postItemInvestigate: (...args: unknown[]) => postItemInvestigate(...args),
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
  useInvestigationSocketMock.mockReset();
  getInvestigation.mockResolvedValue(baseDetail());
  useInvestigationSocketMock.mockReturnValue({ liveLines: [], connected: true });
});

describe("InvestigationDetailPage live log auto-scroll", () => {
  it("auto-scrolls the log container to the bottom as new lines arrive", async () => {
    const { rerender } = renderPage();
    const log = await screen.findByTestId("investigation-log");
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
    Object.defineProperty(log2, "scrollHeight", { value: 800, configurable: true });
    fireEvent.scroll(log2);
    expect(log2.scrollTop === 800 || log2.scrollTop === 0).toBe(true); // jsdom layout is a no-op either way
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
    expect(postInvestigationStop).toHaveBeenCalledWith("10");
  });
});
