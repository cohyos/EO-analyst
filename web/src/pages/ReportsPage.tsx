import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Download } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { linkifyReportCitations } from "@/lib/reportHtml";
import { formatDate, formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";

const KIND_LABEL: Record<string, string> = {
  daily: "יומי",
  weekly: "שבועי",
  ad_hoc: "אד-הוק",
};

function addHeadingIds(html: string): { html: string; toc: { id: string; text: string }[] } {
  let i = 0;
  const toc: { id: string; text: string }[] = [];
  const withIds = html.replace(/<h([23])>(.*?)<\/h\1>/g, (_m, level, text) => {
    i += 1;
    const id = `section-${i}`;
    toc.push({ id, text: text.replace(/<[^>]+>/g, "") });
    return `<h${level} id="${id}">${text}</h${level}>`;
  });
  return { html: withIds, toc };
}

export function ReportsPage() {
  const [kind, setKind] = useState("");
  const [searchParams, setSearchParams] = useSearchParams();
  const selectedId = searchParams.get("id");

  const listQuery = useQuery({
    queryKey: ["reports", kind],
    queryFn: () => api.getReports(kind || undefined),
  });

  const detailQuery = useQuery({
    queryKey: ["report", selectedId],
    queryFn: () => api.getReport(Number(selectedId)),
    enabled: !!selectedId,
  });

  const processed = useMemo(
    () =>
      detailQuery.data
        ? addHeadingIds(
            linkifyReportCitations(
              detailQuery.data.html ?? "",
              detailQuery.data.items_included ?? [],
            ),
          )
        : null,
    [detailQuery.data],
  );

  return (
    <div className="flex h-full flex-col md:flex-row">
      <div className="w-full shrink-0 border-b border-border md:w-72 md:border-b-0 md:border-l">
        <div className="border-b border-border p-3">
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value)}
            className="w-full rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm"
            aria-label="סינון לפי סוג דוח"
          >
            <option value="">כל הסוגים</option>
            <option value="daily">יומי</option>
            <option value="weekly">שבועי</option>
          </select>
        </div>
        {listQuery.isLoading && <LoadingState label="טוען דוחות…" />}
        {listQuery.isError && <ErrorState onRetry={() => listQuery.refetch()} />}
        {listQuery.data && listQuery.data.length === 0 && (
          <EmptyState title="אין דוחות" />
        )}
        <ul className="max-h-[70vh] overflow-y-auto">
          {listQuery.data?.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => setSearchParams({ id: String(r.id) })}
                className={cn(
                  "flex w-full flex-col items-start gap-0.5 border-b border-border px-3 py-2 text-start hover:bg-bg-sunken",
                  selectedId === String(r.id) && "bg-accent-muted/60",
                )}
              >
                <span className="text-sm font-medium">
                  {KIND_LABEL[r.kind] ?? r.kind} — {formatDate(r.period_end)}
                </span>
                <span className="text-xs text-fg-dim">
                  {r.headline_count ?? 0} כותרות · {r.qa_passed ? "QA עבר" : "QA נכשל"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {!selectedId && <EmptyState title="בחר דוח מהרשימה" />}
        {selectedId && detailQuery.isLoading && <LoadingState label="טוען דוח…" />}
        {selectedId && detailQuery.isError && <ErrorState onRetry={() => detailQuery.refetch()} />}
        {detailQuery.data && processed && (
          <div className="mx-auto flex max-w-4xl gap-6 p-4 md:p-6">
            <article className="min-w-0 flex-1">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-semibold text-fg-dim">
                  {formatDateTime(detailQuery.data.created_at)}
                </h2>
                <div className="flex gap-2">
                  <a
                    href={api.getReportFileUrl(detailQuery.data.id, "docx")}
                    className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-accent-fg hover:opacity-90"
                  >
                    <Download size={13} aria-hidden="true" />
                    docx
                  </a>
                  <a
                    href={api.getReportFileUrl(detailQuery.data.id, "md")}
                    className="flex items-center gap-1.5 rounded-md border border-border-strong px-3 py-1.5 text-xs hover:bg-bg-sunken"
                  >
                    <Download size={13} aria-hidden="true" />
                    md
                  </a>
                </div>
              </div>
              <div
                className="report-body text-sm"
                dangerouslySetInnerHTML={{ __html: processed.html }}
              />
            </article>
            {processed.toc.length > 0 && (
              <nav aria-label="תוכן עניינים" className="hidden w-48 shrink-0 lg:block">
                <p className="mb-2 text-xs font-semibold text-fg-dim">תוכן עניינים</p>
                <ul className="space-y-1 text-sm">
                  {processed.toc.map((t) => (
                    <li key={t.id}>
                      <a href={`#${t.id}`} className="text-accent hover:underline">
                        {t.text}
                      </a>
                    </li>
                  ))}
                </ul>
              </nav>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
