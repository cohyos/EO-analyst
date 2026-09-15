import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ExternalLink, FileText } from "lucide-react";
import { api } from "@/api";
import { formatDate } from "@/lib/time";
import { cn } from "@/lib/cn";
import { productLineLabel } from "@/lib/productLines";
import { CorroborationBadge } from "@/components/feed/CorroborationBadge";
import { useI18n, useT } from "@/i18n";

const SUMMARY_CLAMP_THRESHOLD = 160; // roughly the point a 4-line clamp actually truncates something

export interface SourcePreviewFallback {
  title?: string | null;
  sourceName?: string | null;
  publishedAt?: string | null;
  url?: string | null;
}

/**
 * "Read the summary before you're sent to the article" (2026-09-07): a shared preview card
 * surfaced everywhere the UI links out to a source -- the report citation tooltip/appendix, the
 * chat sources footer, tenders, and tender forecasts. Given `itemId` it lazily loads
 * `GET /api/items/{id}` via react-query (staleTime doubles as the "small in-memory cache" the spec
 * asks for -- a second hover/open of the same item within 5 minutes never refetches, and the same
 * cache entry is shared across every caller keyed on that id, matching the pattern
 * `ForecastList.tsx`'s `useResolvedSources` already relies on for `item:N` sources).
 *
 * When no `itemId` is known (a citation that only ever carried a URL, or the backend hasn't wired
 * an id yet) this renders whatever `fallback` fields the caller already has on hand instead of
 * fetching anything -- see "### R10-preview status" in docs/qa/loop/round_10_fixes.md for the one
 * remaining gap that leaves (report citations resolved only by URL, no summary available).
 */
export function SourcePreviewCard({
  itemId,
  fallback,
  className,
  titleId,
}: {
  itemId?: number | null;
  fallback?: SourcePreviewFallback;
  className?: string;
  /** Applied to the title element so a wrapping dialog/tooltip can `aria-labelledby` it. */
  titleId?: string;
}) {
  const t = useT();
  const { locale } = useI18n();
  const [expanded, setExpanded] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["source-preview-item", itemId],
    queryFn: () => api.getItem(itemId as number),
    enabled: itemId != null,
    staleTime: 5 * 60_000,
  });

  // The action row below (itemId != null || url) is deliberately computed from `fallback` and
  // rendered *outside* the loading gate: every caller already knows the URL (and often the item
  // id) synchronously from its own data before this card ever fetches anything, so "פתח מקור" /
  // "פתח פריט" must stay clickable immediately -- an analyst who already knows they want to leave
  // shouldn't have to wait on the summary fetch first. Only the richer detail body (summary, key
  // facts, corroboration, product lines) -- which really does depend on the fetch -- shows a
  // skeleton while loading.
  const showSkeleton = itemId != null && isLoading;
  const title = data?.title ?? fallback?.title ?? null;
  const sourceName = data?.source_name ?? fallback?.sourceName ?? null;
  const publishedAt = data?.published_at ?? fallback?.publishedAt ?? null;
  const url = data?.url ?? fallback?.url ?? null;
  const summary = data?.summary_he ?? null;
  const keyFacts = data?.key_facts ?? [];
  const productLines = data?.product_lines ?? [];

  return (
    <div className={cn("w-72 max-w-[85vw] space-y-2", className)} data-testid="source-preview-card">
      {showSkeleton ? (
        <div className="space-y-1.5" aria-busy="true">
          <span className="sr-only">{t("sourcePreview.loading")}</span>
          <div aria-hidden="true" className="h-3.5 w-4/5 animate-pulse rounded bg-bg-sunken" />
          <div aria-hidden="true" className="h-2.5 w-1/2 animate-pulse rounded bg-bg-sunken" />
          <div aria-hidden="true" className="h-2.5 w-full animate-pulse rounded bg-bg-sunken" />
          <div aria-hidden="true" className="h-2.5 w-full animate-pulse rounded bg-bg-sunken" />
          <div aria-hidden="true" className="h-2.5 w-2/3 animate-pulse rounded bg-bg-sunken" />
        </div>
      ) : (
        <>
          <bdi id={titleId} className="block text-sm font-medium leading-snug text-fg">
            {title || t("sourcePreview.noTitle")}
          </bdi>

          {(sourceName || publishedAt) && (
            <div className="flex items-center gap-1.5 text-xs text-fg-dim">
              {sourceName && <bdi className="min-w-0 truncate">{sourceName}</bdi>}
              {sourceName && publishedAt && <span aria-hidden="true">·</span>}
              {publishedAt && <span className="shrink-0 font-mono">{formatDate(publishedAt)}</span>}
            </div>
          )}

          {data?.corroboration !== undefined && (
            <CorroborationBadge corroboration={data.corroboration} size="sm" showUnknown />
          )}

          {summary && (
            <div>
              <p
                dir="auto"
                className={cn("text-xs leading-relaxed text-fg-muted", !expanded && "line-clamp-4")}
              >
                {summary}
              </p>
              {summary.length > SUMMARY_CLAMP_THRESHOLD && (
                <button
                  type="button"
                  onClick={() => setExpanded((v) => !v)}
                  aria-expanded={expanded}
                  className="mt-0.5 text-xs font-medium text-accent hover:underline"
                >
                  {expanded ? t("sourcePreview.less") : t("sourcePreview.more")}
                </button>
              )}
            </div>
          )}

          {keyFacts.length > 0 && (
            <ul className="space-y-0.5 text-xs text-fg-muted">
              {keyFacts.slice(0, 3).map((fact, i) => (
                <li key={i} dir="auto" className="flex gap-1.5">
                  <span aria-hidden="true" className="shrink-0 text-fg-dim">
                    •
                  </span>
                  <span>{fact}</span>
                </li>
              ))}
            </ul>
          )}

          {productLines.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {productLines.map((id) => (
                <span
                  key={id}
                  className="rounded-full bg-bg-sunken px-1.5 py-0.5 text-xs text-fg-dim"
                >
                  <bdi>{productLineLabel(id, locale)}</bdi>
                </span>
              ))}
            </div>
          )}
        </>
      )}

      {(itemId != null || url) && (
        <div className="flex items-center gap-3 border-t border-border pt-1.5 text-xs">
          {itemId != null && (
            <Link
              to={`/items/${itemId}`}
              className="flex items-center gap-1 font-medium text-accent hover:underline"
            >
              <FileText size={11} aria-hidden="true" />
              {t("sourcePreview.openItem")}
            </Link>
          )}
          {url && (
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 font-medium text-accent hover:underline"
            >
              <ExternalLink size={11} aria-hidden="true" />
              {t("sourcePreview.openSource")}
            </a>
          )}
        </div>
      )}
    </div>
  );
}
