import { useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pause, Square } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { CitationText } from "@/components/CitationText";
import { useInvestigationSocket } from "@/hooks/useInvestigationSocket";
import { formatDateTime } from "@/lib/time";

export function InvestigationDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const queryClient = useQueryClient();

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["investigation", jobId],
    queryFn: () => api.getInvestigation(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data?.state === "running" ? 5000 : false),
  });

  const isRunning = data?.state === "running";
  const { liveLines, connected } = useInvestigationSocket(jobId, isRunning);

  const stop = useMutation({
    mutationFn: () => api.postInvestigationStop(jobId!),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["investigation", jobId] }),
  });

  const continueInvestigation = useMutation({
    mutationFn: () => api.postItemInvestigate(data?.item_id ?? 0, { question: data?.question ?? null }),
  });

  const allLog = [...(data?.log ?? []), ...liveLines];

  // Auto-scroll the log to the newest line as it grows, unless the analyst
  // is hovering over it (reading a past entry) — resumes once the pointer
  // leaves.
  const logRef = useRef<HTMLOListElement>(null);
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    const el = logRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [allLog.length, paused]);

  if (isLoading) return <LoadingState label="טוען חקירה…" />;
  if (isError || !data) return <ErrorState onRetry={() => refetch()} message="החקירה לא נמצאה" />;

  return (
    <div className="mx-auto max-w-3xl space-y-5 p-4 md:p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">
            <bdi>{data.question}</bdi>
          </h2>
          <p className="mt-1 text-xs text-fg-dim">
            {formatDateTime(data.started_at)} · {data.rounds} סבבים · {data.pages_read} עמודים ·{" "}
            {connected ? "מחובר" : "מנותק"}
          </p>
        </div>
        {isRunning && (
          <button
            type="button"
            onClick={() => stop.mutate()}
            disabled={stop.isPending}
            className="flex items-center gap-1.5 rounded-md bg-danger px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50"
          >
            <Square size={13} aria-hidden="true" />
            עצור
          </button>
        )}
      </header>

      <section aria-label="לוג חקירה חי">
        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-sm font-semibold text-fg-dim">לוג חקירה</h3>
          {isRunning && paused && (
            <span
              className="flex items-center gap-1 rounded bg-warn/15 px-1.5 py-0.5 text-xs text-warn"
              data-testid="log-paused-indicator"
            >
              <Pause size={11} aria-hidden="true" />
              גלילה מושהית (ריחוף עכבר)
            </span>
          )}
        </div>
        {allLog.length === 0 ? (
          <EmptyState title="אין עדיין רשומות לוג" />
        ) : (
          <ol
            ref={logRef}
            onMouseEnter={() => setPaused(true)}
            onMouseLeave={() => setPaused(false)}
            data-testid="investigation-log"
            className="max-h-[28rem] space-y-2 overflow-y-auto scroll-smooth font-mono text-xs"
          >
            {allLog.map((line, i) => (
              <li key={i} className="rounded-md border border-border bg-bg-raised p-2">
                <div className="flex flex-wrap items-center gap-2 text-fg-dim">
                  <span className="rounded bg-bg-sunken px-1.5 py-0.5">סבב {line.round}</span>
                  <span className="uppercase">{line.lang}</span>
                  <span>{line.results} תוצאות</span>
                  <span className="ms-auto">{formatDateTime(line.at)}</span>
                </div>
                <bdi className="mt-1 block text-fg" dir="auto">
                  {line.query}
                </bdi>
                <bdi className="block text-fg-muted" dir="auto">
                  {line.outcome}
                </bdi>
              </li>
            ))}
          </ol>
        )}
      </section>

      {data.answer && (
        <section aria-label="תשובה סופית" className="rounded-lg border border-border bg-bg-raised p-4">
          <h3 className="mb-2 text-sm font-semibold text-fg-dim">תשובה</h3>
          <bdi className="block text-sm leading-relaxed" dir="auto">
            <CitationText
              text={data.answer.answer_he ?? ""}
              citations={data.answer.sources ?? []}
            />
          </bdi>
        </section>
      )}

      {(data.state === "done" || data.state === "stopped" || data.state === "not_found") && (
        <button
          type="button"
          onClick={() => continueInvestigation.mutate()}
          disabled={continueInvestigation.isPending}
          className="rounded-md border border-accent px-3 py-1.5 text-sm text-accent hover:bg-accent-muted disabled:opacity-50"
        >
          {continueInvestigation.isPending ? "פותח…" : "המשך חקירה"}
        </button>
      )}
    </div>
  );
}
