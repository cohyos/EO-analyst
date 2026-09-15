import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronDown, ChevronUp, Download } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { ReportBody } from "@/components/reports/ReportBody";
import { formatDateTime } from "@/lib/time";
import { decodeHtmlEntities } from "@/lib/reportHtml";
import { outcomeLabel, outcomeTone } from "@/lib/investigations";
import { cn } from "@/lib/cn";
import { useIsNarrowViewport } from "@/hooks/useIsNarrowViewport";
import type { ReportSummary } from "@/types/api";

const KIND_LABEL: Record<string, string> = {
  daily: "יומי",
  weekly: "שבועי",
  monthly: "חודשי",
  bd_territory: "פיתוח עסקי",
  // PL-ui (2026-09-07): reports queued from `ProductLinesPage`/`ProductLineDetailPage`'s
  // "צור דוח" -- see docs/qa/loop/round_7_fixes.md "### PL-ui status".
  product_line: "קו מוצר",
  patent_survey: "סקר פטנטים",
  adhoc: "אד-הוק",
};

// W14 (docs/REVIEW_2026-09-06_evening.md, user finding 2026-09-06 19:10): "seven patent_survey
// rows for two topics" -- reports sharing a `group_key` (server-computed: kind+subject, or
// kind+period for daily/weekly/monthly) are different runs/versions of the same report. Only the
// newest (`is_latest`) row per group shows by default; the rest fold behind "גרסאות קודמות (N)".
interface ReportGroup {
  key: string;
  latest: ReportSummary;
  older: ReportSummary[];
}

function groupReports(reports: ReportSummary[]): ReportGroup[] {
  const byKey = new Map<string, ReportSummary[]>();
  for (const r of reports) {
    const arr = byKey.get(r.group_key);
    if (arr) arr.push(r);
    else byKey.set(r.group_key, [r]);
  }
  const groups: ReportGroup[] = [];
  for (const [key, rows] of byKey) {
    // `reports` arrives created_at DESC from the server, so the first row of each group in
    // insertion order is already its newest -- fall back to it if `is_latest` is ever missing.
    const latest = rows.find((r) => r.is_latest) ?? rows[0];
    const older = rows.filter((r) => r.id !== latest.id);
    groups.push({ key, latest, older });
  }
  return groups;
}

// W14 point 4: title_he + a one-line preview + chips (QA ✓/✗, N מקורות, N כותרות, built time),
// with a hover/focus tooltip surfacing the full preview text. `sub` renders the smaller, indented
// style used for older versions inside a group's "גרסאות קודמות" expander.
function ReportRow({
  report,
  isSelected,
  onSelect,
  sub = false,
}: {
  report: ReportSummary;
  isSelected: boolean;
  onSelect: () => void;
  sub?: boolean;
}) {
  const previewId = `report-preview-${report.id}`;
  return (
    <button
      type="button"
      onClick={onSelect}
      title={report.preview_he ?? undefined}
      aria-describedby={report.preview_he ? previewId : undefined}
      aria-label={`${report.title_he}${report.preview_he ? ` — ${report.preview_he}` : ""} — ${
        report.qa_passed ? "QA עבר" : "QA נכשל"
      }`}
      className={cn(
        "group relative flex w-full flex-col items-start gap-1 border-b border-border px-3 py-2 text-start hover:bg-bg-sunken",
        sub ? "ps-6 py-1.5" : "py-2",
        isSelected && "bg-accent-muted/60",
      )}
    >
      <span className={cn("font-medium", sub ? "text-xs text-fg-dim" : "text-sm")}>
        {report.title_he}
      </span>
      {report.preview_he && (
        <span className="line-clamp-1 max-w-full text-xs text-fg-dim">{report.preview_he}</span>
      )}
      <span className="flex flex-wrap items-center gap-1.5 text-xs text-fg-dim">
        <span
          className={cn(
            "rounded bg-bg-sunken px-1.5 py-0.5 font-medium",
            report.qa_passed ? "text-fg-dim" : "text-danger",
          )}
        >
          {report.qa_passed ? "QA ✓" : "QA ✗"}
        </span>
        <span className="rounded bg-bg-sunken px-1.5 py-0.5">{report.source_count} מקורות</span>
        <span className="rounded bg-bg-sunken px-1.5 py-0.5">{report.headline_count} כותרות</span>
        <span className="rounded bg-bg-sunken px-1.5 py-0.5">{formatDateTime(report.built_at)}</span>
      </span>
      {report.preview_he && (
        <span
          id={previewId}
          role="tooltip"
          className="pointer-events-none absolute inset-x-2 top-full z-10 mt-1 hidden rounded-md border border-border-strong bg-bg-raised p-2 text-xs text-fg shadow-panel group-focus-within:block group-hover:block"
        >
          {report.preview_he}
        </span>
      )}
    </button>
  );
}

