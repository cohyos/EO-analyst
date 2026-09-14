import { ContentShareActions } from "@/components/ContentShareActions";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { DossierComparisonView, type CompareProduct } from "@/components/dossiers/DossierComparisonView";
import { useT } from "@/i18n";
import { productLineLabel } from "@/lib/productLines";

const MAX_COMPARE = 3;
const MIN_COMPARE = 2;

// Deliberately does NOT cap at MAX_COMPARE here -- a `?keys=` list outside the 2-3 range must
// fail the explicit validity check below and show the honest error, not be silently truncated
// (which would hide from the user that some of their selection was dropped). Only the first
// MAX_COMPARE keys are ever actually fetched (see `slots` below), since this page calls its
// per-slot hook a fixed 3 times regardless.
function parseKeys(raw: string | null): string[] {
  if (!raw) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const part of raw.split(",")) {
    const k = part.trim();
    if (k && !seen.has(k)) {
      seen.add(k);
      out.push(k);
    }
  }
  return out;
}

/** Fetches one product's identity + latest structured data (`getDossier`) and, once that
 * resolves, its latest run's own `product_line` (`getDossierRun` -- the one dossier endpoint the
 * live backend reliably returns `product_line` on, see `web/src/types/api.ts`'s
 * `DossierRunDetail.product_line` doc comment). Always calls both hooks unconditionally (React's
 * rules of hooks) -- `enabled: !!key` is what actually skips the network call for an unused
 * comparison slot (this page calls this hook a fixed `MAX_COMPARE` times regardless of how many
 * keys the URL actually carries). */
function useDossierForCompare(key: string | undefined) {
  const detailQuery = useQuery({
    queryKey: ["dossier", key ?? ""],
    queryFn: () => api.getDossier(key as string),
    enabled: !!key,
  });
  const latestId = detailQuery.data?.dossiers[0]?.id ?? null;
  const runQuery = useQuery({
    queryKey: ["dossierRun", key ?? "", latestId],
    queryFn: () => api.getDossierRun(key as string, latestId as number),
    enabled: !!key && latestId != null,
  });
  return { key, detailQuery, runQuery };
}

type Slot = ReturnType<typeof useDossierForCompare>;

/**
 * PD-vocab-ui (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md §5.2): `/dossiers/compare?keys=a,b,c`
 * (2-3 `product_key`s). Joins each product's `GET /api/dossiers/{key}` + `GET /api/dossiers/{key}/
 * {id}` client-side (no new API endpoint -- the plan's own §5.2 intent, adapted to the real
 * backend's actual field split, see the doc comments on `useDossierForCompare`/
 * `DossierRunDetail.product_line`). Rejects/warns rather than rendering when the selected
 * products don't share one `product_line` -- comparison only means something within one line's
 * own fixed vocabulary (§9 item 4: cross-line comparison is deliberately unsupported).
 */
export function DossierComparePage() {
  const t = useT();
  const [searchParams] = useSearchParams();
  const keys = useMemo(() => parseKeys(searchParams.get("keys")), [searchParams]);

  const slot0 = useDossierForCompare(keys[0]);
  const slot1 = useDossierForCompare(keys[1]);
  const slot2 = useDossierForCompare(keys[2]);
  const slots: Slot[] = [slot0, slot1, slot2].filter((s) => !!s.key);

  if (keys.length < MIN_COMPARE || keys.length > MAX_COMPARE) {
    return (
      <div data-share-content className="space-y-4 p-4 md:p-6">
        <EmptyState title={t("dossiers.compare.invalidCountError")} />
        <Link to="/dossiers" className="inline-block text-xs text-accent hover:underline">
          {t("dossiers.backToList")}
        </Link>
      </div>
    );
  }

  const anyLoading = slots.some(
    (s) => s.detailQuery.isLoading || (s.detailQuery.data?.dossiers.length && s.runQuery.isLoading),
  );
  if (anyLoading) return <LoadingState label={t("common.loading")} />;

  const firstError = slots.find((s) => s.detailQuery.isError || s.runQuery.isError);
  if (firstError) {
    return (
      <ErrorState
        error={(firstError.detailQuery.error ?? firstError.runQuery.error) as Error}
        onRetry={() => {
          firstError.detailQuery.refetch();
          firstError.runQuery.refetch();
        }}
      />
    );
  }

  const missing = slots.filter((s) => !s.detailQuery.data || s.detailQuery.data.dossiers.length === 0);
  if (missing.length > 0) {
    return (
      <div className="space-y-4 p-4 md:p-6">
        <EmptyState title={t("dossiers.compare.someNotFoundError")} />
        <Link to="/dossiers" className="inline-block text-xs text-accent hover:underline">
          {t("dossiers.backToList")}
        </Link>
      </div>
    );
  }

  // `detail.latest` is the sole source of spec/performance content + sources (it's what
  // DossierDetailPage itself renders from); `runQuery` is fetched only to learn `product_line`
  // (see `useDossierForCompare`'s own doc comment for why the two calls split this way).
  const products: CompareProduct[] = slots.map((s) => {
    const detail = s.detailQuery.data!;
    const data = detail.latest;
    return {
      productKey: detail.product_key,
      productName: detail.product_name,
      vendor: detail.vendor,
      specifications: data?.specifications ?? [],
      performance: data?.performance ?? [],
      otherSpecifications: data?.other_specifications ?? [],
      sources: data?.sources ?? [],
    };
  });

  const productLines = slots.map((s) => s.runQuery.data?.product_line ?? s.detailQuery.data?.product_line ?? null);
  const uniqueLines = new Set(productLines);
  const sharedLine = uniqueLines.size === 1 ? [...uniqueLines][0] : null;

  return (
    <div className="space-y-4 p-4 md:p-6">
      <ContentShareActions title={t("dossiers.compare.title")} />
      <div>
        <h2 className="text-lg font-semibold text-fg">{t("dossiers.compare.title")}</h2>
        <p className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-fg-dim">
          {products.map((p, i) => (
            <span key={p.productKey}>
              <Link to={`/dossiers/${p.productKey}`} className="text-accent hover:underline">
                <bdi>{p.productName}</bdi>
              </Link>
              {i < products.length - 1 ? " · " : ""}
            </span>
          ))}
        </p>
      </div>

      {!sharedLine ? (
        <EmptyState
          title={t("dossiers.compare.differentLinesWarning")}
          description={t("dossiers.compare.differentLinesDetail", {
            lines: productLines.map((l) => (l ? productLineLabel(l, "he") : t("dossiers.compare.noLine"))).join(" / "),
          })}
        />
      ) : (
        <>
          <p className="text-xs text-fg-dim">
            {t("dossiers.compare.sharedLineLabel")}: {productLineLabel(sharedLine, "he")}
          </p>
          <DossierComparisonView products={products} productLine={sharedLine} />
        </>
      )}

      <Link to="/dossiers" className="inline-block text-xs text-accent hover:underline">
        {t("dossiers.backToList")}
      </Link>
    </div>
  );
}
