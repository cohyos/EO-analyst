import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/api";
import { cn } from "@/lib/cn";
import { countryFlagEmoji } from "@/lib/countryFlag";
import { formatDateTime } from "@/lib/time";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";
import type { TenderSourceCoverageItem, TenderSourceStatus } from "@/types/api";

// A15 (docs/TENDER_PORTALS.md): status -> badge color, mirrors the TENDER_STATUS_CHIP_CLASS
// convention in lib/tenders.ts (kept local since this is the only place these three statuses
// render as chips).
const STATUS_CHIP_CLASS: Record<TenderSourceStatus, string> = {
  integrated_keyless: "bg-ok/15 text-ok",
  waiting_for_key: "bg-warn/15 text-warn",
  not_integrated: "bg-bg-sunken text-fg-dim",
};

// t()'s TranslationKey type is a literal union derived from the dictionary shape (i18n/types.ts) --
// a dynamically-built template string wouldn't type-check, so each status maps to its own literal
// key here instead (still fully translated, just via a lookup rather than string interpolation).
const STATUS_LABEL_KEY: Record<TenderSourceStatus, TranslationKey> = {
  integrated_keyless: "tenders.coverage.status.integratedKeyless",
  waiting_for_key: "tenders.coverage.status.waitingForKey",
  not_integrated: "tenders.coverage.status.notIntegrated",
};

const KIND_LABEL: Record<TenderSourceCoverageItem["kind"], string> = {
  api_json: "API",
  rss: "RSS/Atom",
  html: "HTML",
  search: "חיפוש",
};

function SourceRow({ source }: { source: TenderSourceCoverageItem }) {
  const t = useT();
  return (
    <tr className="border-t border-border">
      <td className="max-w-[16rem] truncate px-2 py-1.5 text-xs text-fg" title={source.name}>
        <bdi dir="auto">{source.name}</bdi>
      </td>
      <td className="px-2 py-1.5 text-xs text-fg-dim">{KIND_LABEL[source.kind]}</td>
      <td className="px-2 py-1.5">
        <span
          className={cn(
            "inline-block rounded-md px-1.5 py-0.5 text-[11px] font-medium",
            STATUS_CHIP_CLASS[source.status],
          )}
        >
          {t(STATUS_LABEL_KEY[source.status])}
        </span>
        {source.needs_key_env_var && (
          <span className="ms-1 font-mono text-[10px] text-fg-dim" dir="ltr">
            {source.needs_key_env_var}
          </span>
        )}
      </td>
      <td className="px-2 py-1.5 text-end text-xs font-mono text-fg-dim">{source.notices_stored}</td>
      <td className="px-2 py-1.5 text-end text-xs text-fg-dim">{formatDateTime(source.last_fetch_at)}</td>
    </tr>
  );
}

export function SourceCoveragePanel() {
  const t = useT();
  const [expanded, setExpanded] = useState(false);
  const query = useQuery({
    queryKey: ["tender-source-coverage"],
    queryFn: () => api.getTenderSourceCoverage(),
  });

  if (query.isLoading || query.isError || !query.data) return null;

  const { regions, totals, source_count } = query.data;
  const summaryChips: { key: string; label: string; count: number }[] = [
    { key: "integrated_keyless", label: t("tenders.coverage.integratedKeyless"), count: totals.integrated_keyless ?? 0 },
    { key: "search_only", label: t("tenders.coverage.searchOnly"), count: totals.search_only ?? 0 },
    { key: "waiting_for_key", label: t("tenders.coverage.waitingForKey"), count: totals.waiting_for_key ?? 0 },
    { key: "not_integrated", label: t("tenders.coverage.notIntegrated"), count: totals.not_integrated ?? 0 },
  ];

  return (
    <section className="rounded-lg border border-border bg-bg-raised" aria-label={t("tenders.coverage.title")}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        className="flex w-full flex-wrap items-center justify-between gap-2 rounded-lg px-3 py-2.5 text-start hover:bg-bg-sunken"
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-fg">{t("tenders.coverage.title")}</span>
          <span className="text-xs text-fg-dim">
            {t("tenders.coverage.sourceCount", { count: source_count })}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {summaryChips.map((c) => (
            <span
              key={c.key}
              className={cn(
                "rounded-md px-1.5 py-0.5 text-[11px] font-medium",
                STATUS_CHIP_CLASS[c.key as TenderSourceStatus] ?? "bg-bg-sunken text-fg-dim",
              )}
            >
              {c.label}: {c.count}
            </span>
          ))}
          {expanded ? (
            <ChevronUp size={16} className="text-fg-dim" aria-hidden="true" />
          ) : (
            <ChevronDown size={16} className="text-fg-dim" aria-hidden="true" />
          )}
        </div>
      </button>

      {expanded && (
        <div className="space-y-3 border-t border-border p-3">
          {regions.map((region) => (
            <div key={region.region} className="overflow-x-auto rounded-md border border-border">
              <div className="flex items-center gap-1.5 bg-bg-sunken px-2 py-1 text-xs font-medium text-fg">
                <span aria-hidden="true">{countryFlagEmoji(region.region)}</span>
                <span dir="ltr">{region.region}</span>
                <span className="text-fg-dim">
                  ({t("tenders.coverage.sourceCount", { count: region.sources.length })})
                </span>
              </div>
              <table className="w-full min-w-[28rem] border-collapse">
                <thead>
                  <tr className="text-start text-[11px] text-fg-dim">
                    <th className="px-2 py-1 text-start font-normal">{t("tenders.coverage.colSource")}</th>
                    <th className="px-2 py-1 text-start font-normal">{t("tenders.coverage.colAccess")}</th>
                    <th className="px-2 py-1 text-start font-normal">{t("tenders.coverage.colStatus")}</th>
                    <th className="px-2 py-1 text-end font-normal">{t("tenders.coverage.colNotices")}</th>
                    <th className="px-2 py-1 text-end font-normal">{t("tenders.coverage.colLastFetch")}</th>
                  </tr>
                </thead>
                <tbody>
                  {region.sources.map((source) => (
                    <SourceRow key={source.id} source={source} />
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
