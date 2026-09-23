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

  // F29 (SOL-REVIEW-2026-09-24): the backend job result (`InvestigationOut`,
  // eoa.llm.schemas.analysis) actually persists the flagged reason/snippet as
  // `security_flag_reason`/`security_flag_snippet` -- `security_review_reason_he`/
  // `security_review_snippet` never existed on the real wire payload (only the test fixture
  // above assumed they did). Old code read only the wrong names, so the detail page's security
  // review banner rendered with an empty reason/snippet for every real flagged investigation.
  it("maps the real wire field names security_flag_reason/security_flag_snippet", async () => {
    const raw = {
      job_id: 115,
      question: "q",
      state: "done",
      log: [],
      answer: {
        answer_he: "התשובה הוסתרה.",
        sources: [],
        outcome: "partial",
        key_facts: [],
        what_was_tried_he: "",
        contradictions_he: "",
        security_review: true,
        security_flag_reason: "חשד להזרקת פרומפט במקור",
        security_flag_snippet: "התעלם מההוראות הקודמות...",
        security_review_resolved: false,
        confidence: 0.4,
      },
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
    const detail = await api.getInvestigation("115");
    expect(detail.answer?.security_review_reason_he).toBe("חשד להזרקת פרומפט במקור");
    expect(detail.answer?.security_review_snippet).toBe("התעלם מההוראות הקודמות...");
  });
});

