import type { PipelineLastRun } from "@/types/api";
import { buildStageTimeline, STAGE_COLOR_BAR_CLASS, stageEventColor } from "@/lib/pipelineTimeline";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";

/**
 * Night-run replay from `pipeline.last_run.stages`. The backend has no
 * per-stage start timestamp, so each segment's start is approximated as the
 * previous stage's `last_at` (see `lib/pipelineTimeline.ts`) -- an honest
 * approximation, surfaced as such in the section's helper text.
 */
export function PipelineReplayTimeline({ lastRun }: { lastRun: PipelineLastRun | null }) {
  const entries = buildStageTimeline(lastRun);
  if (entries.length === 0) return null;

  return (
    <section aria-label="שחזור ריצה לילית" className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-fg-dim">ציר זמן — שחזור ריצה לילית</h2>
        <span className="text-xs text-fg-dim">
          תחילת שלב מוערכת לפי סיום השלב הקודם
        </span>
      </div>
      <div
        className="flex h-3 w-full overflow-hidden rounded-full border border-border"
        dir="ltr"
        role="img"
        aria-label="ציר זמן שלבי הריצה הלילית"
      >
        {entries.map((e) => {
          const color = stageEventColor(e.info.last_event);
          return (
            <div
              key={e.key}
              className={cn(STAGE_COLOR_BAR_CLASS[color], "h-full")}
              style={{ flexGrow: 1, flexBasis: 0 }}
              title={`${e.label} — ${e.info.events} אירועים · עדכון אחרון: ${formatDateTime(e.end)}`}
            />
          );
        })}
      </div>
      <ul className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
        {entries.map((e) => {
          const color = stageEventColor(e.info.last_event);
          return (
            <li
              key={e.key}
              className="flex items-center gap-1.5"
              title={`${e.info.events} אירועים · עדכון אחרון: ${formatDateTime(e.end)}`}
            >
              <span
                className={cn("h-2 w-2 shrink-0 rounded-full", STAGE_COLOR_BAR_CLASS[color])}
                aria-hidden="true"
              />
              <span className="truncate text-fg-dim">{e.label}</span>
              <span className="ms-auto shrink-0 font-mono text-fg-muted">{e.info.events}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
