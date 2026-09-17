import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useOutletContext } from "react-router-dom";
import {
  AlertOctagon,
  Clock,
  Cpu,
  Download,
  FileWarning,
  Gavel,
  Inbox,
  Search,
  TriangleAlert,
} from "lucide-react";
import { api } from "@/api";
import { ZERO_NIGHT_SUMMARY } from "@/api/normalize";
import { StatTile } from "@/components/StatTile";
import { LevelBadge } from "@/components/LevelBadge";
import { LoadingState, ErrorState, EmptyState } from "@/components/states";
import { ReportBody } from "@/components/reports/ReportBody";
import { NowRunningStrip } from "@/components/morning/NowRunningStrip";
import { PipelineReplayTimeline } from "@/components/morning/PipelineReplayTimeline";
import { RunErrorsPanel } from "@/components/morning/RunErrorsPanel";
import { formatDateTime } from "@/lib/time";
import { DEADLINE_SOON_DAYS, daysLeft } from "@/lib/tenders";
import { useI18n } from "@/i18n";
import type { StatusSocketState } from "@/hooks/useStatusSocket";
import type { TechDailySummary } from "@/types/api";

const ONE_WEEK_MS = 7 * 24 * 60 * 60 * 1000;

function TendersTile() {
  const tendersQuery = useQuery({
    queryKey: ["tenders", "open"],
    queryFn: () => api.getTenders({ status: "open" }),
  });
  const forecastsQuery = useQuery({
    queryKey: ["tender-forecasts"],
    queryFn: () => api.getTenderForecasts(),
  });

  const openSoonCount = (tendersQuery.data?.tenders ?? []).filter((t) => {
    const days = daysLeft(t.deadline);
    return days != null && days <= DEADLINE_SOON_DAYS;
  }).length;

  const newForecastsCount = (forecastsQuery.data ?? []).filter((f) => {
    const created = new Date(f.created_at).getTime();
    return !Number.isNaN(created) && Date.now() - created <= ONE_WEEK_MS;
  }).length;

  const { t } = useI18n();
  return (
    <Link
      to="/tenders"
      aria-label={t("morning.tendersAria")}
      className="flex flex-col gap-1 rounded-lg border border-border bg-bg-raised p-3 shadow-panel hover:border-border-strong"
    >
      <div className="flex items-center gap-2 text-xs text-fg-dim">
        <Gavel size={14} aria-hidden="true" />
        <span>{t("morning.tendersLabel")}</span>
      </div>
      <p className="font-mono font-tabular text-2xl font-semibold text-fg">{openSoonCount}</p>
      <p className="text-xs text-fg-dim">
        מכרזים פתוחים ב-{DEADLINE_SOON_DAYS} הימים הקרובים · {newForecastsCount} תחזיות חדשות השבוע
      </p>
    </Link>
  );
}

// tech_daily (2026-09-17, user request -- daily EO/IR supply-chain technology-watch report):
// "טכנולוגיה היום" card, linking to the latest tech_daily report with its layers-with-news count.
// No own query -- `techDaily` comes straight off `GET /api/morning`'s own response, same as
// `report`/`headlines`/`night_summary` below (unlike `TendersTile`, which fetches independently).
function TechDailyTile({ techDaily }: { techDaily: TechDailySummary | null }) {
  const { t } = useI18n();
  return (
    <Link
      to={techDaily ? `/reports?id=${techDaily.report_id}` : "/reports"}
      aria-label={t("morning.techDailyAria")}
      className="flex flex-col gap-1 rounded-lg border border-border bg-bg-raised p-3 shadow-panel hover:border-border-strong"
    >
      <div className="flex items-center gap-2 text-xs text-fg-dim">
        <Cpu size={14} aria-hidden="true" />
        <span>{t("morning.techDailyLabel")}</span>
      </div>
      <p className="font-mono font-tabular text-2xl font-semibold text-fg">
        {techDaily ? techDaily.layers_with_news_count : "—"}
      </p>
      <p className="text-xs text-fg-dim">
        {techDaily ? t("morning.techDailyLayersWithNews", { count: techDaily.layers_with_news_count }) : t("morning.techDailyNone")}
      </p>
    </Link>
  );
}

