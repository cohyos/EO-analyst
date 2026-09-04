import type { PipelineStatus } from "@/types/api";
import { timeAgo } from "@/lib/time";

/** Live "מה קורה עכשיו" strip, shown only while a pipeline job is running. */
export function NowRunningStrip({ pipeline }: { pipeline: PipelineStatus | null }) {
  if (!pipeline?.current_job) return null;
  const job = pipeline.current_job;
  return (
    <div
      role="status"
      className="flex items-center gap-2 rounded-lg border border-accent/40 bg-accent-muted/20 px-3 py-2 text-sm"
    >
      <span className="relative flex h-2 w-2 shrink-0" aria-hidden="true">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-75" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
      </span>
      <span className="font-medium text-fg">מה קורה עכשיו:</span>
      <span className="text-fg-muted">{pipeline.stage ?? job.kind}</span>
      <span className="text-fg-dim">· עדכון אחרון {timeAgo(job.updated_at)}</span>
    </div>
  );
}
