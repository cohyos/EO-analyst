// Pure helpers for the Morning page's night-run replay timeline, built from
// `pipeline.last_run.stages` (GET /api/status / WS /ws/status). The backend
// doesn't expose a per-stage start time (only `events`/`last_event`/`last_at`
// per stage — agent/eoa/api/services.py `pipeline_status()`), so this module
// approximates each stage's start as the previous stage's `last_at` (or the
// run's own `started_at` for the first stage). That's an honest
// approximation, not a real per-stage timestamp.
import type { PipelineLastRun, PipelineStageInfo } from "@/types/api";

// Mirrors agent/eoa/orchestrator/jobs.py STAGE_ORDER exactly, so a real
// backend payload replays in true pipeline order.
export const STAGE_ORDER = [
  "ingest",
  "embed_dedup",
  "classify",
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
  triage: "מיון (Triage)",
  deep_search: "חיפוש עומק",
  analyze: "ניתוח",
  tenders: "מכרזים",
  report: "דוח",
  export_backup: "ייצוא וגיבוי",
  notify: "התראות",
};

export interface StageTimelineEntry {
  key: string;
  label: string;
  start: string | null;
  end: string | null;
  info: PipelineStageInfo;
}

function toTime(iso: string | null | undefined): number {
  if (!iso) return 0;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? 0 : t;
}

/**
 * Orders `last_run.stages` into a replay timeline: known stages first (in
 * canonical pipeline order), then any unrecognized stage keys appended,
 * sorted by their own `last_at` so the sequence still reads chronologically.
 * Each entry's `start` is the previous entry's `end` (or the run's
 * `started_at` for the first entry) -- see module docstring.
 */
export function buildStageTimeline(
  lastRun: PipelineLastRun | null | undefined,
): StageTimelineEntry[] {
  if (!lastRun) return [];
  const stages = lastRun.stages ?? {};
  const stageKeys = Object.keys(stages);
  const known = STAGE_ORDER.filter((k) => stageKeys.includes(k));
  const extra = stageKeys
    .filter((k) => !(STAGE_ORDER as readonly string[]).includes(k))
    .sort((a, b) => toTime(stages[a]?.last_at) - toTime(stages[b]?.last_at));
  const orderedKeys = [...known, ...extra];

  let prevEnd: string | null = lastRun.started_at ?? null;
  return orderedKeys.map((key) => {
    const info = stages[key];
    const entry: StageTimelineEntry = {
      key,
      label: STAGE_LABEL_HE[key] ?? key,
      start: prevEnd,
      end: info.last_at,
      info,
    };
    prevEnd = info.last_at ?? prevEnd;
    return entry;
  });
}

export type StageEventColor = "done" | "skipped" | "error" | "running" | "unknown";

export function stageEventColor(lastEvent: string | null | undefined): StageEventColor {
  if (!lastEvent) return "unknown";
  if (lastEvent === "done") return "done";
  if (lastEvent === "skipped") return "skipped";
  if (lastEvent === "error" || lastEvent === "failed") return "error";
  if (lastEvent === "running") return "running";
  return "unknown";
}

export const STAGE_COLOR_BAR_CLASS: Record<StageEventColor, string> = {
  done: "bg-ok",
  skipped: "bg-level-archive",
  error: "bg-danger",
  running: "bg-accent",
  unknown: "bg-border-strong",
};

export const STAGE_COLOR_DOT_CLASS = STAGE_COLOR_BAR_CLASS;
