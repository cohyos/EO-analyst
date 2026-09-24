import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { PayloadFilters, type PayloadFiltersState } from "@/components/payloads/PayloadFilters";
import { PayloadTable } from "@/components/payloads/PayloadTable";
import { PayloadTree } from "@/components/payloads/PayloadTree";
import { PayloadDetailDrawer } from "@/components/payloads/PayloadDetailDrawer";
import {
  buildPayloadTree,
  defaultExpandedKeys,
  expandedKeysForSearch,
  filterPayloadTree,
} from "@/lib/payloadFamilies";
import { useT } from "@/i18n";
import type { PayloadRecord } from "@/types/api";

const EMPTY_FILTERS: PayloadFiltersState = { category: "", vendor: "", q: "" };

/** W19b (docs/REVIEW_2026-09-06_evening.md): true under a narrow (mobile/portrait) viewport --
 * drives the tree's accordion behaviour (expanding one vendor collapses the others) instead of
 * letting every vendor stay open at once on a screen too small to show more than one branch at a
 * time. `window.matchMedia` is polyfilled in `src/test/setup.ts` (always `matches: false`), so
 * this is a no-op (multi-expand) under vitest. */
function useIsNarrowScreen(breakpointPx = 640): boolean {
  const [isNarrow, setIsNarrow] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia(`(max-width: ${breakpointPx}px)`).matches;
  });
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(`(max-width: ${breakpointPx}px)`);
    const handler = () => setIsNarrow(mql.matches);
    if (mql.addEventListener) mql.addEventListener("change", handler);
    else mql.addListener(handler);
    return () => {
      if (mql.removeEventListener) mql.removeEventListener("change", handler);
      else mql.removeListener(handler);
    };
  }, [breakpointPx]);
  return isNarrow;
}

const PAGE_SIZE = 200;

