// Pure helpers for the Morning page's night-run replay timeline, built from
// `pipeline.last_run.stages` (GET /api/status / WS /ws/status,
// agent/eoa/api/services.py `_last_run`/`_stage_timeline_from_log`).
//
// F12 (docs/REVIEW_2026-09-05.md): each stage's `status`/`minutes` now come from that stage's own
// terminal `run_log` event, not a raw heartbeat-row count (which was almost always exactly "2" --
// one `start` + one `done` -- regardless of what the stage actually did).
import type { PipelineLastRun, PipelineStageInfo, StageStatus } from "@/types/api";

// The pipeline order agent/eoa/orchestrator/jobs.py's run_daily() actually runs stages in (NOT
// literally its STAGE_ORDER constant, which omits "dedup_xlang" even though run_daily() runs it
// as a real stage between "classify" and "triage" -- see agent/eoa/api/services.py's matching
// `_DAILY_RUN_STAGE_ORDER` comment), so a real backend payload replays in true pipeline order.
export const STAGE_ORDER = [
  "ingest",
  "embed_dedup",
  "classify",
  "dedup_xlang",
  "triage",
  "deep_search",
  "analyze",
  "tenders",
  "report",
  "export_backup",
  "notify",
] as const;

export const STAGE_LABEL_HE: Record<string, string> = {
  ingest: "קליטה",
  embed_dedup: "הטמעה וזיהוי כפילויות",
  classify: "סיווג",
  dedup_xlang: "זיהוי כפילויות רב-לשוני",
  triage: "מיון (Triage)",
  deep_search: "חיפוש עומק",
  analyze: "ניתוח",
  tenders: "מכרזים",
  // Q5-5 (docs/qa/findings_Q5_r1.md): agent/eoa/orchestrator/jobs.py's STAGE_ORDER runs this stage
  // between "tenders" and "report" -- it was missing here entirely, so the replay timeline fell
  // back to the raw English key.
  post_tenders_catchup: "השלמת מכרזים",
  report: "דוח",
  export_backup: "ייצוא וגיבוי",
  notify: "התראות",
};

/**
 * Q5-5: a stage key this map doesn't know yet (new stage added to
 * agent/eoa/orchestrator/jobs.py's STAGE_ORDER without an update here) used to render as the raw
 * English/snake_case key verbatim. Falls back to a humanised form instead -- "some_new_stage" ->
 * "some new stage" -- so the timeline never shows an untranslated identifier.
 */
export function stageLabelHe(key: string): string {
  const known = STAGE_LABEL_HE[key];
  if (known) return known;
  return key.replace(/_/g, " ");
}

export interface StageTimelineEntry {
  key: string;
  label: string;
  status: StageStatus;
  minutes: number | null;
  // From `PipelineStageInfo.detail.error` -- only ever populated for a `failed` stage (see
  // agent/eoa/orchestrator/jobs.py's `except` handler that logs the `error` run_log event).
  error: string | null;
}

/**
 * Orders `last_run.stages` into a replay timeline: known stages first (in canonical pipeline
 * order, including any that never ran this time -- they show as "pending"), then any
 * unrecognized stage key appended alphabetically.
 */
export function buildStageTimeline(
  lastRun: PipelineLastRun | null | undefined,
): StageTimelineEntry[] {
  if (!lastRun) return [];
  const stages = lastRun.stages ?? {};
  const stageKeys = Object.keys(stages);
  const known = STAGE_ORDER.filter((k) => stageKeys.includes(k));
  const extra = stageKeys.filter((k) => !(STAGE_ORDER as readonly string[]).includes(k)).sort();
  const orderedKeys = [...known, ...extra];

  return orderedKeys.map((key) => {
    const info: PipelineStageInfo = stages[key];
    const rawError = info.detail?.error;
    return {
      key,
      label: stageLabelHe(key),
      status: info.status,
      minutes: info.minutes,
      error: typeof rawError === "string" && rawError.length > 0 ? rawError : null,
    };
  });
}

/**
 * UI QA fix (2026-09-08, docs/qa/content_review/UI-TIMELINE.md): the legend used to render
 * "N דק׳" -- the geresh (U+05F3) after a bare Hebrew "דק" renders, in the app's font stack, as a
 * glyph indistinguishable from a yod, so "14.5 דק׳" read as "14.5 דקי". The spelled-out word has
 * no such ambiguity, so every duration in this component uses it instead of the abbreviation.
 */
export function formatStageMinutes(minutes: number): string {
  return `${minutes} דקות`;
}

export const STAGE_STATUS_LABEL_HE: Record<StageStatus, string> = {
  pending: "טרם הגיע",
  running: "בתהליך",
  done: "הושלם",
  failed: "נכשל",
  skipped: "דולג",
};

export const STAGE_COLOR_BAR_CLASS: Record<StageStatus, string> = {
  done: "bg-ok",
  skipped: "bg-level-archive",
  failed: "bg-danger",
  running: "bg-accent",
  pending: "bg-border-strong",
};

export const STAGE_COLOR_DOT_CLASS = STAGE_COLOR_BAR_CLASS;
