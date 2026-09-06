import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { NewInvestigationDialog } from "@/components/investigations/NewInvestigationDialog";
import { ToastStack } from "@/components/ToastStack";
import { useToastQueue } from "@/hooks/useToastQueue";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";
import { outcomeLabel, outcomeTone } from "@/lib/investigations";

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
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["investigations"],
    queryFn: () => api.getInvestigations(30),
    refetchInterval: 5000,
  });

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

  if (isLoading) return <LoadingState label="טוען חקירות…" />;
  if (isError) return <ErrorState onRetry={() => refetch()} />;
  if (!data || data.length === 0)
    return (
      <div className="p-4 md:p-6">
        {header}
        <EmptyState title="אין חקירות עומק" description="חקירה נפתחת מפריט בפיד, משאלה חדשה, או מהצ'אט." />
        <ToastStack toasts={toasts} onDismiss={dismissToast} />
      </div>
    );

  return (
    <div className="p-4 md:p-6">
      {header}
      <ToastStack toasts={toasts} onDismiss={dismissToast} />
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="bg-bg-raised text-xs text-fg-dim">
            <tr>
              <th className="p-2 text-start">שאלה</th>
              <th className="p-2 text-start">מצב</th>
              <th className="p-2 text-start">סבבים</th>
              <th className="p-2 text-start">עמודים</th>
              <th className="p-2 text-start">תוצאה</th>
              <th className="p-2 text-start">התחיל</th>
            </tr>
          </thead>
          <tbody>
            {data.map((inv) => (
              <tr key={inv.job_id} className="border-t border-border hover:bg-bg-sunken">
                <td className="p-2">
                  <Link to={`/investigations/${inv.job_id}`} className="text-accent hover:underline">
                    <bdi>{inv.question}</bdi>
                  </Link>
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