// PD-ui (docs/PLAN_PRODUCT_DOSSIER.md): the dossier normalizers must never throw on a
// backend build that predates this feature, omits a field, or sends a malformed/partial value --
// every consumer (DossiersPage/DossierDetailPage) reads the result unconditionally, exactly like
// every other PL-ui-style normalizer in this file.
describe("real.ts dossier normalizers (PD-ui)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  function stubFetchJson(body: unknown) {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }),
        ),
      ),
    );
  }

  it("normalizes a well-formed getDossiers list", async () => {
    stubFetchJson([
      {
        product_key: "elbit-systems-spectro-xr",
        product_name: "SPECTRO XR",
        vendor: "Elbit Systems",
        latest: { id: 501, created_at: "2026-09-06T21:10:00+03:00", outcome: "found", confidence: 0.82, report_id: 940 },
        count: 3,
      },
    ]);
    const { realApi: api } = await import("./real");
    const list = await api.getDossiers();
    expect(list).toEqual([
      {
        product_key: "elbit-systems-spectro-xr",
        product_name: "SPECTRO XR",
        vendor: "Elbit Systems",
        // llm_leg (PD-cloud-tools, 2026-09-09) and product_line (PD-vocab-ui, 2026-09-09) are
        // both always present on the normalized shape -- "local"/null defaults when the raw
        // response (as stubbed above) doesn't carry them.
        latest: { id: 501, created_at: "2026-09-06T21:10:00+03:00", outcome: "found", confidence: 0.82, report_id: 940, llm_leg: "local" },
        count: 3,
        product_line: null,
      },
    ]);
  });

  it("degrades a malformed/partial getDossiers list to safe defaults instead of throwing", async () => {
    stubFetchJson([{ product_key: "x" }, null, {}]);
    const { realApi: api } = await import("./real");
    const list = await api.getDossiers();
    expect(list).toHaveLength(3);
    expect(list[0]).toMatchObject({ product_key: "x", product_name: "", vendor: null, latest: null, count: 0 });
    expect(list[1]).toMatchObject({ product_key: "", latest: null });
  });

  it("normalizes getDossier's nested ProductDossierOut, defaulting missing arrays/objects", async () => {
    stubFetchJson({
      product_key: "elbit-systems-spectro-xr",
      product_name: "SPECTRO XR",
      vendor: "Elbit Systems",
      aliases: ["Spectro"],
      dossiers: [{ id: 501, created_at: "2026-09-06T21:10:00+03:00", outcome: "found", confidence: 0.82, report_id: 940 }],
      latest: {
        identity: { product_name: "SPECTRO XR", vendor: "Elbit Systems", cites: [1] },
        summary: [{ text_he: "תקציר.", cites: [1] }],
        // every other array-shaped field omitted on purpose
        sources: [{ n: 1, url: "https://example.test", title: "Source 1" }],
      },
      pending_job: { job_id: 77, state: "running" },
    });
    const { realApi: api } = await import("./real");
    const detail = await api.getDossier("elbit-systems-spectro-xr");
    expect(detail.aliases).toEqual(["Spectro"]);
    expect(detail.pending_job).toEqual({ job_id: "77", state: "running", progress: [] });
    expect(detail.latest?.identity.product_name).toBe("SPECTRO XR");
    expect(detail.latest?.summary).toEqual([{ text_he: "תקציר.", cites: [1] }]);
    // Every array field the backend omitted normalizes to [], not undefined/throw.
    expect(detail.latest?.specifications).toEqual([]);
    expect(detail.latest?.deals).toEqual([]);
    expect(detail.latest?.what_changed).toEqual([]);
    expect(detail.latest?.maturity).toEqual({
      trl: null,
      operational_users: [],
      platforms_integrated: [],
      first_fielding: null,
      assessment_he: null,
      cites: [],
    });
    expect(detail.latest?.sources).toEqual([
      { n: 1, url: "https://example.test", title: "Source 1", kind: null, reliability: null, accessed_at: null },
    ]);
  });

  it("normalizes a missing/null getDossier response to the zero shape rather than throwing", async () => {
    stubFetchJson(null);
    const { realApi: api } = await import("./real");
    const detail = await api.getDossier("unknown-product");
    expect(detail).toMatchObject({ product_key: "", product_name: "", aliases: [], dossiers: [], latest: null, pending_job: null });
  });

  it("PD-fix item 5: normalizes pending_job.progress, one entry per topic", async () => {
    stubFetchJson({
      product_key: "elbit-systems-spectro-xr",
      product_name: "SPECTRO XR",
      vendor: "Elbit Systems",
      aliases: [],
      dossiers: [],
      latest: null,
      pending_job: {
        job_id: 77,
        state: "running",
        progress: [
          { topic: "specifications", title_he: "מפרט ודף נתונים", status: "done", seconds: 12.3, sources_found: 2 },
          { topic: "versions", title_he: "גרסאות וציר זמן", status: "running", seconds: null, sources_found: null },
          { topic: "performance", status: "not-a-real-status" }, // malformed -- falls back to "pending"
        ],
      },
    });
    const { realApi: api } = await import("./real");
    const detail = await api.getDossier("elbit-systems-spectro-xr");
    expect(detail.pending_job?.progress).toEqual([
      { topic: "specifications", title_he: "מפרט ודף נתונים", status: "done", seconds: 12.3, sources_found: 2 },
      { topic: "versions", title_he: "גרסאות וציר זמן", status: "running", seconds: null, sources_found: null },
      { topic: "performance", title_he: "", status: "pending", seconds: null, sources_found: null },
    ]);
  });

  it("PD-fix-2 item 3: normalizes a deal row's date_kind/region_he through", async () => {
    stubFetchJson({
      product_key: "elbit-systems-spectro-xr",
      product_name: "SPECTRO XR",
      vendor: "Elbit Systems",
      aliases: [],
      dossiers: [],
      latest: {
        identity: { product_name: "SPECTRO XR", cites: [] },
        deals: [
          {
            date: "2026-09-02",
            date_kind: "published",
            customer: "לקוח בינלאומי (לא מזוהה)",
            country: "",
            region_he: "אסיה-פסיפיק",
            kind: "FMS",
            amount: "כ-80 מיליון דולר",
            currency: "USD",
            quantity: null,
            platform: null,
            cites: [1],
            confidence: 0.7,
          },
          // a deal row persisted before date_kind/region_he existed -- must not throw.
          { date: null, customer: "X", country: "IL", kind: "contract_award", cites: [] },
        ],
        sources: [],
      },
      pending_job: null,
    });
    const { realApi: api } = await import("./real");
    const detail = await api.getDossier("elbit-systems-spectro-xr");
    expect(detail.latest?.deals[0]).toMatchObject({
      date_kind: "published",
      region_he: "אסיה-פסיפיק",
      country: "",
    });
    expect(detail.latest?.deals[1]).toMatchObject({ date_kind: null, region_he: null });
  });
});
