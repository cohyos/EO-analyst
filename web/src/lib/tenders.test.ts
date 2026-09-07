import { describe, expect, it } from "vitest";
import { DEADLINE_URGENT_DAYS, daysLeft, likelihoodBand, tenderSourceLabel } from "./tenders";

describe("daysLeft", () => {
  const now = new Date("2026-09-04T10:00:00Z");

  it("returns null for a missing deadline", () => {
    expect(daysLeft(null, now)).toBeNull();
    expect(daysLeft(undefined, now)).toBeNull();
  });

  it("returns null for an unparseable deadline", () => {
    expect(daysLeft("not-a-date", now)).toBeNull();
  });

  it("computes whole days remaining for a future deadline", () => {
    expect(daysLeft("2026-09-10", now)).toBe(6);
  });

  it("returns 0 for a deadline that is today", () => {
    expect(daysLeft("2026-09-04", now)).toBe(0);
  });

  it("returns a negative count for an overdue deadline", () => {
    expect(daysLeft("2026-08-30", now)).toBe(-5);
  });

  it("is not thrown off by the `now` argument carrying a time-of-day component", () => {
    const lateInDay = new Date("2026-09-04T23:55:00Z");
    expect(daysLeft("2026-09-10", lateInDay)).toBe(6);
  });

  it("marks a deadline under DEADLINE_URGENT_DAYS as urgent (the table chip's threshold)", () => {
    const days = daysLeft("2026-09-15", now); // 11 days out
    expect(days).not.toBeNull();
    expect(days! < DEADLINE_URGENT_DAYS).toBe(true);
  });
});

describe("likelihoodBand", () => {
  it("bands >=0.66 as high", () => {
    expect(likelihoodBand(0.66)).toBe("high");
    expect(likelihoodBand(0.9)).toBe("high");
  });

  it("bands 0.33-0.66 as mid", () => {
    expect(likelihoodBand(0.33)).toBe("mid");
    expect(likelihoodBand(0.5)).toBe("mid");
  });

  it("bands below 0.33 as low", () => {
    expect(likelihoodBand(0.32)).toBe("low");
    expect(likelihoodBand(0)).toBe("low");
  });

  it("treats a null/undefined likelihood as 0 (low)", () => {
    expect(likelihoodBand(null)).toBe("low");
    expect(likelihoodBand(undefined)).toBe("low");
  });
});

// Content review (docs/qa/content_review/CR-ui.md): `GET /api/tenders` returns the raw connector
// id from config/tenders.yaml (e.g. `rfi_rfp_news`) as `tender.source`, not a display name -- the
// table used to render that slug as-is next to properly-labeled columns.
describe("tenderSourceLabel", () => {
  it("maps a known connector id to its short display label", () => {
    expect(tenderSourceLabel("ted_eu")).toBe("TED (Tenders Electronic Daily)");
    expect(tenderSourceLabel("rfi_rfp_news")).toBe("RFI/RFP defense news");
    expect(tenderSourceLabel("jp_search")).toBe("Japan ATLA/MoD procurement");
  });

  it("falls back to the raw id for an unmapped source (e.g. mock/legacy display names)", () => {
    expect(tenderSourceLabel("SAM.gov")).toBe("SAM.gov");
    expect(tenderSourceLabel("some_future_connector")).toBe("some_future_connector");
  });

  it("returns an em dash placeholder for a missing source", () => {
    expect(tenderSourceLabel(null)).toBe("—");
    expect(tenderSourceLabel(undefined)).toBe("—");
    expect(tenderSourceLabel("")).toBe("—");
  });
});