export function MorningPage() {
  const { t } = useI18n();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["morning"],
    queryFn: () => api.getMorning(),
  });
  const queryClient = useQueryClient();
  const [errorsPanelOpen, setErrorsPanelOpen] = useState(false);
  // Provided by AppShell via <Outlet context={...}> (a single shared
  // WS /ws/status connection) -- undefined when this page renders without
  // that ancestor (e.g. a unit test rendering <MorningPage /> directly), in
  // which case the pipeline replay/"now running" sections simply don't render.
  const statusState = useOutletContext<StatusSocketState | undefined>();
  const pipeline = useMemo(() => statusState?.status?.pipeline ?? null, [statusState]);

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
  const recent_errors = data.recent_errors ?? [];

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-4 md:p-6">
      {pipeline && <NowRunningStrip pipeline={pipeline} />}
      {pipeline?.last_run && (
        <div id="pipeline-replay">
          <PipelineReplayTimeline lastRun={pipeline.last_run} />
        </div>
      )}

      {errorsPanelOpen && (
        <RunErrorsPanel errors={recent_errors} onClose={() => setErrorsPanelOpen(false)} />
      )}

      <section aria-label="מכרזים ו-RFI/RFP וטכנולוגיה" className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <TendersTile />
        <TechDailyTile techDaily={data.tech_daily ?? null} />
      </section>

      {/* U2 (docs/REVIEW_2026-09-05.md): every KPI card is clickable and navigates to (or, for
          errors, opens a drawer onto) its filtered view — they used to go nowhere.
          Q5-10 (docs/qa/findings_Q5_r2.md): red/orange also carry `since=24h` so the feed count
          the analyst lands on matches the KPI card's own last-24h window (both now read
          `COALESCE(fetched_at, created_at)` — see `list_items` in eoa/api/services.py). */}
      <section aria-label="תקציר הלילה" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <p className="col-span-full text-xs text-fg-dim">פריטים, חקירות ושגיאות: 24 השעות האחרונות. משך הריצה מתייחס לריצה האחרונה המוצגת לעיל.</p>
        <StatTile
          label={t("morning.itemsIngestedLabel")}
          value={night_summary.items_ingested}
          icon={<Inbox size={14} />}
          to="/feed?since=24h"
          ariaLabel={t("morning.itemsIngestedAria")}
        />
        <StatTile
          label={t("morning.redLabel")}
          value={night_summary.red}
          tone="danger"
          icon={<AlertOctagon size={14} />}
          to="/feed?level=red&since=24h"
          ariaLabel={t("morning.redAria")}
        />
        <StatTile
          label={t("morning.orangeLabel")}
          value={night_summary.orange}
          tone="warn"
          icon={<TriangleAlert size={14} />}
          to="/feed?level=orange&since=24h"
          ariaLabel={t("morning.orangeAria")}
        />
        <StatTile
          label={t("morning.deepSearchesLabel")}
          value={night_summary.deep_searches}
          icon={<Search size={14} />}
          to="/investigations"
          ariaLabel={t("morning.deepSearchesAria")}
        />
        <StatTile
          label={t("morning.durationLabel")}
          value={night_summary.duration_min != null ? `${night_summary.duration_min} דק׳` : "—"}
          icon={<Clock size={14} />}
        />
        <StatTile
          label={t("morning.errorsLabel")}
          value={t("morning.errorsCount", { count: night_summary.errors })}
          tone={night_summary.errors > 0 ? "danger" : "ok"}
          icon={<FileWarning size={14} />}
          onClick={() => setErrorsPanelOpen(true)}
          ariaLabel={t("morning.errorsAria")}
        />
      </section>

      {report ? (
        <section className="rounded-lg border border-border bg-bg-raised p-4 shadow-panel">
          {Date.now() - Date.parse(report.created_at) > 24 * 60 * 60 * 1000 && (
            <p role="status" className="mb-3 rounded border border-warn/40 p-2 text-sm text-warn">
              הדוח האחרון בן יותר מ־24 שעות. מועד הפקתו: {formatDateTime(report.created_at)}.
            </p>
          )}
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold text-fg-dim">תקציר מנהלים — {formatDateTime(report.created_at)}</h2>
            <div className="flex items-center gap-2">
              <a
                href={api.getReportFileUrl(report.id, "docx")}
                className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90"
              >
                <Download size={14} aria-hidden="true" />
                docx
              </a>
              <a
                href={api.getReportFileUrl(report.id, "html")}
                className="flex items-center gap-1.5 rounded-md border border-border-strong px-3 py-1.5 text-sm text-fg-dim hover:bg-bg-sunken"
              >
                <Download size={14} aria-hidden="true" />
                html
              </a>
            </div>
          </div>
          <ReportBody html={report.html ?? ""} reportId={report.id} className="report-body text-sm text-fg" />
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
                    {/* Mobile fix (UI-MOBILE-iphone.md #2): a single-line `truncate` clipped the
                        headline itself -- the point of this card -- down to a sliver on phones.
                        2-line clamp keeps it readable at any width instead of cutting it off. */}
                    <bdi className="block line-clamp-2 font-medium" title={h.title ?? undefined}>
                      {h.title}
                    </bdi>
                    <bdi className="block truncate text-sm text-fg-muted" title={h.summary_he ?? undefined}>
                      {h.summary_he}
                    </bdi>
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
