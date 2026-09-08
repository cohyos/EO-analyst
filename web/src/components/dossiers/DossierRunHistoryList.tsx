import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronUp } from "lucide-react";
import { api } from "@/api";
import { LoadingState } from "@/components/states";
import { DossierSentenceList } from "./DossierFact";
import { formatDateTime } from "@/lib/time";
import { cn } from "@/lib/cn";
import { useI18n, useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";
import type { DossierRunRef } from "@/types/api";

/**
 * PD-ui (docs/PLAN_PRODUCT_DOSSIER.md section 6): "run history with a diff link between two
 * runs". The frozen contract (section 5) exposes one run at a time (`GET
 * /api/dossiers/{key}/{id}`), each already carrying its own `what_changed` -- the diff against
 * *its* immediate predecessor (section 4's `diff.py`) -- so "השווה" on a given run lazily fetches
 * that one run and expands its own `what_changed` inline, rather than requesting a two-id diff
 * the API doesn't offer.
 */
export function DossierRunHistoryList({ productKey, runs }: { productKey: string; runs: DossierRunRef[] }) {
  const t = useT();
  const { locale } = useI18n();
  const [expandedId, setExpandedId] = useState<number | null>(null);

  if (runs.length === 0) return null;

  return (
    <ul className="divide-y divide-border rounded-lg border border-border" data-testid="dossier-run-history">
      {runs.map((run) => (
        <li key={run.id}>
          <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-fg">{formatDateTime(run.created_at, locale)}</span>
              <span className="rounded-full bg-bg-sunken px-2 py-0.5 text-xs text-fg-dim">
                {t(`dossiers.outcome.${run.outcome}` as TranslationKey)}
              </span>
              {run.confidence != null && (
                <span className="text-xs text-fg-dim">
                  {t("dossiers.confidenceLabel", { pct: Math.round(run.confidence * 100) })}
                </span>
              )}
            </div>
            <button
              type="button"
              onClick={() => setExpandedId((cur) => (cur === run.id ? null : run.id))}
              aria-expanded={expandedId === run.id}
              data-testid={`dossier-run-compare-${run.id}`}
              className="flex items-center gap-1 text-xs text-accent hover:underline"
            >
              {expandedId === run.id ? t("dossiers.runHistory.hideCompare") : t("dossiers.runHistory.compare")}
              {expandedId === run.id ? (
                <ChevronUp size={12} aria-hidden="true" />
              ) : (
                <ChevronDown size={12} aria-hidden="true" />
              )}
            </button>
          </div>
          {expandedId === run.id && <RunDiffPanel productKey={productKey} runId={run.id} />}
        </li>
      ))}
    </ul>
  );
}

function RunDiffPanel({ productKey, runId }: { productKey: string; runId: number }) {
  const t = useT();
  const runQuery = useQuery({
    queryKey: ["dossier-run", productKey, runId],
    queryFn: () => api.getDossierRun(productKey, runId),
  });

  const citations = (runQuery.data?.sources ?? []).map((s) => ({
    n: s.n,
    item_id: null,
    title: s.title,
    url: s.url,
  }));

  return (
    <div className={cn("border-t border-border bg-bg-sunken px-3 py-2")}>
      {runQuery.isLoading && <LoadingState label={t("common.loading")} />}
      {runQuery.data && (
        <DossierSentenceList
          sentences={runQuery.data.data.what_changed}
          citations={citations}
          emptyLabel={t("dossiers.runHistory.noChanges")}
        />
      )}
    </div>
  );
}
