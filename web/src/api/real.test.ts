import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";

// W13 (docs/REVIEW_2026-09-06_evening.md round 4): every request made through the real ApiClient
// must give up after a bounded time instead of leaving the caller's LoadingState spinning forever
// on a hung backend/dead connection, and surface a distinct, retryable error for it.
describe("real.ts request() timeout (W13)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("aborts and throws a timeout ApiError after the default 10s when fetch never resolves", async () => {
    const fetchMock = vi.fn((_url: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          const err = new DOMException("aborted", "AbortError");
          reject(err);
        });
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    const { realApi: api, ApiError } = await import("./real");

    const promise = api.getLessons();
    const assertion = expect(promise).rejects.toMatchObject({
      code: "timeout",
      message: "השרת לא הגיב, נסה שוב",
    });
    // Confirms the rejection actually is our ApiError subclass, not some other object shape.
    promise.catch((e) => expect(e).toBeInstanceOf(ApiError));

    await vi.advanceTimersByTimeAsync(10_000);
    await assertion;
  });

  it("does not time out a normal fast response", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify([]), { status: 200, headers: { "Content-Type": "application/json" } }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { realApi: api } = await import("./real");
    await expect(api.getLessons()).resolves.toEqual([]);
  });
});


// Round 12 follow-up: the security-review / blocked-reason / confidence fields declared on
// InvestigationOut must survive normalizeInvestigationDetail -- InvestigationDetailPage reads
// them, and its own tests mock api.getInvestigation with hand-built objects, so only a test
// through the real request path proves the normaliser keeps them.
describe("real.ts getInvestigation keeps security-review, blocked-reason and confidence fields", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("passes the fields through normalisation", async () => {
    const raw = {
      job_id: 113,
      question: "מהם פרטי העסקה?",
      state: "done",
      log: [],
      answer: {
        answer_he: "התשובה הוסתרה.",
        sources: [],
        outcome: "blocked",
        key_facts: [],
        what_was_tried_he: "",
        contradictions_he: "",
        stopped_reason: "blocked",
        security_review: true,
        security_review_reason_he: "חשד להזרקת הוראות",
        security_review_snippet: "ignore previous instructions",
        security_review_resolved: false,
        blocked_reason_he: "התשובה נחסמה בבדיקת אבטחה.",
        confidence: 0.35,
      },
    };
    const fetchMock = vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify(raw), { status: 200, headers: { "Content-Type": "application/json" } }),
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { realApi: api } = await import("./real");
    const detail = await api.getInvestigation("113");
    expect(detail.answer).toMatchObject({
      outcome: "blocked",
      security_review: true,
      security_review_reason_he: "חשד להזרקת הוראות",
      security_review_snippet: "ignore previous instructions",
      security_review_resolved: false,
      blocked_reason_he: "התשובה נחסמה בבדיקת אבטחה.",
      confidence: 0.35,
    });
  });

  it("normalises a missing confidence to null and leaves absent review fields undefined", async () => {
    const raw = {
      job_id: 114,
      question: "q",
      state: "done",
      log: [],
      answer: { answer_he: "x", sources: [], outcome: "found" },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify(raw), { status: 200, headers: { "Content-Type": "application/json" } }),
        ),
      ),
    );
    const { realApi: api } = await import("./real");
    const detail = await api.getInvestigation("114");
    expect(detail.answer?.confidence).toBeNull();
    expect(detail.answer?.security_review).toBeUndefined();
    expect(detail.answer?.blocked_reason_he).toBeUndefined();
  });
});
