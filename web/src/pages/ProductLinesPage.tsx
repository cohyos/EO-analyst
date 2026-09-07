import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { ProductLineCard, type ProductLinePendingState } from "@/components/productLines/ProductLineCard";
import { useT } from "@/i18n";

/**
 * PL-ui (2026-09-07): "קווי מוצר" -- one card per EO/IR product line (see
 * `web/src/lib/productLines.ts` for the fixed six-line catalog), each with compact KPIs, latest
 * report status, a "צור דוח" button and a click-through to `ProductLineDetailPage`.
 *
 * Built against a frozen contract (docs/qa/loop/round_7_fixes.md "### PL-ui status") that may not
 * exist on the live API yet -- `getProductLines` degrades to `[]` on anything but a real 404/5xx
 * (see `normalizeProductLine` in `web/src/api/real.ts`), so a backend that hasn't landed the
 * endpoint yet renders the empty state below rather than crashing the page.
 */
export function ProductLinesPage() {
  const t = useT();
  const queryClient = useQueryClient();
  const [pendingById, setPendingById] = useState<Record<string, ProductLinePendingState | null>>(
    {},
  );
  // One BD-page-style "keep polling the list for a few minutes" timer per in-flight report build,
  // so a card whose report finishes in the background updates without a manual refresh.
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const listQuery = useQuery({
    queryKey: ["product-lines"],
    queryFn: () => api.getProductLines(),
  });

  const createMutation = useMutation({
    mutationFn: (id: string) => api.postProductLineReport(id),
    onMutate: (id) => {
      setPendingById((cur) => ({ ...cur, [id]: null }));
    },
    onSuccess: (_res, id) => {
      setPendingById((cur) => ({ ...cur, [id]: { status: "queued" } }));
      if (pollTimers.current[id]) clearInterval(pollTimers.current[id]);
      const interval = setInterval(() => {
        queryClient.invalidateQueries({ queryKey: ["product-lines"] });
      }, 4000);
      pollTimers.current[id] = interval;
      setTimeout(() => {
        clearInterval(interval);
        delete pollTimers.current[id];
        setPendingById((cur) => ({ ...cur, [id]: null }));
      }, 3 * 60_000);
    },
    onError: (err, id) => {
      setPendingById((cur) => ({
        ...cur,
        [id]: { status: "failed", error: err instanceof Error ? err.message : null },
      }));
    },
  });

  const productLines = listQuery.data ?? [];

  return (
    <div className="space-y-4 p-4 md:p-6">
      <h2 className="sr-only">{t("nav.productLines")}</h2>
      {listQuery.isLoading && <LoadingState label={t("common.loading")} />}
      {listQuery.isError && <ErrorState error={listQuery.error} onRetry={() => listQuery.refetch()} />}
      {!listQuery.isLoading && !listQuery.isError && productLines.length === 0 && (
        <EmptyState title={t("productLines.emptyList")} />
      )}
      {productLines.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {productLines.map((pl) => (
            <ProductLineCard
              key={pl.id}
              productLine={pl}
              pending={pendingById[pl.id]}
              creating={createMutation.isPending && createMutation.variables === pl.id}
              onCreateReport={() => createMutation.mutate(pl.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
