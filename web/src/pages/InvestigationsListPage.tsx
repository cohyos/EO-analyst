import { useState } from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { TableScrollHint } from "@/components/TableScrollHint";
import { NewInvestigationDialog } from "@/components/investigations/NewInvestigationDialog";
import { ToastStack } from "@/components/ToastStack";
import { useToastQueue } from "@/hooks/useToastQueue";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";
import { outcomeLabel, outcomeTone } from "@/lib/investigations";
import { renderBidiText } from "@/lib/bidiText";

// F17 (docs/REVIEW_2026-09-05.md): job #70 ran for a long time in `running` state without the
// operator noticing -- "רץ עכשיו" (running now) makes an in-flight investigation visually loud
// instead of blending into "בתור"/"הושלם".
const STATE_LABEL: Record<string, string> = {
  queued: "בתור",
  running: "רץ עכשיו",
  done: "הושלם",
  stopped: "נעצר",
  error: "שגיאה",
  not_found: "לא נמצא",
};

const STATE_TONE: Record<string, string> = {
  queued: "text-fg-dim bg-bg-sunken",
  running: "text-accent bg-accent-muted animate-pulse",
  done: "text-ok bg-level-yellow-bg",
  stopped: "text-warn bg-level-orange-bg",
  error: "text-danger bg-level-red-bg",
  not_found: "text-fg-dim bg-bg-sunken",
};

