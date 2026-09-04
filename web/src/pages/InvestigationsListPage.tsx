import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";

const STATE_LABEL: Record<string, string> = {
  queued: "בתור",
  running: "רץ",
  done: "הושלם",
  stopped: "נעצר",
  error: "שגיאה",
  not_found: "לא נמצא",
};

const STATE_TONE: Record<string, string> = {
  queued: "text-fg-dim bg-bg-sunken",
  running: "text-accent bg-accent-muted",
  done: "text-ok bg-level-yellow-bg",
  stopped: "text-warn bg-level-orange-bg",
  error: "text-danger bg-level-red-bg",
  not_found: "text-fg-dim bg-bg-sunken",
};

export function InvestigationsListPage() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["investigations"],
    queryFn: () => api.getInvestigations(30),
    refetchInterval: 5000,
  });

  if (isLoading) return <LoadingState label="טוען חקירות…" />;
  if (isError) return <ErrorState onRetry={() => refetch()} />;
  if (!data || data.length === 0)
    return <EmptyState title="אין חקירות עומק" description="חקירה נפתחת מפריט בפיד או משאלה ישירה." />;

  return (
    <div className="p-4 md:p-6">
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
                <td className="p-2 text-fg-muted">{inv.outcome ?? "—"}</td>
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
