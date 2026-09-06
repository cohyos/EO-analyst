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
