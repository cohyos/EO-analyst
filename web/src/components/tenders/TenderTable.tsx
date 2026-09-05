import { Fragment } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ExternalLink } from "lucide-react";
import type { TenderCard } from "@/types/api";
import { countryFlagEmoji } from "@/lib/countryFlag";
import { formatDate } from "@/lib/time";
import { cn } from "@/lib/cn";
import {
  DEADLINE_URGENT_DAYS,
  TENDER_STATUS_CHIP_CLASS,
  TENDER_STATUS_LABEL,
  daysLeft,
} from "@/lib/tenders";
import { EmptyState } from "@/components/states";
import { useT } from "@/i18n";

function DeadlineChip({ deadline }: { deadline: string | null }) {
  if (!deadline) return <span className="text-fg-dim">—</span>;
  const days = daysLeft(deadline);
  const urgent = days != null && days < DEADLINE_URGENT_DAYS;
  const daysLabel =
    days == null ? "" : days < 0 ? `עברו ${Math.abs(days)} ימים` : `נותרו ${days} ימים`;
  return (
    <span
      className={cn(
        "inline-flex flex-col items-start gap-0.5 rounded-md px-1.5 py-0.5 text-xs",
        urgent ? "bg-danger/15 text-danger" : "bg-bg-sunken text-fg-dim",
      )}
      title={daysLabel}
    >
      <span className="font-mono">{formatDate(deadline)}</span>
      {daysLabel && <span className="font-mono">{daysLabel}</span>}
    </span>
  );
}

function RelevanceDots({ value }: { value: number | null }) {
  const v = Math.max(0, Math.min(5, value ?? 0));
  return (
    <span
      className="flex items-center gap-0.5"
      role="img"
      aria-label={`רלוונטיות ${v} מתוך 5`}
      title={`רלוונטיות: ${v}/5`}
    >
      {[1, 2, 3, 4, 5].map((i) => (
        <span
          key={i}
          className={cn("h-1.5 w-1.5 rounded-full", i <= v ? "bg-accent" : "bg-border-strong")}
        />
      ))}
    </span>
  );
}

function TenderDetailRow({ t }: { t: TenderCard }) {
  const translate = useT();
  const whyRelevant = [t.matched_terms.join(", "), t.summary_he].filter(Boolean).join(" — ");
  return (
    <tr className="border-t border-border bg-bg-sunken/60">
      <td colSpan={8} className="p-3 text-xs">
        {whyRelevant && (
          <p className="mb-2">
            <span className="text-fg-dim">{translate("tenders.whyRelevantPrefix")}</span>
            <bdi dir="auto">{whyRelevant}</bdi>
          </p>
        )}
        <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-fg-dim">
          <span>
            פורסם: <span className="font-mono text-fg">{formatDate(t.published_at)}</span>
          </span>
          <span>
            גוף מזמין: <bdi className="text-fg">{t.agency ?? "—"}</bdi>
          </span>
          <span>
            מדינה: <span className="font-mono text-fg">{t.country ?? "—"}</span>
          </span>
        </div>
        <div className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
          {t.entities.length > 0 && (
            <div>
              <span className="text-fg-dim">ישויות: </span>
              <span className="inline-flex flex-wrap gap-1">
                {t.entities.map((e) => (
                  <span key={e} className="rounded-full bg-bg-raised px-2 py-0.5">
                    <bdi>{e}</bdi>
                  </span>
                ))}
              </span>
            </div>
          )}
          {t.cpv_naics.length > 0 && (
            <div>
              <span className="text-fg-dim">CPV/NAICS: </span>
              <span className="font-mono">{t.cpv_naics.join(", ")}</span>
            </div>
          )}
        </div>
        {t.item_id != null && (
          <Link
            to={`/items/${t.item_id}`}
            className="mt-2 inline-block text-accent hover:underline"
          >
            פתח פריט מקושר ←
          </Link>
        )}
      </td>
    </tr>
  );
}

export function TenderTable({
  tenders,
  expandedId,
  onToggleExpand,
}: {
  tenders: TenderCard[];
  expandedId: number | null;
  onToggleExpand: (id: number) => void;
}) {
  const t = useT();
  if (tenders.length === 0) {
    return (
      <EmptyState
        title={t("tenders.emptyFilteredTitle")}
        description={t("tenders.emptyFilteredDescription")}
      />
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[900px] text-sm">
        <thead className="bg-bg-raised text-xs text-fg-dim">
          <tr>
            <th className="p-2 text-start"></th>
            <th className="p-2 text-start">מועד סגירה</th>
            <th className="p-2 text-start">כותרת</th>
            <th className="p-2 text-start">גוף מזמין / מדינה</th>
            <th className="p-2 text-start">מקור</th>
            <th className="p-2 text-start">רלוונטיות</th>
            <th className="p-2 text-start">מונחים תואמים</th>
            <th className="p-2 text-start">סטטוס</th>
          </tr>
        </thead>
        <tbody>
          {tenders.map((t) => {
            const expanded = expandedId === t.id;
            return (
              <Fragment key={t.id}>
                <tr
                  onClick={() => onToggleExpand(t.id)}
                  className="cursor-pointer border-t border-border hover:bg-bg-sunken"
                  aria-expanded={expanded}
                >
                  <td className="p-2 text-fg-dim">
                    <ChevronDown
                      size={14}
                      className={cn("transition-transform", expanded && "rotate-180")}
                      aria-hidden="true"
                    />
                  </td>
                  <td className="p-2">
                    <DeadlineChip deadline={t.deadline} />
                  </td>
                  <td className="p-2">
                    {t.url ? (
                      <a
                        href={t.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-start gap-1 text-fg hover:text-accent hover:underline"
                      >
                        <bdi>{t.title || "(ללא כותרת)"}</bdi>
                        <ExternalLink size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
                      </a>
                    ) : (
                      <bdi>{t.title || "(ללא כותרת)"}</bdi>
                    )}
                  </td>
                  <td className="p-2 text-fg-muted">
                    <div className="flex items-center gap-1.5">
                      <span aria-hidden="true">{countryFlagEmoji(t.country)}</span>
                      <span className="font-mono text-xs">{t.country ?? "—"}</span>
                    </div>
                    <bdi className="block truncate text-xs">{t.agency ?? "—"}</bdi>
                  </td>
                  <td className="p-2 text-fg-muted">
                    <bdi>{t.source ?? "—"}</bdi>
                  </td>
                  <td className="p-2">
                    <RelevanceDots value={t.relevance} />
                  </td>
                  <td className="p-2">
                    <div className="flex flex-wrap gap-1">
                      {t.matched_terms.slice(0, 3).map((m) => (
                        <span
                          key={m}
                          className="rounded-full bg-bg-sunken px-1.5 py-0.5 text-xs text-fg-dim"
                        >
                          {m}
                        </span>
                      ))}
                      {t.matched_terms.length > 3 && (
                        <span className="text-xs text-fg-dim">+{t.matched_terms.length - 3}</span>
                      )}
                    </div>
                  </td>
                  <td className="p-2">
                    <span
                      className={cn(
                        "rounded-md px-1.5 py-0.5 text-xs font-medium",
                        TENDER_STATUS_CHIP_CLASS[t.status],
                      )}
                    >
                      {TENDER_STATUS_LABEL[t.status]}
                    </span>
                  </td>
                </tr>
                {expanded && <TenderDetailRow t={t} />}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
