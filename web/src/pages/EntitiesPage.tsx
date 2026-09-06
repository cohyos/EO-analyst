import { useMemo, useState } from "react";
import { useParams, useSearchParams, Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Search, X } from "lucide-react";
import { api } from "@/api";
import type { EntitySummary } from "@/types/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { AddToContextButton } from "@/components/AddToContextButton";
import { StatTile } from "@/components/StatTile";
import { LevelBadge } from "@/components/LevelBadge";
import { EntityGraph } from "@/components/entities/EntityGraph";
import { entityKindLabel, eventKindLabel, edgeLabelHe } from "@/components/entities/eventKindLabel";
import { domainLabel } from "@/lib/taxonomy";
import { countryFlagEmoji } from "@/lib/countryFlag";
import { countryLabel } from "@/lib/countries";
import { formatDate, timeAgo } from "@/lib/time";
import type { TriageLevel } from "@/types/api";
import { useT } from "@/i18n";

const KIND_OPTIONS = ["company", "program", "org", "system", "person", "country"];
const SORT_OPTIONS: { value: "last_seen" | "mentions_7d" | "mentions_30d" | "name"; label: string }[] = [
  { value: "last_seen", label: "נראה לאחרונה" },
  { value: "mentions_7d", label: "אזכורים ב-7 ימים" },
  { value: "mentions_30d", label: "אזכורים ב-30 יום" },
  { value: "name", label: "שם" },
];

function EntityRow({ entity, active }: { entity: EntitySummary; active: boolean }) {
  const t = useT();
  return (
    <li
      draggable
      onDragStart={(ev) => {
        ev.dataTransfer.setData(
          "application/x-eo-context",
          JSON.stringify({ kind: "entity", id: entity.id, label: entity.name }),
        );
        ev.dataTransfer.effectAllowed = "copy";
      }}
      className={`flex items-center gap-2 rounded-lg border p-2.5 shadow-panel ${
        active ? "border-accent bg-bg-sunken" : "border-border bg-bg-raised"
      }`}
    >
      <Link to={`/entities/${entity.id}`} className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <bdi className="truncate text-sm font-medium text-fg">{entity.name}</bdi>
          <span className="shrink-0 rounded bg-bg-sunken px-1.5 py-0.5 text-[10px] text-fg-dim">
            {entityKindLabel(entity.kind)}
          </span>
          {entity.country && (
            <span className="shrink-0 text-xs" title={countryLabel(entity.country, "he")}>
              {countryFlagEmoji(entity.country)}
            </span>
          )}
          {/* A13 (מיקוד תעשייה ישראלית): small flag badge next to the watchlist star below. */}
          {entity.is_israeli && (
            <span
              data-testid={`entity-row-israel-badge-${entity.id}`}
              role="img"
              aria-label={t("entities.israelBadgeAria")}
              title={t("entities.israelBadgeAria")}
              className="shrink-0 text-xs"
            >
              🇮🇱
            </span>
          )}
          {entity.is_watchlist && (
            <span
              className="shrink-0 rounded bg-level-orange-bg px-1 py-0.5 text-[10px] text-level-orange"
              title="ברשימת המעקב"
            >
              ★
            </span>
          )}
        </div>
        <div className="mt-1 flex items-center gap-2 font-mono text-[11px] text-fg-dim">
          <span>{entity.mentions_7d} ב-7 ימים</span>
          <span>·</span>
          <span>{entity.mentions_30d} ב-30 יום</span>
          <span>·</span>
          <span>{timeAgo(entity.last_seen)}</span>
        </div>
      </Link>
      <AddToContextButton kind="entity" id={entity.id} label={entity.name} size="sm" />
    </li>
  );
}

