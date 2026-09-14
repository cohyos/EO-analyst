import type { PipelineLastRun } from "@/types/api";
import {
  buildStageTimeline,
  formatStageMinutes,
  STAGE_COLOR_BAR_CLASS,
  STAGE_STATUS_LABEL_HE,
} from "@/lib/pipelineTimeline";
import { cn } from "@/lib/cn";
import { formatDateTime } from "@/lib/time";

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
      <p role="status" className={cn("text-sm", lastRun?.state === "failed" ? "text-danger" : "text-fg-muted")}>
        הריצה האחרונה: {lastRun?.state === "failed" ? "נכשלה" : lastRun?.state === "partial" ? "הסתיימה חלקית" : "הושלמה"}
        {" · "}{formatDateTime(lastRun?.started_at)}
      </p>
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
              e.minutes != null ? ` · ${formatStageMinutes(e.minutes)}` : ""
            }${e.error ? ` · ${e.error}` : ""}`}
          />
        ))}
      </div>
      {/* UI QA fix (2026-09-08, docs/qa/content_review/UI-TIMELINE.md): a plain multi-column grid
          used to let a long label overflow its cell (the grid item's default `min-width: auto`
          ignores the child's `truncate`), which read as clipped/overlapping text next to the
          duration. `min-w-0` on every cell -- and on the label span itself, since it also sits in
          a flex row -- forces both to respect the track/row width so `truncate` actually applies,
          and the duration gets its own fixed-width column instead of just trailing the label. */}
      <ul className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs lg:grid-cols-4">
        {entries.map((e) => {
          const failed = e.status === "failed";
          const durationText = e.minutes != null ? formatStageMinutes(e.minutes) : STAGE_STATUS_LABEL_HE[e.status];
          const tooltipParts = [STAGE_STATUS_LABEL_HE[e.status]];
          if (e.minutes != null) tooltipParts.push(formatStageMinutes(e.minutes));
          if (e.error) tooltipParts.push(e.error);
          return (
            <li key={e.key} className="flex min-w-0 items-center gap-1.5" title={tooltipParts.join(" · ")}>
              <span
                className={cn("h-2 w-2 shrink-0 rounded-full", STAGE_COLOR_BAR_CLASS[e.status])}
                aria-hidden="true"
              />
              <span
                className="flex min-w-0 flex-1 items-center gap-1"
                aria-label={`${e.label} — ${STAGE_STATUS_LABEL_HE[e.status]}${failed && e.error ? ` — ${e.error}` : ""}`}
              >
                <span className="min-w-0 truncate text-fg-dim">{e.label}</span>
                {/* Never inside the truncating span: a long label must not be able to clip the
                    failure marker off the end, since it's the only in-legend hint of which
                    stage broke the run. */}
                {failed && <span className="shrink-0 text-xs font-semibold text-danger">נכשל</span>}
                {(e.status === "partial" || e.status === "deferred") && (
                  <span className="shrink-0 text-xs font-semibold text-warn">{STAGE_STATUS_LABEL_HE[e.status]}</span>
                )}
              </span>
              <span className="ms-auto w-16 shrink-0 text-end font-mono text-fg-muted">{durationText}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
