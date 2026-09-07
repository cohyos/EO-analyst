import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Crosshair, Maximize2, X, EyeOff } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { LevelBadge } from "@/components/LevelBadge";
import {
  entityKindLabel,
  edgeLabelHe,
  eventKindLabel,
} from "@/components/entities/eventKindLabel";
import { countryFlagEmoji } from "@/lib/countryFlag";
import { countryLabel } from "@/lib/countries";
import { formatDate } from "@/lib/time";
import { productLineLabel } from "@/lib/productLines";
import type { GraphNodeStats, TriageLevel } from "@/types/api";

const INVESTIGATION_STATE_LABEL_HE: Record<string, string> = {
  queued: "בתור",
  running: "רץ",
  done: "הושלם",
  failed: "נכשל",
  stopped: "נעצר",
  error: "שגיאה",
  not_found: "לא נמצא",
};

const REPORT_KIND_LABEL_HE: Record<string, string> = {
  daily: "דוח יומי",
  weekly: "דוח שבועי",
  monthly: "דוח חודשי",
  adhoc: "דוח אד-הוק",
};

export function EntitySidePanel({
  node,
  onExpand,
  onCenter,
  onHide,
  onClose,
}: {
  /** The canvas's own `GraphNodeStats` for the selected node -- carries mention_count/
   * corroboration/product_lines, none of which `GET /api/entities/{id}/detail` repeats (it
   * reuses `services.get_entity`, which predates those fields). */
  node: GraphNodeStats;
  onExpand: (id: number) => void;
  onCenter: (id: number) => void;
  onHide: (id: number) => void;
  onClose: () => void;
}) {
  const nodeId = node.id;
  const q = useQuery({
    queryKey: ["graph-entity-detail", nodeId],
    queryFn: () => api.getEntityDetail(nodeId),
  });

  return (
    <aside
      aria-label="פרטי ישות נבחרת"
      className="flex h-full flex-col gap-3 overflow-y-auto rounded-lg border border-border bg-bg-raised p-3"
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg-dim">פרטי ישות</h3>
        <button
          type="button"
          onClick={onClose}
          aria-label="סגור פרטי ישות"
          className="tap-target inline-flex items-center justify-center text-fg-dim hover:text-fg"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>

      {q.isLoading && <LoadingState label="טוען פרטי ישות…" />}
      {q.isError && (
        <ErrorState onRetry={() => q.refetch()} message="לא ניתן לטעון את פרטי הישות" />
      )}

      {q.data && (
        <>
          <div>
            <div className="flex flex-wrap items-center gap-1.5">
              <h4 className="text-base font-semibold">
                <bdi>{q.data.name}</bdi>
              </h4>
              <span className="rounded bg-bg-sunken px-1.5 py-0.5 text-xs text-fg-dim">
                {entityKindLabel(q.data.kind)}
              </span>
              {q.data.country && (
                <span className="text-sm" title={countryLabel(q.data.country, "he")}>
                  {countryFlagEmoji(q.data.country)}
                </span>
              )}
            </div>
            {q.data.aliases.length > 0 && (
              <bdi className="mt-0.5 block text-xs text-fg-dim">
                כינויים: {q.data.aliases.join(", ")}
              </bdi>
            )}
          </div>

          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={() => onExpand(nodeId)}
              className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg-muted hover:bg-bg-sunken"
            >
              <Maximize2 size={12} aria-hidden="true" />
              הרחב שכנים
            </button>
            <button
              type="button"
              onClick={() => onCenter(nodeId)}
              className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg-muted hover:bg-bg-sunken"
            >
              <Crosshair size={12} aria-hidden="true" />
              מרכז
            </button>
            <button
              type="button"
              onClick={() => onHide(nodeId)}
              className="flex items-center gap-1 rounded-md border border-border-strong px-2 py-1 text-xs text-fg-muted hover:bg-bg-sunken"
            >
              <EyeOff size={12} aria-hidden="true" />
              הסתר
            </button>
            <Link
              to={`/entities/${nodeId}`}
              className="rounded-md border border-border-strong px-2 py-1 text-xs text-accent hover:bg-bg-sunken"
            >
              כרטיס מלא ←
            </Link>
          </div>

          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-md bg-bg-sunken p-1.5">
              <div className="text-fg-dim">אזכורים ב-7 ימים</div>
              <div className="font-mono text-sm">{q.data.mentions_7d}</div>
            </div>
            <div className="rounded-md bg-bg-sunken p-1.5">
              <div className="text-fg-dim">אזכורים ב-30 יום</div>
              <div className="font-mono text-sm">{q.data.mentions_30d}</div>
            </div>
          </div>

          <section aria-label="אימות צולב">
            <h5 className="mb-1 text-xs font-semibold text-fg-dim">
              אימות צולב (בין {node.mention_count} פריטים)
            </h5>
            <div className="flex flex-wrap gap-1.5 text-[11px]">
              {node.corroboration.corroborated > 0 && (
                <span className="rounded bg-level-green-bg px-1.5 py-0.5 text-level-green">
                  מאומת: {node.corroboration.corroborated}
                </span>
              )}
              {node.corroboration.official_primary > 0 && (
                <span className="rounded bg-level-green-bg px-1.5 py-0.5 text-level-green">
                  מקור רשמי: {node.corroboration.official_primary}
                </span>
              )}
              {node.corroboration.single_source > 0 && (
                <span className="rounded bg-level-orange-bg px-1.5 py-0.5 text-level-orange">
                  מקור יחיד: {node.corroboration.single_source}
                </span>
              )}
              {node.corroboration.unknown > 0 && (
                <span className="rounded bg-bg-sunken px-1.5 py-0.5 text-fg-dim">
                  טרם נבדק: {node.corroboration.unknown}
                </span>
              )}
              {node.mention_count === 0 && (
                <span className="text-fg-dim">אין עדיין פריטים</span>
              )}
            </div>
          </section>

          {node.product_lines.length > 0 && (
            <section aria-label="קווי מוצר">
              <h5 className="mb-1 text-xs font-semibold text-fg-dim">קווי מוצר</h5>
              <div className="flex flex-wrap gap-1.5 text-[11px]">
                {node.product_lines.map((pl) => (
                  <span
                    key={pl}
                    className="rounded bg-bg-sunken px-1.5 py-0.5 text-fg-muted"
                  >
                    {productLineLabel(pl, "he")}
                  </span>
                ))}
              </div>
            </section>
          )}

          <section aria-label="ציר זמן">
            <h5 className="mb-1 text-xs font-semibold text-fg-dim">אזכורים אחרונים</h5>
            {q.data.timeline.length === 0 ? (
              <EmptyState title="אין אזכורים" />
            ) : (
              <ul className="space-y-1">
                {q.data.timeline.slice(0, 8).map((t) => (
                  <li key={t.item_id}>
                    <Link
                      to={`/feed?open=${t.item_id}`}
                      className="block rounded-md p-1 text-xs hover:bg-bg-sunken"
                    >
                      <bdi className="block truncate font-medium" title={t.title ?? undefined}>
                        {t.title}
                      </bdi>
                      <span className="flex items-center gap-1.5 text-[10px] text-fg-dim">
                        <span className="font-mono">{formatDate(t.published_at)}</span>
                        <LevelBadge level={t.level as TriageLevel} size="sm" />
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-label="אירועים עסקיים">
            <h5 className="mb-1 text-xs font-semibold text-fg-dim">אירועים עסקיים</h5>
            {q.data.business_events.length === 0 ? (
              <EmptyState title="אין אירועים" />
            ) : (
              <ul className="space-y-1 text-xs">
                {q.data.business_events.slice(0, 5).map((e) => (
                  <li
                    key={e.id}
                    className="rounded-md border border-border bg-bg-sunken p-1.5"
                  >
                    <div className="flex items-center justify-between">
                      <span>{eventKindLabel(e.kind)}</span>
                      <span className="font-mono text-[10px] text-fg-dim">
                        {formatDate(e.date)}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-label="חקירות">
            <h5 className="mb-1 text-xs font-semibold text-fg-dim">חקירות מעמיקות</h5>
            {q.data.investigations.length === 0 ? (
              <EmptyState title="אין חקירות" />
            ) : (
              <ul className="space-y-1 text-xs">
                {q.data.investigations.map((inv) => (
                  <li
                    key={inv.job_id}
                    className="rounded-md border border-border bg-bg-sunken p-1.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <bdi className="min-w-0 flex-1 truncate" title={inv.question ?? "ללא שאלה"}>
                        {inv.question ?? "ללא שאלה"}
                      </bdi>
                      <span className="shrink-0 rounded bg-bg-raised px-1 py-0.5 text-[10px]">
                        {INVESTIGATION_STATE_LABEL_HE[inv.state] ?? inv.state}
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-label="דוחות">
            <h5 className="mb-1 text-xs font-semibold text-fg-dim">דוחות מזכירים</h5>
            {q.data.reports.length === 0 ? (
              <EmptyState title="אין דוחות" />
            ) : (
              <ul className="space-y-1 text-xs">
                {q.data.reports.map((r) => (
                  <li
                    key={r.id}
                    className="flex items-center justify-between rounded-md bg-bg-sunken p-1.5"
                  >
                    <span>{REPORT_KIND_LABEL_HE[r.kind] ?? r.kind}</span>
                    <span className="font-mono text-[10px] text-fg-dim">
                      {formatDate(r.created_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-label="קשרים">
            <h5 className="mb-1 text-xs font-semibold text-fg-dim">קשרים</h5>
            {q.data.edge_groups.length === 0 ? (
              <EmptyState title="אין קשרים" />
            ) : (
              <div className="space-y-1.5">
                {q.data.edge_groups.map((g) => (
                  <div key={g.label} className="text-xs">
                    <span className="text-fg-dim">{edgeLabelHe(g.label)}:</span>{" "}
                    {g.counterparts.map((c, i) => (
                      <span key={c.entity_id}>
                        <bdi>{c.entity_name}</bdi>
                        {i < g.counterparts.length - 1 ? ", " : ""}
                      </span>
                    ))}
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </aside>
  );
}
