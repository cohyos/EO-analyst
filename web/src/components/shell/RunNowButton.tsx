import { useEffect, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  CheckCircle2,
  CircleDashed,
  CircleSlash,
  Loader2,
  Play,
  X,
  XCircle,
} from "lucide-react";
import { api, ApiError } from "@/api";
import { useI18n } from "@/i18n";
import { useRunsCurrent } from "@/hooks/useRunsCurrent";
import { stageLabelHe } from "@/lib/pipelineTimeline";
import { cn } from "@/lib/cn";
import type { CurrentRun, OtherRunningJob, RunStageEntry, StageStatus } from "@/types/api";

type ToastKind = "done" | "partial" | "failed" | "conflict";
interface ToastState {
  kind: ToastKind;
  jobId?: number;
}

const STAGE_ICON: Record<StageStatus, typeof CheckCircle2> = {
  done: CheckCircle2,
  failed: XCircle,
  skipped: CircleSlash,
  running: Loader2,
  pending: CircleDashed,
};

const STAGE_ICON_CLASS: Record<StageStatus, string> = {
  done: "text-ok",
  failed: "text-danger",
  skipped: "text-fg-dim",
  running: "text-accent animate-spin",
  pending: "text-fg-dim",
};

function StageRow({ entry }: { entry: RunStageEntry }) {
  const { t } = useI18n();
  const Icon = STAGE_ICON[entry.status];
  const statusLabel = t(
    (
      {
        done: "topBar.runNowStageDone",
        failed: "topBar.runNowStageFailed",
        skipped: "topBar.runNowStageSkipped",
        running: "topBar.runNowStageRunning",
        pending: "topBar.runNowStagePending",
      } as const
    )[entry.status],
  );
  return (
    <li className="flex items-center gap-2 py-0.5 text-xs">
      <Icon size={13} aria-hidden="true" className={cn("shrink-0", STAGE_ICON_CLASS[entry.status])} />
      <span className="flex-1 truncate">{stageLabelHe(entry.stage)}</span>
      <span className="shrink-0 text-fg-dim">
        {entry.minutes != null ? `${entry.minutes} דק׳` : statusLabel}
      </span>
    </li>
  );
}

function RunProgressPopover({
  current,
  otherRunning,
  onClose,
}: {
  current: CurrentRun | null;
  otherRunning: OtherRunningJob[];
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <div
      role="dialog"
      aria-label={t("topBar.runNowPopoverTitle")}
      className="absolute end-0 top-full z-30 mt-2 w-72 rounded-lg border border-border-strong bg-bg-raised p-3 text-sm shadow-panel"
    >
      <div className="mb-2 flex items-center justify-between">
        <h3 className="font-semibold text-fg">{t("topBar.runNowPopoverTitle")}</h3>
        <button
          type="button"
          onClick={onClose}
          className="rounded p-0.5 text-fg-dim hover:bg-bg-sunken"
          aria-label={t("common.close")}
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      {current ? (
        <>
          <p className="mb-1 text-xs text-fg-dim">{t("topBar.runNowPopoverKind", { kind: current.kind })}</p>
          {current.current_stage && (
            <p className="mb-2 text-xs text-fg-dim">
              {t("topBar.runNowPopoverStage", {
                stage: stageLabelHe(current.current_stage),
              })}
            </p>
          )}
          {current.stages.length > 0 && (
            <ul className="max-h-48 overflow-y-auto border-t border-border pt-1">
              {current.stages.map((entry) => (
                <StageRow key={entry.stage} entry={entry} />
              ))}
            </ul>
          )}
          <div className="mt-2 flex items-center justify-between border-t border-border pt-2 text-xs text-fg-dim">
            <span>
              {current.elapsed_min != null
                ? t("topBar.runNowPopoverElapsed", { minutes: current.elapsed_min })
                : null}
            </span>
            <span>
              {current.eta_min != null
                ? t("topBar.runNowPopoverEta", { minutes: current.eta_min })
                : t("topBar.runNowPopoverEtaUnknown")}
            </span>
          </div>
        </>
      ) : (
        <p className="text-xs text-fg-dim">{t("common.noData")}</p>
      )}

      {otherRunning.length > 0 && (
        <ul className="mt-2 space-y-0.5 border-t border-border pt-2 text-xs text-fg-dim">
          {otherRunning.map((job) => (
            <li key={job.job_id}>
              {t("topBar.runNowPopoverOtherRunning", { kind: job.kind, jobId: job.job_id })}
            </li>
          ))}
        </ul>
      )}

      <Link
        to="/morning"
        onClick={onClose}
        className="mt-3 block text-center text-xs font-medium text-accent hover:underline"
      >
        {t("topBar.runNowPopoverViewLog")}
      </Link>
    </div>
  );
}

