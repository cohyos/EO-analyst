import { Link } from "react-router-dom";
import type { ReportSummary } from "@/types/api";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";
import { EmptyState } from "@/components/states";
import { useT } from "@/i18n";

/**
 * PL-ui (2026-09-07): "reports (reuse report cards with preview/open)" -- a lighter-weight sibling
 * of `ReportsPage`'s own `ReportRow` (title_he + preview + QA/source/headline/built-at chips),
 * each row linking to `/reports?id=<id>` so "open" always lands on the same full report viewer
 * (`ReportBody`, download links, TOC) the Reports/BD pages already use, rather than a duplicate one.
 */
export function ProductLineReportList({ reports }: { reports: ReportSummary[] }) {
  const t = useT();
  if (reports.length === 0) {
    return <EmptyState title={t("productLines.noReports")} />;
  }
  return (
    <ul className="divide-y divide-border rounded-lg border border-border" data-testid="product-line-report-list">
      {reports.map((r) => (
        <li key={r.id}>
          <Link
            to={`/reports?id=${r.id}`}
            className="flex flex-col items-start gap-1 px-3 py-2 text-start hover:bg-bg-sunken"
          >
            <span className="text-sm font-medium text-fg">{r.title_he}</span>
            {r.preview_he && (
              <span className="line-clamp-1 max-w-full text-xs text-fg-dim">{r.preview_he}</span>
            )}
            <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-fg-dim">
              <span
                className={cn(
                  "rounded bg-bg-sunken px-1.5 py-0.5 font-medium",
                  r.qa_passed ? "text-fg-dim" : "text-danger",
                )}
              >
                {r.qa_passed ? "QA ✓" : "QA ✗"}
              </span>
              <span className="rounded bg-bg-sunken px-1.5 py-0.5">{r.source_count} מקורות</span>
              <span className="rounded bg-bg-sunken px-1.5 py-0.5">{formatDateTime(r.built_at)}</span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
  );
}
