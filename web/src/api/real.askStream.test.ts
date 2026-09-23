import { afterEach, describe, expect, it, vi } from "vitest";

// F30 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md): `askStream`'s SSE loop used to have no
// `error` case at all -- a server-sent `error` frame was silently dropped, then the `done` frame
// that always follows it (the server's own `finally`) fired `onDone`, so a failed stream looked
// successful. Response-body EOF without ever seeing a `done`/`error` frame fired neither callback,
// leaving the caller's streaming state stuck forever. A user abort hit the same gap: the client
// returned from the `AbortError` catch branch without telling the caller anything.

function sseStreamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(encoder.encode(chunks[i]));
      i += 1;
    },
  });
}

function sseResponse(chunks: string[]): Response {
  return new Response(sseStreamFrom(chunks), {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function sse(obj: unknown): string {
  return `data: ${JSON.stringify(obj)}\n\n`;
}

const BODY = {
  question: "q",
  context_item_ids: [],
  context_entity_ids: [],
  history: [],
};

function makeHandlers() {
  return {
    onToken: vi.fn(),
    onCitations: vi.fn(),
    onDone: vi.fn(),
    onError: vi.fn(),
    onAbort: vi.fn(),
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("real.ts askStream SSE handling (F30)", () => {
  it("a normal stream calls onDone once and never onError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          sseResponse([sse({ type: "citations", items: [] }), sse({ type: "token", text: "hi" }), sse({ type: "done" })]),
        ),
      ),
    );
    const { realApi: api } = await import("./real");
    const handlers = makeHandlers();
    api.askStream(BODY, handlers);
    await vi.waitFor(() => expect(handlers.onDone).toHaveBeenCalledTimes(1));
    expect(handlers.onError).not.toHaveBeenCalled();
    expect(handlers.onAbort).not.toHaveBeenCalled();
  });

  it("an `error` frame calls onError and suppresses the `done` that follows it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          sseResponse([sse({ type: "token", text: "partial" }), sse({ type: "error", message: "boom" }), sse({ type: "done" })]),
        ),
      ),
    );
    const { realApi: api } = await import("./real");
    const handlers = makeHandlers();
    api.askStream(BODY, handlers);
    await vi.waitFor(() => expect(handlers.onError).toHaveBeenCalledTimes(1));
    expect(handlers.onError.mock.calls[0][0]).toBeInstanceOf(Error);
    expect((handlers.onError.mock.calls[0][0] as Error).message).toBe("boom");
    // The `done` frame right after `error` must NOT also report success.
    expect(handlers.onDone).not.toHaveBeenCalled();
  });

  it("EOF without a `done`/`error` frame calls onError instead of hanging silently", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(sseResponse([sse({ type: "token", text: "partial" })])),
      ),
    );
    const { realApi: api } = await import("./real");
    const handlers = makeHandlers();
    api.askStream(BODY, handlers);
    await vi.waitFor(() => expect(handlers.onError).toHaveBeenCalledTimes(1));
    expect(handlers.onDone).not.toHaveBeenCalled();
  });

  it("a user abort calls onAbort, not onError/onDone", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((_url: string, init?: RequestInit) => {
        return new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(new DOMException("aborted", "AbortError"));
          });
        });
      }),
    );
    const { realApi: api } = await import("./real");
    const handlers = makeHandlers();
    const abort = api.askStream(BODY, handlers);
    abort();
    await vi.waitFor(() => expect(handlers.onAbort).toHaveBeenCalledTimes(1));
    expect(handlers.onError).not.toHaveBeenCalled();
    expect(handlers.onDone).not.toHaveBeenCalled();
  });
});
