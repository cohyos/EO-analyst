import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  AlertOctagon,
  Clock,
  Download,
  FileWarning,
  Inbox,
  Search,
  TriangleAlert,
} from "lucide-react";
import { api } from "@/api";
import { ZERO_NIGHT_SUMMARY } from "@/api/normalize";
import { StatTile } from "@/components/StatTile";
import { LevelBadge } from "@/components/LevelBadge";
import { LoadingState, ErrorState, EmptyState } from "@/components/states";
import { linkifyReportCitations } from "@/lib/reportHtml";
import { formatDateTime } from "@/lib/time";

export function MorningPage() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["morning"],
    queryFn: () => api.getMorning(),
  });
  const queryClient = useQueryClient();

  const answerClarification = useMutation({
    mutationFn: ({ id, answer }: { id: number; answer: string }) =>
      api.postClarificationAnswer(id, answer),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["morning"] }),
  });

  if (isLoading) return <LoadingState label="טוען את דוח הבוקר…" />;
  if (isError) return <ErrorState onRetry={() => refetch()} />;
  if (!data) return null;

  const report = data.report ?? null;
  const headlines = data.headlines ?? [];
  const open_points = data.open_points ?? [];
  const night_summary = data.night_summary ?? ZERO_NIGHT_SUMMARY;

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4 md:p-6">
      <section aria-label="תקציר הלילה" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="פריטים שנקלטו" value={night_summary.items_ingested} icon={<Inbox size={14} />} />
        <StatTile
          label="קריטי"
          value={night_summary.red}
          tone="danger"
          icon={<AlertOctagon size={14} />}
        />
        <StatTile
          label="חשוב"
          value={night_summary.orange}
          tone="warn"
          icon={<TriangleAlert size={14} />}
        />
        <StatTile
          label="חקירות עומק"
          value={night_summary.deep_searches}
          icon={<Search size={14} />}
        />
        <StatTile
          label="משך ריצה"
          value={`${night_summary.duration_min} דק׳`}
          icon={<Clock size={14} />}
        />
        <StatTile
          label="שגיאות"
          value={night_summary.errors}
          tone={night_summary.errors > 0 ? "danger" : "default"}
          icon={<FileWarning size={14} />}
        />
      </section>

      {report ? (
        <section className="rounded-lg border border-border bg-bg-raised p-4 shadow-panel">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-fg-dim">תקציר מנהלים — {formatDateTime(report.created_at)}</h2>
            <a
              href={api.getReportFileUrl(report.id, "docx")}
              className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90"
            >
              <Download size={14} aria-hidden="true" />
              פתח דוח docx
            </a>
          </div>
          <div
            className="report-body text-sm text-fg"
            dangerouslySetInnerHTML={{
              __html: linkifyReportCitations(report.html ?? "", report.items_included ?? []),
            }}
          />
        </section>
      ) : (
        <EmptyState title="אין ריצה לילית עדיין" description="הריצה הלילית טרם הושלמה." />
      )}

      <section aria-label="כותרות עיקריות">
        <h2 className="mb-2 text-sm font-semibold text-fg-dim">3 כותרות</h2>
        {headlines.length === 0 ? (
          <EmptyState title="אין פריטים" description="לא נמצאו כותרות מהריצה האחרונה." />
        ) : (
          <ul className="space-y-2">
            {headlines.map((h) => (
              <li key={h.item_id}>
                <Link
                  to={`/feed?open=${h.item_id}`}
                  className="flex items-center gap-3 rounded-lg border border-border bg-bg-raised p-3 hover:border-border-strong"
                >
                  <LevelBadge level={h.level ?? "yellow"} size="sm" />
                  <div className="min-w-0 flex-1">
                    <bdi className="block truncate font-medium">{h.title}</bdi>
                    <bdi className="block truncate text-sm text-fg-muted">{h.summary_he}</bdi>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="מה דורש הכרעה">
        <h2 className="mb-2 text-sm font-semibold text-fg-dim">מה דורש הכרעה</h2>
        {open_points.length === 0 ? (
          <EmptyState title="אין נקודות פתוחות" description="כל ההבהרות טופלו." />
        ) : (
          <ul className="space-y-2">
            {open_points.map((op) => (
              <li key={op.id} className="rounded-lg border border-border bg-bg-raised p-3">
                <p className="mb-2 text-sm">{op.question}</p>
                {op.answer ? (
                  <p className="text-sm text-ok">
                    נענה: {op.answer} {op.assumed && "(הנחת עבודה)"}
                  </p>
                ) : (
                  <div className="flex flex-wrap gap-2">
                    {(op.options ?? ["כן", "לא"]).map((opt) => (
                      <button
                        key={opt}
                        type="button"
                        onClick={() => answerClarification.mutate({ id: op.id, answer: opt })}
                        disabled={answerClarification.isPending}
                        className="rounded-md border border-border-strong px-2.5 py-1 text-xs hover:bg-bg-sunken disabled:opacity-50"
                      >
                        {opt}
                      </button>
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