export function PayloadsPage() {
  const t = useT();
  const [filters, setFilters] = useState<PayloadFiltersState>(EMPTY_FILTERS);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [viewMode, setViewMode] = useState<"tree" | "flat">("tree");
  const [manualExpanded, setManualExpanded] = useState<Record<string, boolean>>({});
  const isNarrow = useIsNarrowScreen();
  // R06/F33 (SOL-REVIEW2-2026-09-24): "load more" pagination -- resets to page 1 whenever the
  // active filters change (a stale page 3 of a NEW filter's results would be nonsensical).
  const [page, setPage] = useState(1);
  useEffect(() => {
    setPage(1);
  }, [filters.category, filters.vendor, filters.q]);

  // F33 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md, both the original audit and the
  // 2026-09-24 review of round-1): `vendor` AND `q` are now both sent to the API -- the API
  // already supports both server-side (`eoa.api.routes.payloads.list_payloads`, ILIKE matches) --
  // filtering there means the row cap is applied AFTER matching, not before. `q` used to stay a
  // client-side filter over the already-capped `payloads` response, so a text match sitting
  // outside the cap could never be found (round-1 review's remaining F33 gap).
  const payloadsQuery = useQuery({
    queryKey: ["payloads", filters.category, filters.vendor, filters.q, page],
    queryFn: () =>
      api.getPayloads({
        category: filters.category || undefined,
        vendor: filters.vendor || undefined,
        q: filters.q || undefined,
        limit: PAGE_SIZE,
        page,
      }),
  });
  // F33 review follow-up: facet options (vendor dropdown) used to come from a capped
  // (`limit=500`) baseline `getPayloads` fetch -- a vendor whose only rows sat outside that cap
  // could never appear as a filter option. `getPayloadFacets` is an uncapped, server-side DISTINCT
  // query -- no row cap to defeat. Still category-scoped (not vendor-scoped), so picking a vendor
  // doesn't collapse the dropdown down to just that one vendor.
  const facetsQuery = useQuery({
    queryKey: ["payloads-facets", filters.category],
    queryFn: () => api.getPayloadFacets(filters.category || undefined),
  });

  // R06/F33: accumulate pages fetched so far for the current filter set -- page 1 replaces the
  // list (a fresh filter/search), page > 1 (a "load more" click) appends to it. Reset whenever
  // the filters change, mirroring the `page` reset above.
  const [accumulatedPayloads, setAccumulatedPayloads] = useState<PayloadRecord[]>([]);
  useEffect(() => {
    setAccumulatedPayloads([]);
  }, [filters.category, filters.vendor, filters.q]);
  useEffect(() => {
    if (!payloadsQuery.data) return;
    setAccumulatedPayloads((prev) =>
      page === 1 ? payloadsQuery.data.payloads : [...prev, ...payloadsQuery.data.payloads],
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on the query result, not `page` itself.
  }, [payloadsQuery.data]);

  const payloads = accumulatedPayloads;
  const payloadsTotal = payloadsQuery.data?.total ?? payloads.length;
  const hasMorePayloads = payloadsQuery.data?.has_more ?? false;
  const vendors = facetsQuery.data?.vendors ?? [];
  // R09 (SOL-REVIEW2-2026-09-24): "any payloads in the DB at all", independent of the current
  // filter -- `facetsQuery.data.total` is an unfiltered `count(*)`, so it can't be fooled into
  // reporting "database empty" by a filter that matches zero rows, or by every existing row having
  // a null vendor (which used to make `vendors` empty too). Falls back to the old vendor/row
  // heuristic only while facets haven't loaded yet, to avoid a load-time empty-state flash.
  const hasAnyPayloads =
    facetsQuery.data?.total != null ? facetsQuery.data.total > 0 : vendors.length > 0 || payloads.length > 0;

  // `q` is now forwarded to the server above -- `payloads` is already the fully server-matched,
  // server-side-filtered set. No further client-side re-filtering needed.
  const filteredPayloads = payloads;

  // W19b: vendor -> family -> variant grouping, built client-side from the already-fetched flat
  // list (`@/lib/payloadFamilies`) -- see that module's docstring for why this doesn't call
  // `GET /api/payloads/tree` a second time.
  const rawTree = useMemo(() => buildPayloadTree(payloads), [payloads]);
  const tree = useMemo(() => filterPayloadTree(rawTree, filters.q), [rawTree, filters.q]);

  const baseExpanded = useMemo(() => defaultExpandedKeys(rawTree), [rawTree]);
  const searchExpanded = useMemo(() => expandedKeysForSearch(tree, filters.q), [tree, filters.q]);
  const hasQuery = filters.q.trim() !== "";

  // Manual per-node overrides (from clicking/toggling a row) are reset whenever search switches
  // on/off, not on every keystroke -- so a match found mid-search never stays hidden behind an
  // override left over from browsing, but toggling a branch open/closed while typing still feels
  // stable within one search.
  const wasSearchingRef = useRef(hasQuery);
  useEffect(() => {
    if (wasSearchingRef.current !== hasQuery) {
      wasSearchingRef.current = hasQuery;
      setManualExpanded({});
    }
  }, [hasQuery]);

  function isExpanded(key: string): boolean {
    if (key in manualExpanded) return manualExpanded[key];
    return hasQuery ? searchExpanded.has(key) : baseExpanded.has(key);
  }

  function toggleExpand(key: string) {
    setManualExpanded((prev) => {
      const currentlyExpanded = key in prev ? prev[key] : hasQuery ? searchExpanded.has(key) : baseExpanded.has(key);
      const next: Record<string, boolean> = { ...prev, [key]: !currentlyExpanded };
      // Accordion on narrow screens (W19b): expanding a vendor closes every other vendor so a
      // small screen never has to scroll past more than one open branch at a time.
      const isVendorKey = key.startsWith("v:") && !key.includes("|f:");
      if (isNarrow && isVendorKey && !currentlyExpanded) {
        for (const vendorNode of tree.vendors) {
          const vk = `v:${vendorNode.vendor}`;
          if (vk !== key) next[vk] = false;
        }
      }
      return next;
    });
  }

  const isTreeMode = viewMode === "tree";
  const currentCount = isTreeMode ? tree.payload_count : filteredPayloads.length;
  // R06/F33: only the very first fetch (page 1, nothing accumulated yet) blanks the whole page --
  // a "load more" fetch (page > 1) keeps the already-loaded rows on screen with its own inline
  // indicator instead, see the button below.
  const initialLoading = payloadsQuery.isLoading && page === 1;
  const isLoadingMore = payloadsQuery.isFetching && page > 1;

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="rounded-md border border-border-strong bg-bg-raised px-3 py-2 text-sm text-fg-dim" dir="auto">
        {t("payloads.intro")}
      </div>

      {initialLoading && <LoadingState label={t("payloads.loading")} />}
      {payloadsQuery.isError && <ErrorState onRetry={() => payloadsQuery.refetch()} />}

      {!initialLoading && !payloadsQuery.isError && (
        <>
          {!hasAnyPayloads ? (
            <EmptyState title={t("payloads.emptyTitle")} description={t("payloads.emptyDescription")} />
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <PayloadFilters value={filters} onChange={setFilters} vendors={vendors} />
                <div
                  role="group"
                  aria-label={t("payloads.viewModeLabel")}
                  className="flex shrink-0 items-center gap-1 rounded-md border border-border-strong p-0.5"
                >
                  <button
                    type="button"
                    aria-pressed={viewMode === "tree"}
                    onClick={() => setViewMode("tree")}
                    className={
                      "rounded px-2.5 py-1 text-sm " +
                      (viewMode === "tree" ? "bg-bg-sunken text-fg" : "text-fg-dim hover:text-fg")
                    }
                  >
                    {t("payloads.viewModeTree")}
                  </button>
                  <button
                    type="button"
                    aria-pressed={viewMode === "flat"}
                    onClick={() => setViewMode("flat")}
                    className={
                      "rounded px-2.5 py-1 text-sm " +
                      (viewMode === "flat" ? "bg-bg-sunken text-fg" : "text-fg-dim hover:text-fg")
                    }
                  >
                    {t("payloads.viewModeFlat")}
                  </button>
                </div>
              </div>

              {currentCount === 0 ? (
                <EmptyState title={t("payloads.noMatchesTitle")} description={t("payloads.noMatchesDescription")} />
              ) : isTreeMode ? (
                <PayloadTree
                  tree={tree}
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  isExpanded={isExpanded}
                  onToggleExpand={toggleExpand}
                />
              ) : (
                <PayloadTable payloads={filteredPayloads} selectedId={selectedId} onSelect={setSelectedId} />
              )}

              {/* R06/F33: server-side "load more" -- mobile/RTL: full-width, min-h-10 (40px) touch
                  target, dir="auto" so the Hebrew count text ("X מתוך Y") reads right-to-left. */}
              {hasMorePayloads && (
                <div className="flex justify-center pt-2">
                  <button
                    type="button"
                    onClick={() => setPage((p) => p + 1)}
                    disabled={isLoadingMore}
                    dir="auto"
                    className="min-h-10 w-full max-w-xs rounded-md border border-border-strong px-4 py-2 text-sm text-fg hover:bg-bg-raised disabled:opacity-60 sm:w-auto"
                  >
                    {isLoadingMore
                      ? t("payloads.loadingMore")
                      : t("payloads.loadMore", { shown: payloads.length, total: payloadsTotal })}
                  </button>
                </div>
              )}
            </>
          )}
        </>
      )}

      {selectedId != null && <PayloadDetailDrawer payloadId={selectedId} onClose={() => setSelectedId(null)} />}
    </div>
  );
}