function ReportGroupRow({
  group,
  selectedId,
  onSelect,
}: {
  group: ReportGroup;
  selectedId: string | null;
  onSelect: (id: number) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const olderListId = `report-older-${group.key.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  return (
    <li>
      <ReportRow
        report={group.latest}
        isSelected={selectedId === String(group.latest.id)}
        onSelect={() => onSelect(group.latest.id)}
      />
      {group.older.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-controls={olderListId}
            className="flex w-full items-center gap-1 border-b border-border bg-bg-sunken/40 px-3 py-1 text-xs text-fg-dim hover:bg-bg-sunken"
          >
            {expanded ? <ChevronUp size={12} aria-hidden="true" /> : <ChevronDown size={12} aria-hidden="true" />}
            גרסאות קודמות ({group.older.length})
          </button>
          {expanded && (
            <ul id={olderListId}>
              {group.older.map((r) => (
                <li key={r.id}>
                  <ReportRow
                    report={r}
                    isSelected={selectedId === String(r.id)}
                    onSelect={() => onSelect(r.id)}
                    sub
                  />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </li>
  );
}

function addHeadingIds(html: string): {
  html: string;
  toc: { id: string; text: string }[];
} {
  let i = 0;
  const toc: { id: string; text: string }[] = [];
  const withIds = html.replace(/<h([23])>(.*?)<\/h\1>/g, (_m, level, text) => {
    i += 1;
    const id = `section-${i}`;
    toc.push({ id, text: decodeHtmlEntities(text.replace(/<[^>]+>/g, "")) });
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

  const groups = useMemo(() => groupReports(listQuery.data ?? []), [listQuery.data]);

  const detailQuery = useQuery({
    queryKey: ["report", selectedId],
    queryFn: () => api.getReport(Number(selectedId)),
    enabled: !!selectedId,
  });

  // R10-links: the investigations this report's own "חקירות עומק" section actually cites --
  // a separate call (not embedded in ReportDetail) so it stays out of `services.py`'s scope.
  const investigationsQuery = useQuery({
    queryKey: ["report-investigations", selectedId],
    queryFn: () => api.getReportInvestigations(Number(selectedId)),
    enabled: !!selectedId,
  });

  // Heading ids (for the TOC) are added to the raw server HTML; citation
  // `[n]` markers are linkified separately, inside <ReportBody>, so this
  // effect never double-wraps an already-linkified `[n]` token.
  const processed = useMemo(
    () => (detailQuery.data ? addHeadingIds(detailQuery.data.html ?? "") : null),
    [detailQuery.data],
  );

  // Round-2 mobile fix (UI-MOBILE-iphone.md #1): a nested `overflow-y-auto` scroll container for
  // the detail pane collapsed to near-zero height on phones (a 1-line sliver of the title, then
  // blank space -- the report body itself never became visible even though it rendered fine into
  // the DOM). Below `md`, once a report is selected: hide the list entirely (its own scroll
  // container was part of the same squeeze) and let the detail pane grow to its natural height so
  // the page's own `<main>` (AppShell, `overflow-y-auto`) is the single scroll container instead
  // of a second one nested inside it. `md:`+ keeps the original two-pane, independently-scrolling
  // split unchanged.
  const isMobile = useIsNarrowViewport(768);
  const showList = !isMobile || !selectedId;

  return (
    <div className="flex h-full flex-col md:flex-row">
      {showList && (
        <div className="w-full shrink-0 border-b border-border md:w-72 md:border-b-0 md:border-l">
          <div className="border-b border-border p-3">
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value)}
              className="w-full rounded-md border border-border-strong bg-bg px-2 py-1.5 text-sm"
              aria-label="סינון לפי סוג דוח"
            >
              <option value="">כל הסוגים</option>
              {Object.entries(KIND_LABEL).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </div>
          {listQuery.isLoading && <LoadingState label="טוען דוחות…" />}
          {listQuery.isError && (
            <ErrorState error={listQuery.error} onRetry={() => listQuery.refetch()} />
          )}
          {listQuery.data && listQuery.data.length === 0 && (
            <EmptyState title="אין דוחות" />
          )}
          {/* Mobile fix (UI-MOBILE-iphone.md #6): on phones this list stacks above the detail pane
              (flex-col, not the desktop side-by-side flex-row) -- capped at 70vh here it could eat
              almost the entire viewport by itself, squeezing the `flex-1` detail pane below it down
              to near-zero height once a report was selected (the report body never became visible,
              "stayed on the list"). A much shorter cap on phones leaves real room for the detail
              pane; `md:max-h-[70vh]` restores the original desktop sizing unchanged. */}
          <ul className="max-h-48 overflow-y-auto md:max-h-[70vh]">
            {groups.map((group) => (
              <ReportGroupRow
                key={group.key}
                group={group}
                selectedId={selectedId}
                onSelect={(id) => setSearchParams({ id: String(id) })}
              />
            ))}
          </ul>
        </div>
      )}

      <div className={cn("min-w-0 flex-1", !isMobile && "min-h-0 overflow-y-auto")}>
        {!selectedId && <EmptyState title="בחר דוח מהרשימה" />}
        {selectedId && detailQuery.isLoading && <LoadingState label="טוען דוח…" />}
        {selectedId && detailQuery.isError && (
          <ErrorState error={detailQuery.error} onRetry={() => detailQuery.refetch()} />
        )}
        {detailQuery.data && processed && (
          <div className="mx-auto flex max-w-4xl gap-6 p-4 md:p-6">
            <article className="min-w-0 flex-1">
              {isMobile && (
                <button
                  type="button"
                  onClick={() => setSearchParams({})}
                  className="mb-3 flex items-center gap-1 text-sm text-accent hover:underline"
                >
                  ← חזרה לרשימה
                </button>
              )}
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                <h2 className="text-sm font-semibold text-fg-dim">
                  {detailQuery.data.title_he} · {formatDateTime(detailQuery.data.created_at)}
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
                  <a
                    href={api.getReportFileUrl(detailQuery.data.id, "html")}
                    className="flex items-center gap-1.5 rounded-md border border-border-strong px-3 py-1.5 text-xs hover:bg-bg-sunken"
                  >
                    <Download size={13} aria-hidden="true" />
                    html
                  </a>
                </div>
              </div>
              <ReportBody html={processed.html} reportId={detailQuery.data.id} />
            </article>
            {(processed.toc.length > 0 || (investigationsQuery.data?.length ?? 0) > 0) && (
              <aside className="hidden w-48 shrink-0 space-y-4 lg:block">
                {processed.toc.length > 0 && (
                  <nav aria-label="תוכן עניינים">
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
                {/* R10-links: "חקירות בדוח" -- the investigations this report's own "חקירות עומק"
                    section rendered, each linking to its own detail page and (when it has one)
                    its trigger item. */}
                {(investigationsQuery.data?.length ?? 0) > 0 && (
                  <details open>
                    <summary className="mb-2 cursor-pointer select-none text-xs font-semibold text-fg-dim">
                      חקירות בדוח ({investigationsQuery.data!.length})
                    </summary>
                    <ul className="space-y-1.5 text-xs">
                      {investigationsQuery.data!.map((inv) => (
                        <li key={inv.job_id} className="rounded-md border border-border bg-bg-raised p-1.5">
                          <Link to={`/investigations/${inv.job_id}`} className="block text-accent hover:underline">
                            <bdi className="line-clamp-2">
                              {inv.question?.trim() || inv.trigger_title || `חקירה #${inv.job_id}`}
                            </bdi>
                          </Link>
                          <div className="mt-1 flex flex-wrap items-center gap-1">
                            {inv.outcome && (
                              <span className={cn("rounded-full px-1.5 py-0.5 text-xs font-medium", outcomeTone(inv.outcome))}>
                                {outcomeLabel(inv.outcome)}
                              </span>
                            )}
                            {inv.item_id != null && (
                              <Link to={`/items/${inv.item_id}`} className="text-xs text-fg-dim hover:underline">
                                פריט מקור
                              </Link>
                            )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </aside>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
