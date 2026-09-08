import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Plus } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { DossierCard, type DossierPendingState } from "@/components/dossiers/DossierCard";
import { NewDossierForm } from "@/components/dossiers/NewDossierForm";
import { useT } from "@/i18n";
import type { DossierCreateBody } from "@/types/api";

/**
 * PD-ui (docs/PLAN_PRODUCT_DOSSIER.md section 6): "סקירות מוצר" list -- one card per product
 * (`DossierCard`), a "סקירה חדשה" form that queues a new investigation and navigates to its
 * detail page (where the pending-run banner takes over polling), and a per-card "הרץ שוב" that
 * re-investigates in place, mirroring `ProductLinesPage`'s own poll-the-list-for-a-few-minutes
 * pattern for a build that finishes in the background.
 */
export function DossiersPage() {
  const t = useT();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [formOpen, setFormOpen] = useState(false);
  const [rerunPendingByKey, setRerunPendingByKey] = useState<Record<string, DossierPendingState | null>>({});
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const listQuery = useQuery({
    queryKey: ["dossiers"],
    queryFn: () => api.getDossiers(),
  });

  const createMutation = useMutation({
    mutationFn: (body: DossierCreateBody) => api.postDossier(body),
    onSuccess: (res) => {
      setFormOpen(false);
      queryClient.invalidateQueries({ queryKey: ["dossiers"] });
      navigate(`/dossiers/${res.product_key}`);
    },
  });

  const rerunMutation = useMutation({
    mutationFn: (productKey: string) => api.postDossierRerun(productKey),
    onMutate: (key) => setRerunPendingByKey((cur) => ({ ...cur, [key]: null })),
    onSuccess: (_res, key) => {
      setRerunPendingByKey((cur) => ({ ...cur, [key]: { status: "queued" } }));
      if (pollTimers.current[key]) clearInterval(pollTimers.current[key]);
      const interval = setInterval(() => {
        queryClient.invalidateQueries({ queryKey: ["dossiers"] });
      }, 4000);
      pollTimers.current[key] = interval;
      setTimeout(() => {
        clearInterval(interval);
        delete pollTimers.current[key];
        setRerunPendingByKey((cur) => ({ ...cur, [key]: null }));
      }, 3 * 60_000);
    },
    onError: (err, key) => {
      setRerunPendingByKey((cur) => ({
        ...cur,
        [key]: { status: "failed", error: err instanceof Error ? err.message : null },
      }));
    },
  });

  const dossiers = listQuery.data ?? [];

  return (
    <div className="space-y-4 p-4 md:p-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="sr-only">{t("nav.dossiers")}</h2>
        {!formOpen && (
          <button
            type="button"
            onClick={() => setFormOpen(true)}
            data-testid="dossier-new-button"
            className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90"
          >
            <Plus size={14} aria-hidden="true" />
            {t("dossiers.createNew")}
          </button>
        )}
      </div>

      {formOpen && (
        <NewDossierForm
          submitting={createMutation.isPending}
          onCancel={() => setFormOpen(false)}
          onSubmit={(body) => createMutation.mutate(body)}
        />
      )}
      {createMutation.isError && (
        <p role="alert" className="text-sm text-danger">
          {createMutation.error instanceof Error ? createMutation.error.message : t("bd.failedStatus")}
        </p>
      )}

      {listQuery.isLoading && <LoadingState label={t("common.loading")} />}
      {listQuery.isError && <ErrorState error={listQuery.error} onRetry={() => listQuery.refetch()} />}
      {!listQuery.isLoading && !listQuery.isError && dossiers.length === 0 && (
        <EmptyState title={t("dossiers.emptyList")} />
      )}
      {dossiers.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {dossiers.map((d) => (
            <DossierCard
              key={d.product_key}
              dossier={d}
              pending={rerunPendingByKey[d.product_key]}
              rerunning={rerunMutation.isPending && rerunMutation.variables === d.product_key}
              onRerun={() => rerunMutation.mutate(d.product_key)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
