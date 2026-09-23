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

export function PayloadsPage() {
  const t = useT();
  const [filters, setFilters] = useState<PayloadFiltersState>(EMPTY_FILTERS);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [viewMode, setViewMode] = useState<"tree" | "flat">("tree");
  const [manualExpanded, setManualExpanded] = useState<Record<string, boolean>>({});
  const isNarrow = useIsNarrowScreen();

  // F33 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md): `vendor` used to be applied client-side,
  // AFTER this query's own 500-row cap -- a payload whose vendor match sits outside that cap could
  // never be found no matter how exact the filter, showing a false "no matches" empty state. The
  // API already supports `vendor` server-side (`eoa.api.routes.payloads.list_payloads`, an ILIKE
  // match) -- filtering there means the cap is applied AFTER matching, not before.
  const payloadsQuery = useQuery({
    queryKey: ["payloads", filters.category, filters.vendor],
    queryFn: () =>
      api.getPayloads({
        category: filters.category || undefined,
        vendor: filters.vendor || undefined,
        limit: 500,
      }),
  });
  // Facet list (vendor dropdown options) from a category-scoped but vendor-UNfiltered baseline
  // fetch, so picking a vendor doesn't collapse the dropdown down to just that one vendor.
  const facetsQuery = useQuery({
    queryKey: ["payloads-facets", filters.category],
    queryFn: () => api.getPayloads({ category: filters.category || undefined, limit: 500 }),
  });

  const payloads = payloadsQuery.data?.payloads ?? [];
  const facetPayloads = facetsQuery.data?.payloads ?? payloads;

  const vendors = useMemo(() => {
    const set = new Set<string>();
    for (const p of facetPayloads) if (p.vendor_entity_name) set.add(p.vendor_entity_name);
    return [...set].sort();
  }, [facetPayloads]);

  // `q` stays a client-side filter over `payloads` -- unlike the (previously mis-scoped) `vendor`
  // filter, this is safe: `payloads` above is already the complete server-matched set for the
  // active category+vendor (well under the 500-row cap at current data volume), so no match can
  // be hiding outside the fetched page the way `vendor` used to.
  const filteredPayloads = useMemo(() => {
    if (!filters.q) return payloads;
    const q = filters.q.toLowerCase();
    return payloads.filter(
      (p) => p.canonical_name.toLowerCase().includes(q) || (p.family ?? "").toLowerCase().includes(q),
    );
  }, [payloads, filters.q]);

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

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="rounded-md border border-border-strong bg-bg-raised px-3 py-2 text-sm text-fg-dim" dir="auto">
        {t("payloads.intro")}
      </div>

      {payloadsQuery.isLoading && <LoadingState label={t("payloads.loading")} />}
      {payloadsQuery.isError && <ErrorState onRetry={() => payloadsQuery.refetch()} />}

      {!payloadsQuery.isLoading && !payloadsQuery.isError && (
        <>
          {facetPayloads.length === 0 ? (
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
            </>
          )}
        </>
      )}

      {selectedId != null && <PayloadDetailDrawer payloadId={selectedId} onClose={() => setSelectedId(null)} />}
    </div>
  );
}