export function InvestigationsListPage() {
  const [newOpen, setNewOpen] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { toasts, push: pushToast, dismiss: dismissToast } = useToastQueue();
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["investigations"],
    queryFn: () => api.getInvestigations(30),
    refetchInterval: 5000,
  });

  // R10-links: "פריט מקור" (trigger item) and "דוח אחרון" (last report) columns need the same
  // `provenance` object the detail page shows -- `GET /api/investigations` itself doesn't carry
  // it (that would mean an N+1 provenance query for every row on every list poll), so it's
  // fetched per-row here instead, capped at this page's own 30-row limit and cached/deduped by
  // react-query the same way navigating into each row's own detail page already would be.
  const provenanceQueries = useQueries({
    queries: (data ?? []).map((inv) => ({
      queryKey: ["investigation", inv.job_id],
      queryFn: () => api.getInvestigation(inv.job_id),
      staleTime: 60_000,
    })),
  });
  const provenanceByJobId = new Map(
    (data ?? []).map((inv, i) => [inv.job_id, provenanceQueries[i]?.data?.provenance ?? null]),
  );

  const startNew = useMutation({
    mutationFn: (question: string) => api.postInvestigationNew({ question }),
    onSuccess: (res) => {
      setNewOpen(false);
      queryClient.invalidateQueries({ queryKey: ["investigations"] });
      // Q5-6 (docs/qa/findings_Q5_r1.md): starting a new investigation used to just close the
      // dialog and navigate with no confirmation that anything actually happened. The navigate is
      // delayed a beat so the toast is actually visible before this page unmounts -- an instant
      // navigate would make the toast fire and disappear in the same tick, unseen.
      pushToast(`חקירה חדשה נפתחה · #${res.job_id}`, { tone: "ok" });
      window.setTimeout(() => navigate(`/investigations/${res.job_id}`), 600);
    },
    onError: () => {
      pushToast("פתיחת החקירה נכשלה — נסה שוב", { tone: "danger" });
    },
  });

  const header = (
    <div className="mb-4 flex items-center justify-between gap-3">
      <h1 className="text-lg font-semibold">חקירות עומק</h1>
      <button
        type="button"
        onClick={() => setNewOpen(true)}
        className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90"
      >
        <Plus size={14} aria-hidden="true" />
        חקירה חדשה
      </button>
      {newOpen && (
        <NewInvestigationDialog
          onClose={() => setNewOpen(false)}
          onSubmit={(question) => startNew.mutate(question)}
          submitting={startNew.isPending}
        />
      )}
    </div>
  );

  // W13 (docs/REVIEW_2026-09-06_evening.md round 4): the header (title + "חקירה חדשה" + the new-
  // investigation dialog) used to be hidden behind `if (isLoading) return <LoadingState />` --
  // rendering nothing else until the list loaded. It now renders immediately every time; only the
  // table area below it swaps between skeleton/error/empty/data.
  return (
    <div className="p-4 md:p-6">
      {header}
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
      {isLoading && <LoadingState label="טוען חקירות…" />}
      {isError && <ErrorState error={error} onRetry={() => refetch()} />}
      {!isLoading && !isError && (!data || data.length === 0) && (
        <EmptyState
          title="אין חקירות עומק"
          description="חקירה נפתחת מפריט בפיד, משאלה חדשה, או מהצ'אט."
        />
      )}
      {!isLoading && !isError && data && data.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border" dir="rtl">
          <TableScrollHint />
          <table className="w-full min-w-[720px] text-sm">
            <thead className="bg-bg-raised text-xs text-fg-dim">
              <tr>
                <th className="p-2 text-start">שאלה</th>
                <th className="p-2 text-start">מצב</th>
                <th className="p-2 text-start">סבבים</th>
                <th className="p-2 text-start">עמודים</th>
                <th className="p-2 text-start">תוצאה</th>
                <th className="p-2 text-start">התחיל</th>
                {/* R10-links */}
                <th className="p-2 text-start">פריט מקור</th>
                <th className="p-2 text-start">דוח אחרון</th>
              </tr>
            </thead>
            <tbody>
              {data.map((inv) => (
                <tr
                  key={inv.job_id}
                  className="border-t border-border hover:bg-bg-sunken"
                >
                  <td className="p-2">
                    <Link
                      to={`/investigations/${inv.job_id}`}
                      className="text-accent hover:underline"
                    >
                      <bdi>
                        {renderBidiText(
                          inv.question?.trim() ||
                            (inv.item_title
                              ? `אימות והרחבה: ${inv.item_title}`
                              : `חקירה על פריט #${inv.item_id ?? "?"}`),
                        )}
                      </bdi>
                    </Link>
                    {inv.state === "error" && inv.error && (
                      <div className="mt-1 text-xs text-danger" title={inv.error}>
                        <bdi>
                          שגיאה:{" "}
                          {inv.error.length > 90
                            ? inv.error.slice(0, 90) + "…"
                            : inv.error}
                        </bdi>
                      </div>
                    )}
                  </td>
                  <td className="p-2">
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5 text-xs font-medium",
                        STATE_TONE[inv.state],
                      )}
                    >
                      {STATE_LABEL[inv.state]}
                    </span>
                  </td>
                  <td className="p-2 font-mono font-tabular">{inv.rounds}</td>
                  <td className="p-2 font-mono font-tabular">{inv.pages_read}</td>
                  <td className="p-2">
                    {inv.outcome ? (
                      <span
                        className={cn(
                          "rounded-full px-2 py-0.5 text-xs font-medium",
                          outcomeTone(inv.outcome),
                        )}
                      >
                        {outcomeLabel(inv.outcome)}
                      </span>
                    ) : (
                      <span className="text-fg-muted">—</span>
                    )}
                  </td>
                  <td className="p-2 font-mono text-xs text-fg-dim">
                    {formatDateTime(inv.started_at)}
                  </td>
                  <td className="p-2 text-xs">
                    {provenanceByJobId.get(inv.job_id)?.trigger_item ? (
                      <Link
                        to={`/items/${provenanceByJobId.get(inv.job_id)!.trigger_item!.id}`}
                        className="text-accent hover:underline"
                      >
                        <bdi
                          className="block max-w-[10rem] truncate"
                          title={provenanceByJobId.get(inv.job_id)!.trigger_item!.title || "פריט"}
                        >
                          {provenanceByJobId.get(inv.job_id)!.trigger_item!.title || "פריט"}
                        </bdi>
                      </Link>
                    ) : (
                      <span className="text-fg-muted">—</span>
                    )}
                  </td>
                  <td className="p-2 text-xs">
                    {(() => {
                      const lastReport = provenanceByJobId.get(inv.job_id)?.reports?.[0];
                      return lastReport ? (
                        <Link to={`/reports?id=${lastReport.id}`} className="text-accent hover:underline">
                          <bdi className="block max-w-[10rem] truncate" title={lastReport.title_he}>
                            {lastReport.title_he}
                          </bdi>
                        </Link>
                      ) : (
                        <span className="text-fg-muted">—</span>
                      );
                    })()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
