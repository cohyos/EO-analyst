import type { InvestigationOutcomeReason } from "@/types/api";

/**
 * Human Hebrew labels for the granular investigation outcome (U11/F17/F18,
 * docs/REVIEW_2026-09-05.md) -- distinct from the raw job `state`. A `not_found` alone told the
 * analyst nothing about *why* (ran out of budget? searched and found nothing? nothing to search
 * at all?), so the backend now exposes `stopped_reason` and this maps it to a chip label + tone.
 */
// 2026-09-06 (job 86 regression fix, point 5): `off_topic` is a distinct outcome from `not_found`
// -- a `finish` was accepted with an answer that (a deterministic anchor check and/or an LLM
// relevance judge determined) does not actually address the investigation question, as opposed to
// a genuine "searched and nothing was there". Currently set only via manual/retroactive
// correction (see docs/qa job-86 notes); surfaced here so the UI can label it distinctly from a
// plain "not found" the moment a job's result carries it.
export const OUTCOME_LABEL: Record<string, string> = {
  found: "נמצא",
  partial: "נמצא חלקית",
  not_found: "לא נמצא",
  off_topic: "לא רלוונטי לשאלה",
  stopped_budget: "נעצר בגלל תקציב",
  stopped_timeout: "נעצר בגלל זמן",
  insufficient_context: "אין מספיק מידע לחיפוש",
};

export const OUTCOME_TONE: Record<string, string> = {
  found: "text-ok bg-level-yellow-bg",
  partial: "text-ok bg-level-yellow-bg",
  not_found: "text-fg-dim bg-bg-sunken",
  off_topic: "text-warn bg-level-orange-bg",
  stopped_budget: "text-warn bg-level-orange-bg",
  stopped_timeout: "text-warn bg-level-orange-bg",
  insufficient_context: "text-fg-dim bg-bg-sunken",
};

export function outcomeLabel(outcome: string | null | undefined): string {
  if (!outcome) return "—";
  return OUTCOME_LABEL[outcome] ?? outcome;
}

export function outcomeTone(outcome: string | null | undefined): string {
  if (!outcome) return "text-fg-dim bg-bg-sunken";
  return OUTCOME_TONE[outcome] ?? "text-fg-dim bg-bg-sunken";
}

export const ALL_OUTCOME_REASONS: InvestigationOutcomeReason[] = [
  "found",
  "partial",
  "not_found",
  "off_topic",
  "stopped_budget",
  "stopped_timeout",
  "insufficient_context",
];
