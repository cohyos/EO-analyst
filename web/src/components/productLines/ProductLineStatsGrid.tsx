import type { ProductLineStats } from "@/types/api";
import { useT } from "@/i18n";

/**
 * PL-ui (2026-09-07): compact KPI row for a `ProductLineCard` -- items 7d/30d, open tenders,
 * active competitors, patents 90d (the five stats the task brief calls out explicitly for the
 * card; `events_30d`/`forecasts` are additionally shown on the full detail page's `StatTile` row).
 */
export function ProductLineStatsGrid({ stats }: { stats: ProductLineStats }) {
  const t = useT();
  const entries: Array<{ key: string; label: string; value: number }> = [
    { key: "items7d", label: t("productLines.stats.items7d"), value: stats.items_7d },
    { key: "items30d", label: t("productLines.stats.items30d"), value: stats.items_30d },
    { key: "openTenders", label: t("productLines.stats.openTenders"), value: stats.open_tenders },
    {
      key: "activeCompetitors",
      label: t("productLines.stats.activeCompetitors"),
      value: stats.active_competitors,
    },
    { key: "patents90d", label: t("productLines.stats.patents90d"), value: stats.patents_90d },
  ];
  return (
    <dl className="grid grid-cols-3 gap-2 sm:grid-cols-5" data-testid="product-line-stats-grid">
      {entries.map((e) => (
        <div
          key={e.key}
          className="flex flex-col items-start gap-0.5 rounded-md bg-bg-sunken px-2 py-1.5"
        >
          <dt className="text-[10px] text-fg-dim">{e.label}</dt>
          <dd className="font-mono font-tabular text-sm font-semibold text-fg">{e.value}</dd>
        </div>
      ))}
    </dl>
  );
}
