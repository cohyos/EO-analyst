// Shared display helpers for the /tenders screen (section 5.2 / FR-5.2).
import type { TenderIntake, TenderStatus } from "@/types/api";

export const TENDER_STATUS_LABEL: Record<TenderStatus, string> = {
  open: "פתוח",
  closed: "סגור",
  awarded: "הוענק",
  unknown: "לא ידוע",
  archived: "בארכיון",
};

export const TENDER_STATUS_CHIP_CLASS: Record<TenderStatus, string> = {
  open: "bg-ok/15 text-ok",
  closed: "bg-bg-sunken text-fg-dim",
  awarded: "bg-accent-muted text-accent",
  unknown: "bg-warn/15 text-warn",
  archived: "bg-bg-sunken text-fg-dim",
};

// F24: the tenders board's default (no explicit status filter) view -- 'open' and recently-seen
// 'unknown' tenders only. Mirrors eoa.api.services.DEFAULT_STATUSES.
export const TENDER_DEFAULT_VIEW_STATUSES: TenderStatus[] = ["open", "unknown"];

// W2b (open intake, 2026-09-06 evening): only 'candidate' gets its own visible badge -- 'accepted'
// is the normal/expected state (no badge needed) and 'rejected-by-user' is hidden by default
// (services.list_tenders never returns it), so there is no row to badge in the first place.
export const TENDER_CANDIDATE_BADGE_LABEL = "מועמד";
export const TENDER_INTAKE_CHIP_CLASS: Record<TenderIntake, string> = {
  candidate: "bg-warn/15 text-warn",
  accepted: "bg-ok/15 text-ok",
  "rejected-by-user": "bg-bg-sunken text-fg-dim",
};

/** 0-1 relevance_score as a rounded percentage string, e.g. 0.62 -> "62%". Returns "—" for null
 * (an LLM-unclassified notice's score should never actually be null post-migration-0021, but the
 * API type allows it defensively). */
export function relevanceScorePercent(score: number | null | undefined): string {
  if (score == null || Number.isNaN(score)) return "—";
  return `${Math.round(score * 100)}%`;
}

/** Days-left urgency threshold shared by the tenders table chip and the Morning tile. */
export const DEADLINE_URGENT_DAYS = 14;
export const DEADLINE_SOON_DAYS = 30;

/**
 * Whole days between `now` and `deadline` (a `DATE`-only ISO string, e.g.
 * "2026-09-20"). Computed in UTC calendar days on both sides so a bare date
 * string never picks up a timezone-shifted off-by-one against the local
 * `now` (per docs/CONVENTIONS.md rule 7, display stays Asia/Jerusalem, but
 * day-granularity arithmetic is timezone-agnostic by construction here).
 * Returns null for a missing/unparseable deadline. Negative means overdue.
 */
export function daysLeft(
  deadline: string | null | undefined,
  now: Date = new Date(),
): number | null {
  if (!deadline) return null;
  const d = new Date(deadline);
  if (Number.isNaN(d.getTime())) return null;
  const utcDeadline = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  const utcNow = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.round((utcDeadline - utcNow) / 86_400_000);
}

export type LikelihoodBand = "high" | "mid" | "low";

/** Bands a 0-1 likelihood into high/mid/low for color coding (>=0.66 / >=0.33 / below). */
export function likelihoodBand(value: number | null | undefined): LikelihoodBand {
  const v = value ?? 0;
  if (v >= 0.66) return "high";
  if (v >= 0.33) return "mid";
  return "low";
}

export const LIKELIHOOD_BAND_FG_CLASS: Record<LikelihoodBand, string> = {
  high: "text-level-red",
  mid: "text-level-orange",
  low: "text-level-archive",
};

export const LIKELIHOOD_BAND_CHIP_CLASS: Record<LikelihoodBand, string> = {
  high: "bg-level-red-bg text-level-red",
  mid: "bg-level-orange-bg text-level-orange",
  low: "bg-level-archive-bg text-level-archive",
};

export const LIKELIHOOD_BAND_BAR_CLASS: Record<LikelihoodBand, string> = {
  high: "bg-level-red",
  mid: "bg-level-orange",
  low: "bg-level-archive",
};
