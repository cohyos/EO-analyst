import { ContentShareActions } from "@/components/ContentShareActions";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { RadarMatrix } from "@/components/tech/RadarMatrix";
import { TechItemsList } from "@/components/tech/TechItemsList";
import type { TechActorKind, TechMaturity } from "@/types/api";

const WEEK_OPTIONS = [4, 12, 26, 52] as const;
const ACTOR_OPTIONS: { value: TechActorKind | ""; label: string }[] = [
  { value: "", label: "כל השחקנים" },
  { value: "academia", label: "אקדמיה" },
  { value: "lab", label: "מעבדה" },
  { value: "startup", label: "סטארטאפ" },
  { value: "prime", label: "יצרן ביטחוני" },
  { value: "government", label: "ממשלתי" },
];

/** A12 (מעקב טכנולוגי, 2026-09-06 -- user request re: פרסומים מדעיים על FPA עם פיקסל דיגיטלי):
 * "רדאר טכנולוגי" -- subdomain x maturity matrix (GET /api/tech/radar), momentum sparkline per
 * subdomain, and a click-through filtered item list (GET /api/tech/items). */
export function TechRadarPage() {
  const [weeks, setWeeks] = useState<number>(12);
  const [selectedSubdomain, setSelectedSubdomain] = useState<string | null>(null);
  const [selectedMaturity, setSelectedMaturity] = useState<TechMaturity | null>(null);
  const [actorKind, setActorKind] = useState<TechActorKind | "">("");

  const radarQuery = useQuery({
    queryKey: ["tech-radar", weeks],
    queryFn: () => api.getTechRadar(weeks),
  });

  const since = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - weeks * 7);
    return d.toISOString();
  }, [weeks]);

  const itemsQuery = useQuery({
    queryKey: ["tech-items", selectedSubdomain, selectedMaturity, actorKind, since],
    queryFn: () =>
      api.getTechItems({
        subdomain: selectedSubdomain ?? undefined,
        maturity: selectedMaturity ?? undefined,
        actor_kind: actorKind || undefined,
        since,
        page_size: 50,
      }),
    enabled: Boolean(selectedSubdomain) || Boolean(actorKind),
  });

  function handleCellClick(subdomain: string | null, maturity: TechMaturity | null) {
    setSelectedSubdomain(subdomain);
    setSelectedMaturity(maturity);
  }

  const hasAnySubdomainActivity = (radarQuery.data?.subdomains ?? []).some((s) => s.total > 0);

  return (
    <div data-share-content className="space-y-4 p-4 md:p-6">
      <ContentShareActions title="EO-Analyst" />
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-fg-muted">
          תקופה:
          <select
            value={weeks}
            onChange={(e) => setWeeks(Number(e.target.value))}
            className="max-w-full min-w-0 rounded-md border border-border bg-bg-raised px-2 py-1 text-sm text-fg"
          >
            {WEEK_OPTIONS.map((w) => (
              <option key={w} value={w}>
                {w} שבועות
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm text-fg-muted">
          שחקן:
          <select
            value={actorKind}
            onChange={(e) => setActorKind(e.target.value as TechActorKind | "")}
            className="max-w-full min-w-0 rounded-md border border-border bg-bg-raised px-2 py-1 text-sm text-fg"
          >
            {ACTOR_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {radarQuery.isLoading && <LoadingState label="טוען רדאר טכנולוגי…" />}
      {radarQuery.isError && <ErrorState onRetry={() => radarQuery.refetch()} />}
      {!radarQuery.isLoading && !radarQuery.isError && !hasAnySubdomainActivity && (
        <EmptyState
          title="אין עדיין נתוני מעקב טכנולוגי"
          description="המקורות האקדמיים/מקצועיים (arXiv, IEEE, כתבי עת) נסרקים במעקב הרגיל; פריטי tech_dev יופיעו כאן לאחר איסוף וסיווג."
        />
      )}
      {!radarQuery.isLoading && !radarQuery.isError && hasAnySubdomainActivity && (
        <RadarMatrix
          subdomains={radarQuery.data!.subdomains}
          maturities={radarQuery.data!.maturities}
          selectedSubdomain={selectedSubdomain}
          selectedMaturity={selectedMaturity}
          onCellClick={handleCellClick}
        />
      )}

      {(selectedSubdomain || actorKind) && (
        <div className="space-y-2">
          <h2 className="text-sm font-medium text-fg-dim">פריטים</h2>
          {itemsQuery.isLoading && <LoadingState label="טוען פריטים…" />}
          {itemsQuery.isError && <ErrorState onRetry={() => itemsQuery.refetch()} />}
          {!itemsQuery.isLoading && !itemsQuery.isError && (
            <TechItemsList items={itemsQuery.data?.items ?? []} />
          )}
        </div>
      )}
    </div>
  );
}
