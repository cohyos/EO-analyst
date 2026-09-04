import { describe, expect, it } from "vitest";
import { normalizeInvestigationLogLine } from "./normalize";

describe("normalizeInvestigationLogLine", () => {
  it("maps the real investigation_log column names (results_n, created_at) to the view model", () => {
    // Real shape sent by both GET /api/investigations/{job_id}'s `log`
    // array and every WS /ws/investigations/{job_id} push — a bare
    // `SELECT *` off the investigation_log table (db/migrations/versions/
    // 0001_core.py:426-440).
    const rawRow = {
      id: 501,
      job_id: 25,
      trigger_item: null,
      round: 2,
      lang: "en",
      query: "Volkswagen Rafael defense contract value",
      engine: "searxng",
      results_n: 7,
      pages_read: 3,
      outcome: "partial",
      notes: null,
      created_at: "2026-09-04T17:44:10+03:00",
    };
    expect(normalizeInvestigationLogLine(rawRow)).toEqual({
      round: 2,
      lang: "en",
      query: "Volkswagen Rafael defense contract value",
      results: 7,
      outcome: "partial",
      at: "2026-09-04T17:44:10+03:00",
    });
  });

  it("falls back to results/at if a caller already passes the clean view-model shape", () => {
    const clean = { round: 1, lang: "he", query: "q", results: 3, outcome: "found", at: "2026-01-01T00:00:00Z" };
    expect(normalizeInvestigationLogLine(clean)).toEqual(clean);
  });

  it("defaults every field for a null/undefined row instead of throwing", () => {
    expect(normalizeInvestigationLogLine(null)).toEqual({
      round: 0,
      lang: "",
      query: "",
      results: 0,
      outcome: "",
      at: "",
    });
  });
});
