import type { PipelineLastRun } from "@/types/api";
import {
  buildStageTimeline,
  STAGE_COLOR_BAR_CLASS,
  STAGE_STATUS_LABEL_HE,
} from "@/lib/pipelineTimeline";
import { cn } from "@/lib/cn";

/**
 * Night-run replay from `pipeline.last_run.stages` (F12: each stage's own outcome/duration, not a
 * heartbeat-row count). Segment widths are proportional to each stage's `minutes` — a stage with
 * no recorded duration (skipped/pending, or an older run predating the F12 fix) gets a small,
 * equal minimum width so it still reads as one calm sliver of a mostly-fast pipeline instead of
 * a `1/n`-sized visual lie.
 */
export function PipelineReplayTimeline({ lastRun }: { lastRun: PipelineLastRun | null }) {
  const entries = buildStageTimeline(lastRun);
  if (entries.length === 0) return null;

  const MIN_WEIGHT = 0.4;
  const weights = entries.map((e) => (e.minutes && e.minutes > 0 ? e.minutes : MIN_WEIGHT));

  return (
    <section aria-label="שחזור ריצה לילית" className="space-y-2">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-fg-dim">ציר זמן — שחזור ריצה לילית</h2>
        <span className="text-xs text-fg-dim">רוחב כל שלב יחסי למשך הריצה שלו</span>
      </div>
      <div
        className="flex h-3 w-full overflow-hidden rounded-full border border-border"
        dir="ltr"
        role="img"
        aria-label="ציר זמן שלבי הריצה הלילית"
      >
        {entries.map((e, i) => (
          <div
            key={e.key}
            className={cn(STAGE_COLOR_BAR_CLASS[e.status], "h-full")}
            style={{ flexGrow: weights[i], flexBasis: 0 }}
            title={`${e.label} — ${STAGE_STATUS_LABEL_HE[e.status]}${
              e.minutes != null ? ` · ${e.minutes} דקות` : ""
            }`}
          />
        ))}
      </div>
      <ul className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs sm:grid-cols-2 lg:grid-cols-3">
        {entries.map((e) => (
          <li
            key={e.key}
            className="flex items-center gap-1.5"
            title={`${STAGE_STATUS_LABEL_HE[e.status]}${e.minutes != null ? ` · ${e.minutes} דקות` : ""}`}
          >
            <span
              className={cn("h-2 w-2 shrink-0 rounded-full", STAGE_COLOR_BAR_CLASS[e.status])}
              aria-hidden="true"
            />
            <span className="truncate text-fg-dim">{e.label}</span>
            <span className="ms-auto shrink-0 font-mono text-fg-muted">
              {e.minutes != null ? `${e.minutes} דק׳` : STAGE_STATUS_LABEL_HE[e.status]}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