function RunToast({ toast, onDismiss }: { toast: ToastState; onDismiss: () => void }) {
  const { t } = useI18n();
  const message =
    toast.kind === "done"
      ? t("topBar.runNowToastDone")
      : toast.kind === "partial"
        ? t("topBar.runNowToastPartial")
        : toast.kind === "failed"
          ? t("topBar.runNowToastFailed")
          : t("topBar.runNowConflictToast", { jobId: toast.jobId ?? "" });
  const tone = toast.kind === "done" ? "border-ok/40 bg-ok/10" : "border-danger/40 bg-danger/10";

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn(
        "fixed bottom-4 start-4 z-50 flex items-center gap-3 rounded-lg border px-4 py-3 text-sm shadow-panel",
        "bg-bg-raised",
        tone,
      )}
    >
      <span>{message}</span>
      {toast.kind === "done" && (
        <Link to="/morning" onClick={onDismiss} className="font-medium text-accent hover:underline">
          {t("topBar.runNowToastViewReport")}
        </Link>
      )}
      <button
        type="button"
        onClick={onDismiss}
        aria-label={t("topBar.runNowToastDismiss")}
        className="rounded p-0.5 text-fg-dim hover:bg-bg-sunken"
      >
        <X size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

/**
 * U4/F17 (docs/REVIEW_2026-09-05.md): "הרץ עכשיו" gave no feedback and got double-clicked into two
 * overlapping runs. This button is now idempotent (the server refuses a second run and returns
 * 409 with the existing job — shown as a toast instead of a silent duplicate), shows live
 * stage-by-stage progress + an ETA in a popover while active, and toasts the outcome when the
 * tracked run finishes.
 */
export function RunNowButton() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [toast, setToast] = useState<ToastState | null>(null);
  const lastJobIdRef = useRef<number | null>(null);

  const runsCurrent = useRunsCurrent();
  const current = runsCurrent.data?.current ?? null;
  const otherRunning = runsCurrent.data?.other_running ?? [];

  const runNow = useMutation({
    mutationFn: () => api.postRun("daily", "full"),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["runs-current"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      setPopoverOpen(true);
    },
    onError: (err: unknown) => {
      if (err instanceof ApiError && err.code === "conflict") {
        const detail = err.detail as { job_id?: number } | null;
        setToast({ kind: "conflict", jobId: detail?.job_id });
        setPopoverOpen(true);
        queryClient.invalidateQueries({ queryKey: ["runs-current"] });
      }
    },
  });

  // U4c: detect the tracked run finishing (current -> null) and toast the outcome.
  useEffect(() => {
    if (current) {
      lastJobIdRef.current = current.job_id;
      return;
    }
    const lastId = lastJobIdRef.current;
    if (lastId == null) return;
    lastJobIdRef.current = null;
    let cancelled = false;
    void (async () => {
      try {
        const jobs = await api.getJobs(undefined, 10);
        const job = jobs.find((j) => j.id === lastId);
        if (cancelled || !job) return;
        if (job.state === "done") {
          setToast({ kind: "done", jobId: job.id });
          queryClient.invalidateQueries({ queryKey: ["morning"] });
          queryClient.invalidateQueries({ queryKey: ["reports"] });
        } else if (job.state === "partial") {
          setToast({ kind: "partial", jobId: job.id });
        } else if (job.state === "failed") {
          setToast({ kind: "failed", jobId: job.id });
        }
      } catch {
        // best-effort only — a missed completion toast isn't worth surfacing an error for.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [current, queryClient]);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 8000);
    return () => clearTimeout(timer);
  }, [toast]);

  const isBusy = runNow.isPending || current != null;
  const label = runNow.isPending
    ? t("topBar.runNowQueued")
    : current
      ? t("topBar.running")
      : t("topBar.runNow");

  return (
    <div className="relative shrink-0">
      <button
        type="button"
        onClick={() => (isBusy ? setPopoverOpen((v) => !v) : runNow.mutate())}
        aria-haspopup="true"
        aria-expanded={popoverOpen}
        aria-label={isBusy ? t("topBar.runningAriaLabel") : t("topBar.runNow")}
        className="flex shrink-0 items-center gap-1.5 rounded-md bg-accent px-2 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-60 sm:px-3"
      >
        {isBusy ? (
          <Loader2 size={14} aria-hidden="true" className="animate-spin" />
        ) : (
          <Play size={14} aria-hidden="true" />
        )}
        <span className="hidden sm:inline">{label}</span>
      </button>

      {popoverOpen && (
        <RunProgressPopover
          current={current}
          otherRunning={otherRunning}
          onClose={() => setPopoverOpen(false)}
        />
      )}

      {toast && <RunToast toast={toast} onDismiss={() => setToast(null)} />}
    </div>
  );
}
