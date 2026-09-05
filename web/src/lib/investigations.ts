import type { InvestigationOutcomeReason } from "@/types/api";

/**
 * Human Hebrew labels for the granular investigation outcome (U11/F17/F18,
 * docs/REVIEW_2026-09-05.md) -- distinct from the raw job `state`. A `not_found` alone told the
 * analyst nothing about *why* (ran out of budget? searched and found nothing? nothing to search
 * at all?), so the backend now exposes `stopped_reason` and this maps it to a chip label + tone.
 */
export const OUTCOME_LABEL: Record<string, string> = {
  found: "נמצא",
  partial: "נמצא חלקית",
  not_found: "לא נמצא",
  stopped_budget: "נעצר בגלל תקציב",
  stopped_timeout: "נעצר בגלל זמן",
  insufficient_context: "אין מספיק מידע לחיפוש",
};

export const OUTCOME_TONE: Record<string, string> = {
  found: "text-ok bg-level-yellow-bg",
  partial: "text-ok bg-level-yellow-bg",
  not_found: "text-fg-dim bg-bg-sunken",
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
  "stopped_budget",
  "stopped_timeout",
  "insufficient_context",
];