function EntityListPanel({
  selectedId,
  onFiltersLoaded,
}: {
  selectedId: number | null;
  onFiltersLoaded: (n: number) => void;
}) {
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const kind = params.get("kind") ?? "";
  const country = params.get("country") ?? "";
  const watchlistOnly = params.get("watchlist") === "1";
  const showAll = params.get("all") === "1";
  // A13 (מיקוד תעשייה ישראלית): same URL-param toggle pattern as `watchlist` above.
  const israelOnly = params.get("israel") === "1";
  const sort = (params.get("sort") as (typeof SORT_OPTIONS)[number]["value"]) || "last_seen";
  const t = useT();

  function setParam(key: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["entities", q, kind, country, watchlistOnly, showAll, israelOnly, sort],
    queryFn: () =>
      api.getEntities({
        q: q || undefined,
        kind: kind || undefined,
        country: country || undefined,
        watchlist: watchlistOnly || undefined,
        all: showAll || undefined,
        israel: israelOnly || undefined,
        sort,
        limit: 200,
      }),
  });

  const countries = useMemo(
    () => Array.from(new Set((data ?? []).map((e) => e.country).filter((c): c is string => !!c))).sort(),
    [data],
  );

  if (data) onFiltersLoaded(data.length);

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="relative">
        <Search
          size={16}
          className="pointer-events-none absolute top-1/2 -translate-y-1/2 text-fg-dim"
          style={{ insetInlineStart: "0.75rem" }}
          aria-hidden="true"
        />
        <input
          type="search"
          value={q}
          onChange={(e) => setParam("q", e.target.value || null)}
          placeholder="חפש ישות לפי שם או כינוי…"
          className="w-full rounded-md border border-border-strong bg-bg-raised py-2 text-sm text-fg placeholder:text-fg-dim"
          style={{ paddingInlineStart: "2.25rem", paddingInlineEnd: "0.75rem" }}
          aria-label="חיפוש ישויות"
        />
      </div>

      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <select
          value={kind}
          onChange={(e) => setParam("kind", e.target.value || null)}
          aria-label="סינון לפי סוג ישות"
          className="rounded-md border border-border-strong bg-bg px-1.5 py-1 text-fg"
        >
          <option value="">כל הסוגים</option>
          {KIND_OPTIONS.map((k) => (
            <option key={k} value={k}>
              {entityKindLabel(k)}
            </option>
          ))}
        </select>
        <select
          value={country}
          onChange={(e) => setParam("country", e.target.value || null)}
          aria-label="סינון לפי מדינה"
          className="rounded-md border border-border-strong bg-bg px-1.5 py-1 text-fg"
        >
          <option value="">כל המדינות</option>
          {countries.map((c) => (
            <option key={c} value={c}>
              {countryFlagEmoji(c)} {countryLabel(c, "he")}
            </option>
          ))}
        </select>
        <select
          value={sort}
          onChange={(e) => setParam("sort", e.target.value)}
          aria-label="מיון"
          className="rounded-md border border-border-strong bg-bg px-1.5 py-1 text-fg"
        >
          {SORT_OPTIONS.map((s) => (
            <option key={s.value} value={s.value}>
              מיין: {s.label}
            </option>
          ))}
        </select>
      </div>

      <div className="flex flex-wrap items-center gap-3 text-xs text-fg-muted">
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={watchlistOnly}
            onChange={(e) => setParam("watchlist", e.target.checked ? "1" : null)}
          />
          רשימת מעקב בלבד
        </label>
        <label className="flex items-center gap-1.5" data-testid="israel-filter-toggle">
          <input
            type="checkbox"
            checked={israelOnly}
            onChange={(e) => setParam("israel", e.target.checked ? "1" : null)}
          />
          {t("entities.israelFilterLabel")}
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={showAll}
            onChange={(e) => setParam("all", e.target.checked ? "1" : null)}
          />
          הצג הכל (כולל רלוונטיות נמוכה)
        </label>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading && <LoadingState label="טוען ישויות…" />}
        {isError && <ErrorState onRetry={() => refetch()} />}
        {!isLoading && !isError && (data?.length ?? 0) === 0 && (
          <EmptyState
            title="לא נמצאו ישויות"
            description={
              showAll
                ? "נסה מונח חיפוש אחר או הסר סינונים."
                : 'נסו לסמן "הצג הכל" — ייתכן שהישות קיימת אך בעלת רלוונטיות נמוכה.'
            }
          />
        )}
        {data && data.length > 0 && (
          <ul className="space-y-2">
            {data.map((e) => (
              <EntityRow key={e.id} entity={e} active={e.id === selectedId} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function EntityCardPanel({ entityId }: { entityId: number }) {
  const t = useT();
  const entityQuery = useQuery({
    queryKey: ["entity", entityId],
    queryFn: () => api.getEntity(entityId),
  });

  if (entityQuery.isLoading) return <LoadingState label="טוען ישות…" />;
  if (entityQuery.isError || !entityQuery.data) {
    return <ErrorState onRetry={() => entityQuery.refetch()} message="הישות לא נמצאה" />;
  }

  const entity = entityQuery.data;
  const aliases = entity.aliases ?? [];
  const timeline = entity.timeline ?? [];
  const businessEvents = entity.business_events ?? [];
  const edgeGroups = entity.edge_groups ?? [];
  const levelCounts = entity.kpis?.related_items_by_level ?? {};

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-lg font-semibold">
              <bdi>{entity.name}</bdi>
            </h2>
            <span className="rounded bg-bg-sunken px-1.5 py-0.5 text-xs text-fg-dim">
              {entityKindLabel(entity.kind)}
            </span>
            {entity.country && (
              <span className="text-sm" title={countryLabel(entity.country, "he")}>
                {countryFlagEmoji(entity.country)}
              </span>
            )}
            {/* A13 (מיקוד תעשייה ישראלית): small flag badge, same slot as the watchlist star below. */}
            {entity.is_israeli && (
              <span
                data-testid="entity-card-israel-badge"
                role="img"
                aria-label={t("entities.israelBadgeAria")}
                title={t("entities.israelBadgeAria")}
                className="text-sm"
              >
                🇮🇱
              </span>
            )}
            {entity.is_watchlist && (
              <span className="rounded bg-level-orange-bg px-1.5 py-0.5 text-xs text-level-orange">
                ★ רשימת מעקב
              </span>
            )}
          </div>
          {entity.focus.length > 0 && (
            <bdi className="mt-1 block text-sm text-fg-muted">
              {entity.focus.map(domainLabel).join(" · ")}
            </bdi>
          )}
          {aliases.length > 0 && (
            <bdi className="mt-0.5 block text-xs text-fg-dim">כינויים: {aliases.join(", ")}</bdi>
          )}
        </div>
        <AddToContextButton kind="entity" id={entity.id} label={entity.name} />
      </header>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <StatTile label="אזכורים ב-7 ימים" value={entity.kpis?.mentions_7d ?? 0} />
        <StatTile label="אזכורים ב-30 יום" value={entity.kpis?.mentions_30d ?? 0} />
        <StatTile label="אירועים עסקיים" value={entity.kpis?.events_count ?? 0} />
        <StatTile label="פריטים סה״כ" value={entity.item_count} />
      </div>

      {Object.keys(levelCounts).length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          {Object.entries(levelCounts).map(([level, n]) => (
            <span key={level} className="flex items-center gap-1 text-xs text-fg-muted">
              <LevelBadge level={level as TriageLevel} size="sm" />
              <span className="font-mono">{n}</span>
            </span>
          ))}
        </div>
      )}

      <section aria-label="ציר זמן">
        <h3 className="mb-2 text-sm font-semibold text-fg-dim">ציר זמן פריטים</h3>
        {timeline.length === 0 ? (
          <EmptyState title="אין פריטים המזכירים ישות זו" />
        ) : (
          <ol className="space-y-1.5 border-r-2 border-border ps-4">
            {timeline.map((t) => (
              <li key={t.item_id} className="relative">
                <span className="absolute -end-[1.15rem] top-1.5 h-2 w-2 rounded-full bg-accent" />
                <Link to={`/feed?open=${t.item_id}`} className="block rounded-md p-1.5 hover:bg-bg-sunken">
                  <bdi className="block text-sm font-medium">{t.title}</bdi>
                  <span className="flex items-center gap-2 text-xs text-fg-dim">
                    {t.source_name && <bdi>{t.source_name}</bdi>}
                    <span className="font-mono">{formatDate(t.published_at)}</span>
                    <LevelBadge level={t.level} size="sm" />
                  </span>
                </Link>
              </li>
            ))}
          </ol>
        )}
      </section>

      <section aria-label="אירועים עסקיים">
        <h3 className="mb-2 text-sm font-semibold text-fg-dim">אירועים עסקיים</h3>
        {businessEvents.length === 0 ? (
          <EmptyState title="אין אירועים עסקיים מתועדים" />
        ) : (
          <ul className="space-y-1.5">
            {businessEvents.map((e) => (
              <li key={e.id} className="rounded-md border border-border bg-bg-raised p-2 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium">{eventKindLabel(e.kind)}</span>
                  <span className="font-mono text-xs text-fg-dim">{formatDate(e.date)}</span>
                </div>
                <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
                  {e.counterpart && <bdi>מול: {e.counterpart}</bdi>}
                  {e.amount_usd != null && (
                    <span className="font-mono">
                      {e.currency ?? "USD"} {Number(e.amount_usd).toLocaleString("he-IL")}
                    </span>
                  )}
                  {e.item_id != null && (
                    <Link to={`/feed?open=${e.item_id}`} className="text-accent hover:underline">
                      מקור →
                    </Link>
                  )}
                </div>
                {e.summary_he && <bdi className="mt-1 block text-xs text-fg-dim">{e.summary_he}</bdi>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="קשרים">
        <h3 className="mb-2 text-sm font-semibold text-fg-dim">קשרים</h3>
        {edgeGroups.length === 0 ? (
          <EmptyState title="אין קשרים מתועדים" />
        ) : (
          <div className="space-y-2">
            {edgeGroups.map((g) => (
              <div key={g.label}>
                <p className="text-xs font-medium text-fg-dim">{edgeLabelHe(g.label)}</p>
                <ul className="mt-1 flex flex-wrap gap-1.5">
                  {g.counterparts.map((c) => (
                    <li key={c.entity_id}>
                      <Link
                        to={`/entities/${c.entity_id}`}
                        className="rounded-md border border-border-strong px-2 py-1 text-xs text-fg-muted hover:bg-bg-sunken"
                      >
                        <bdi>{c.entity_name}</bdi>
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function GraphPanel({ entityId }: { entityId: number }) {
  const [expanded, setExpanded] = useState(false);
  const [depth, setDepth] = useState(1);

  const graphQuery = useQuery({
    queryKey: ["entity-graph", entityId, depth],
    queryFn: () => api.getGraph({ entity_id: entityId, depth }),
  });

  const hasEdges = (graphQuery.data?.edges.length ?? 0) > 0;

  return (
    <section aria-label="גרף ישויות" className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-fg-dim">גרף קשרים</h3>
        <label className="flex items-center gap-1.5 text-xs text-fg-dim">
          עומק:
          <select
            value={depth}
            onChange={(e) => setDepth(Number(e.target.value))}
            className="rounded-md border border-border-strong bg-bg px-1.5 py-0.5 text-sm"
          >
            <option value={1}>1</option>
            <option value={2}>2</option>
          </select>
        </label>
      </div>
      {graphQuery.isLoading && <LoadingState label="בונה גרף…" />}
      {graphQuery.data &&
        (hasEdges ? (
          <EntityGraph graph={graphQuery.data} focusEntityId={entityId} compact onExpand={() => setExpanded(true)} />
        ) : (
          <EmptyState title="אין קשרים מתועדים" description="לא נמצאו קשרי גרף לישות זו עדיין." />
        ))}

      {expanded && graphQuery.data && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
          role="dialog"
          aria-modal="true"
          aria-label="גרף מלא"
        >
          <div className="relative w-full max-w-4xl rounded-lg border border-border-strong bg-bg-raised p-4 shadow-panel">
            <button
              type="button"
              onClick={() => setExpanded(false)}
              aria-label="סגור גרף מלא"
              className="absolute top-3 start-3 rounded-md p-1 text-fg-dim hover:bg-bg-sunken hover:text-fg"
            >
              <X size={16} aria-hidden="true" />
            </button>
            <EntityGraph graph={graphQuery.data} focusEntityId={entityId} />
          </div>
        </div>
      )}
    </section>
  );
}

export function EntitiesPage() {
  const { id } = useParams<{ id: string }>();
  const entityId = id ? Number(id) : null;
  const [listCount, setListCount] = useState<number | null>(null);

  return (
    <div className="flex h-full flex-col gap-4 p-4 md:p-6">
      <div>
        <h1 className="text-base font-semibold text-fg">ישויות וגרף</h1>
        <p className="text-sm text-fg-muted">
          מפת השחקנים: מי עובד עם מי, מי מתחרה במי, ומה קרה לאחרונה.
        </p>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[22rem_1fr_20rem]">
        {/* Right pane (RTL first column): search + filters + list */}
        <div className="min-h-0 lg:order-3">
          <EntityListPanel selectedId={entityId} onFiltersLoaded={setListCount} />
        </div>

        {/* Center pane: selected entity card */}
        <div className="min-h-0 overflow-y-auto lg:order-2">
          {entityId == null || Number.isNaN(entityId) ? (
            <EmptyState
              title="בחר ישות מהרשימה"
              description={
                listCount === 0
                  ? "לא נמצאו ישויות בסינון הנוכחי."
                  : "לחצו על ישות ברשימה מימין כדי לראות פרטים, ציר זמן, אירועים וקשרים."
              }
            />
          ) : (
            <EntityCardPanel entityId={entityId} />
          )}
        </div>

        {/* Left pane: compact graph */}
        <div className="min-h-0 overflow-y-auto lg:order-1">
          {entityId == null || Number.isNaN(entityId) ? (
            <EmptyState title="אין ישות נבחרת" description="הגרף יופיע לאחר בחירת ישות." />
          ) : (
            <GraphPanel entityId={entityId} />
          )}
        </div>
      </div>
    </div>
  );
}
