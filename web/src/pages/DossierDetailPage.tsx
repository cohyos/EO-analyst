import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { DossierSectionNav, type DossierSectionNavItem } from "@/components/dossiers/DossierSectionNav";
import { DossierTable, type DossierTableColumn } from "@/components/dossiers/DossierTable";
import {
  DossierCiteChips,
  DossierFactText,
  DossierPlainText,
  DossierSentenceList,
  NotFoundInSources,
} from "@/components/dossiers/DossierFact";
import { DossierRunHistoryList } from "@/components/dossiers/DossierRunHistoryList";
import { DossierProgressList } from "@/components/dossiers/DossierProgressBanner";
import type { CitationLike } from "@/components/CitationText";
import { formatDateTime } from "@/lib/time";
import { useI18n, useT } from "@/i18n";
import type {
  DossierCompetitorRow,
  DossierDealRow,
  DossierPartnerRow,
  DossierPatentRef,
  DossierPerformanceRow,
  DossierPriceRow,
  DossierSpecRow,
  DossierTenderRef,
  DossierVersionRow,
} from "@/types/api";

/**
 * PD-ui (docs/PLAN_PRODUCT_DOSSIER.md section 6): `/dossiers/:key` -- header (identity, status,
 * TRL, confidence), sticky in-page section nav, the fixed section order the plan specifies
 * (summary through sources), a pending-run banner that polls `getDossier` every 10s while
 * `pending_job` is set (same mechanism `InvestigationDetailPage` uses for a running job), and the
 * run history + "השווה" expander (`DossierRunHistoryList`).
 */
export function DossierDetailPage() {
  const { key = "" } = useParams<{ key: string }>();
  const t = useT();
  const { locale } = useI18n();
  const queryClient = useQueryClient();

  const detailQuery = useQuery({
    queryKey: ["dossier", key],
    queryFn: () => api.getDossier(key),
    enabled: !!key,
    refetchInterval: (query) => (query.state.data?.pending_job ? 10_000 : false),
  });

  const rerunMutation = useMutation({
    mutationFn: () => api.postDossierRerun(key),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["dossier", key] }),
  });

  if (detailQuery.isLoading) return <LoadingState label={t("common.loading")} />;
  if (detailQuery.isError)
    return <ErrorState error={detailQuery.error} onRetry={() => detailQuery.refetch()} />;
  const d = detailQuery.data;
  if (!d) return <EmptyState title={t("dossiers.notFound")} />;

  const data = d.latest;
  const citations: CitationLike[] = (data?.sources ?? []).map((s) => ({
    n: s.n,
    item_id: null,
    title: s.title,
    url: s.url,
  }));
  const latestRun = d.dossiers[0] ?? null;

  const NAV_ITEMS: DossierSectionNavItem[] = [
    { id: "dossier-summary", label: t("dossiers.sections.summary") },
    { id: "dossier-specifications", label: t("dossiers.sections.specifications") },
    { id: "dossier-versions", label: t("dossiers.sections.versions") },
    { id: "dossier-performance", label: t("dossiers.sections.performance") },
    { id: "dossier-maturity", label: t("dossiers.sections.maturity") },
    { id: "dossier-deals", label: t("dossiers.sections.deals") },
    { id: "dossier-pricing", label: t("dossiers.sections.pricing") },
    { id: "dossier-partnerships", label: t("dossiers.sections.partnerships") },
    { id: "dossier-competitors", label: t("dossiers.sections.competitors") },
    { id: "dossier-patents", label: t("dossiers.sections.patents") },
    { id: "dossier-tenders", label: t("dossiers.sections.tendersAndForecasts") },
    { id: "dossier-gaps", label: t("dossiers.sections.risksAndGaps") },
    { id: "dossier-bd", label: t("dossiers.sections.bdImplications") },
    { id: "dossier-changed", label: t("dossiers.sections.whatChanged") },
    { id: "dossier-sources", label: t("dossiers.sections.sources") },
  ];

  const specColumns: DossierTableColumn<DossierSpecRow>[] = [
    { key: "parameter", label: t("dossiers.table.colParameter"), render: (r) => <DossierPlainText text={r.parameter_he} /> },
    { key: "value", label: t("dossiers.table.colValue"), render: (r) => <DossierPlainText text={r.value} /> },
    { key: "unit", label: t("dossiers.table.colUnit"), render: (r) => <DossierPlainText text={r.unit} /> },
    { key: "variant", label: t("dossiers.table.colVariant"), render: (r) => <DossierPlainText text={r.variant} /> },
    { key: "source_kind", label: t("dossiers.table.colSourceKind"), render: (r) => <DossierPlainText text={r.source_kind} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const versionColumns: DossierTableColumn<DossierVersionRow>[] = [
    { key: "name", label: t("dossiers.table.colName"), render: (r) => <DossierPlainText text={r.name} /> },
    { key: "year", label: t("dossiers.table.colYear"), render: (r) => <DossierPlainText text={r.year != null ? String(r.year) : null} /> },
    { key: "changes", label: t("dossiers.table.colChanges"), render: (r) => <DossierPlainText text={r.changes_he} /> },
    { key: "platforms", label: t("dossiers.table.colPlatforms"), render: (r) => <DossierPlainText text={r.platforms.join(", ") || null} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const performanceColumns: DossierTableColumn<DossierPerformanceRow>[] = [
    { key: "metric", label: t("dossiers.table.colMetric"), render: (r) => <DossierPlainText text={r.metric_he} /> },
    { key: "claimed", label: t("dossiers.table.colClaimed"), render: (r) => <DossierPlainText text={r.claimed_value} /> },
    { key: "demonstrated", label: t("dossiers.table.colDemonstrated"), render: (r) => <DossierPlainText text={r.tested_value} /> },
    { key: "conditions", label: t("dossiers.table.colConditions"), render: (r) => <DossierPlainText text={r.conditions_he} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const dealColumns: DossierTableColumn<DossierDealRow>[] = [
    { key: "date", label: t("dossiers.table.colDate"), render: (r) => <DossierPlainText text={r.date} /> },
    {
      key: "customer",
      label: t("dossiers.table.colCustomer"),
      render: (r) => <DossierPlainText text={[r.customer, r.country].filter(Boolean).join(" · ") || null} />,
    },
    {
      key: "kind",
      label: t("dossiers.table.colKind"),
      render: (r) => <DossierPlainText text={r.platform ? `${r.kind} (${r.platform})` : r.kind} />,
    },
    {
      key: "amount",
      label: t("dossiers.table.colAmount"),
      render: (r) => <DossierPlainText text={r.amount && r.currency ? `${r.amount} ${r.currency}` : r.amount} />,
    },
    { key: "quantity", label: t("dossiers.table.colQuantity"), render: (r) => <DossierPlainText text={r.quantity != null ? String(r.quantity) : null} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const priceColumns: DossierTableColumn<DossierPriceRow>[] = [
    { key: "figure", label: t("dossiers.table.colFigure"), render: (r) => <DossierPlainText text={r.figure} /> },
    { key: "currency", label: t("dossiers.table.colUnit"), render: (r) => <DossierPlainText text={r.currency} /> },
    { key: "basis", label: t("dossiers.table.colBasis"), render: (r) => <DossierPlainText text={r.basis_he} /> },
    { key: "date", label: t("dossiers.table.colDate"), render: (r) => <DossierPlainText text={r.date} /> },
    { key: "source_kind", label: t("dossiers.table.colSourceKind"), render: (r) => <DossierPlainText text={r.source_kind} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const partnerColumns: DossierTableColumn<DossierPartnerRow>[] = [
    { key: "partner", label: t("dossiers.table.colPartner"), render: (r) => <DossierPlainText text={r.partner} /> },
    { key: "role", label: t("dossiers.table.colRole"), render: (r) => <DossierPlainText text={r.role_he} /> },
    { key: "since", label: t("dossiers.table.colSince"), render: (r) => <DossierPlainText text={r.since} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const competitorColumns: DossierTableColumn<DossierCompetitorRow>[] = [
    { key: "product", label: t("dossiers.table.colProduct"), render: (r) => <DossierPlainText text={r.product} /> },
    { key: "vendor", label: t("dossiers.table.colVendor"), render: (r) => <DossierPlainText text={r.vendor} /> },
    { key: "comparison", label: t("dossiers.table.colComparison"), render: (r) => <DossierPlainText text={r.comparison_he} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const patentColumns: DossierTableColumn<DossierPatentRef>[] = [
    { key: "pub_number", label: t("dossiers.table.colPubNumber"), render: (r) => <DossierPlainText text={r.pub_number} /> },
    { key: "title", label: t("dossiers.table.colTitle"), render: (r) => <DossierPlainText text={r.title} /> },
    { key: "assignee", label: t("dossiers.table.colAssignee"), render: (r) => <DossierPlainText text={r.assignee} /> },
    { key: "relevance", label: t("dossiers.table.colRelevance"), render: (r) => <DossierPlainText text={r.relevance_he} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const tenderColumns: DossierTableColumn<DossierTenderRef>[] = [
    { key: "title", label: t("dossiers.table.colTitle"), render: (r) => <DossierPlainText text={r.title} /> },
    { key: "status", label: t("dossiers.table.colStatus"), render: (r) => <DossierPlainText text={r.status} /> },
    { key: "relevance", label: t("dossiers.table.colRelevance"), render: (r) => <DossierPlainText text={r.relevance_he} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const sourceColumns: DossierTableColumn<{ n: number; url: string; title: string; kind: string | null; reliability: string | null; accessed_at: string | null }>[] = [
    { key: "n", label: t("dossiers.table.colN"), render: (r) => <span className="font-mono">{r.n}</span> },
    {
      key: "title",
      label: t("dossiers.table.colTitle"),
      render: (r) => (
        <a href={r.url} target="_blank" rel="noopener noreferrer" className="text-accent hover:underline">
          <bdi>{r.title || r.url}</bdi>
        </a>
      ),
    },
    { key: "kind", label: t("dossiers.table.colSourceKind"), render: (r) => <DossierPlainText text={r.kind} /> },
    { key: "reliability", label: t("dossiers.table.colReliability"), render: (r) => <DossierPlainText text={r.reliability} /> },
    { key: "accessed", label: t("dossiers.table.colAccessed"), render: (r) => <DossierPlainText text={r.accessed_at ? formatDateTime(r.accessed_at, locale) : null} /> },
  ];

  return (
    <div className="space-y-4 p-4 md:p-6">
      <DossierSectionNav items={NAV_ITEMS} />

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold text-fg">
            <bdi>{d.product_name}</bdi>
          </h2>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-fg-dim">
            {d.vendor && (
              <span className="rounded-full bg-bg-sunken px-2 py-0.5">
                <bdi>{d.vendor}</bdi>
              </span>
            )}
            {data?.identity.status_he && (
              <span className="rounded-full bg-accent-muted px-2 py-0.5 text-accent-fg">
                <bdi>{data.identity.status_he}</bdi>
              </span>
            )}
            {data?.maturity.trl != null && (
              <span className="rounded-full bg-bg-sunken px-2 py-0.5">
                {t("dossiers.maturity.trlLabel")}: {data.maturity.trl}
              </span>
            )}
            {latestRun?.confidence != null && (
              <span className="rounded-full bg-bg-sunken px-2 py-0.5">
                {t("dossiers.confidenceLabel", { pct: Math.round(latestRun.confidence * 100) })}
              </span>
            )}
            {latestRun && (
              <span>
                {t("dossiers.lastRunLabel")}: {formatDateTime(latestRun.created_at, locale)}
              </span>
            )}
          </div>
          {d.aliases.length > 0 && (
            <p className="mt-1 flex flex-wrap items-center gap-1 text-xs text-fg-dim">
              {d.aliases.map((a) => (
                <span key={a} className="rounded-full bg-bg-sunken px-2 py-0.5">
                  <bdi>{a}</bdi>
                </span>
              ))}
            </p>
          )}
        </div>

        <div className="flex shrink-0 flex-col items-end gap-1">
          <button
            type="button"
            onClick={() => rerunMutation.mutate()}
            disabled={rerunMutation.isPending}
            data-testid="dossier-detail-rerun"
            className="flex items-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-60"
          >
            {rerunMutation.isPending && <Loader2 size={14} className="animate-spin" aria-hidden="true" />}
            {t("dossiers.rerun")}
          </button>
          {latestRun?.report_id && (
            <Link to={`/reports?id=${latestRun.report_id}`} className="text-xs text-accent hover:underline">
              {t("dossiers.openReport")}
            </Link>
          )}
        </div>
      </div>

      {d.pending_job && (
        <div role="status" className="rounded-md border border-border-strong bg-bg-sunken px-3 py-2 text-sm text-fg-dim">
          <p>{t("dossiers.pendingBanner")}</p>
          <DossierProgressList progress={d.pending_job.progress} />
        </div>
      )}

      {!data ? (
        <EmptyState title={t("dossiers.notFound")} />
      ) : (
        <div className="space-y-6">
          <section id="dossier-summary" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.summary")}</h3>
            <DossierSentenceList sentences={data.summary} citations={citations} emptyLabel={t("dossiers.notFoundInSources")} />
            <dl className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
              <div>
                <dt className="text-fg-dim">{t("dossiers.identity.familyLabel")}</dt>
                <dd><DossierFactText text={data.identity.product_family} cites={data.identity.cites} citations={citations} /></dd>
              </div>
              <div>
                <dt className="text-fg-dim">{t("dossiers.identity.categoryLabel")}</dt>
                <dd><DossierFactText text={data.identity.category_he} cites={data.identity.cites} citations={citations} /></dd>
              </div>
              <div>
                <dt className="text-fg-dim">{t("dossiers.identity.firstAnnouncedLabel")}</dt>
                <dd><DossierPlainText text={data.identity.first_announced} /></dd>
              </div>
            </dl>
          </section>

          <section id="dossier-specifications" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.specifications")}</h3>
            <DossierTable columns={specColumns} rows={data.specifications} rowKey={(r, i) => `${r.parameter_he}-${i}`} emptyLabel={t("dossiers.emptySections.specifications")} caption={t("dossiers.sections.specifications")} />
          </section>

          <section id="dossier-versions" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.versions")}</h3>
            <DossierTable columns={versionColumns} rows={data.variants_and_versions} rowKey={(r, i) => `${r.name}-${i}`} emptyLabel={t("dossiers.emptySections.versions")} caption={t("dossiers.sections.versions")} />
          </section>

          <section id="dossier-performance" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.performance")}</h3>
            <DossierTable columns={performanceColumns} rows={data.performance} rowKey={(r, i) => `${r.metric_he}-${i}`} emptyLabel={t("dossiers.emptySections.performance")} caption={t("dossiers.sections.performance")} />
          </section>

          <section id="dossier-maturity" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.maturity")}</h3>
            <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <div>
                <dt className="text-xs text-fg-dim">{t("dossiers.maturity.trlLabel")}</dt>
                <dd><DossierPlainText text={data.maturity.trl != null ? String(data.maturity.trl) : null} /></dd>
              </div>
              <div>
                <dt className="text-xs text-fg-dim">{t("dossiers.maturity.firstFieldingLabel")}</dt>
                <dd><DossierPlainText text={data.maturity.first_fielding} /></dd>
              </div>
              <div className="col-span-2">
                <dt className="text-xs text-fg-dim">{t("dossiers.maturity.operationalUsersLabel")}</dt>
                <dd><DossierPlainText text={data.maturity.operational_users.join(", ") || null} /></dd>
              </div>
              <div className="col-span-2">
                <dt className="text-xs text-fg-dim">{t("dossiers.maturity.platformsIntegratedLabel")}</dt>
                <dd><DossierPlainText text={data.maturity.platforms_integrated.join(", ") || null} /></dd>
              </div>
            </dl>
            <p className="mt-2 text-sm">
              <DossierFactText text={data.maturity.assessment_he} cites={data.maturity.cites} citations={citations} />
            </p>
          </section>

          <section id="dossier-deals" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.deals")}</h3>
            <DossierTable columns={dealColumns} rows={data.deals} rowKey={(r, i) => `${r.date}-${i}`} emptyLabel={t("dossiers.emptySections.deals")} caption={t("dossiers.sections.deals")} />
            {(data.regulatory_export.export_regime_he || data.regulatory_export.restrictions_he) && (
              <div className="mt-3 rounded-md border border-border bg-bg-sunken p-2.5 text-xs">
                <p className="mb-1 font-semibold text-fg-dim">{t("dossiers.regulatoryExport.title")}</p>
                <p>
                  {t("dossiers.regulatoryExport.regimeLabel")}:{" "}
                  <DossierFactText text={data.regulatory_export.export_regime_he} cites={data.regulatory_export.cites} citations={citations} />
                </p>
                {data.regulatory_export.restrictions_he && (
                  <p>
                    {t("dossiers.regulatoryExport.restrictionsLabel")}:{" "}
                    <DossierFactText text={data.regulatory_export.restrictions_he} cites={data.regulatory_export.cites} citations={citations} />
                  </p>
                )}
              </div>
            )}
          </section>

          <section id="dossier-pricing" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.pricing")}</h3>
            <DossierTable columns={priceColumns} rows={data.pricing} rowKey={(r, i) => `${r.figure}-${i}`} emptyLabel={t("dossiers.emptySections.pricing")} caption={t("dossiers.sections.pricing")} />
          </section>

          <section id="dossier-partnerships" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.partnerships")}</h3>
            <DossierTable columns={partnerColumns} rows={data.partnerships} rowKey={(r, i) => `${r.partner}-${i}`} emptyLabel={t("dossiers.emptySections.partnerships")} caption={t("dossiers.sections.partnerships")} />
          </section>

          <section id="dossier-competitors" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.competitors")}</h3>
            <DossierTable columns={competitorColumns} rows={data.competitors} rowKey={(r, i) => `${r.product}-${i}`} emptyLabel={t("dossiers.emptySections.competitors")} caption={t("dossiers.sections.competitors")} />
          </section>

          <section id="dossier-patents" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.patents")}</h3>
            <DossierTable columns={patentColumns} rows={data.patents} rowKey={(r, i) => `${r.pub_number}-${i}`} emptyLabel={t("dossiers.emptySections.patents")} caption={t("dossiers.sections.patents")} />
          </section>

          <section id="dossier-tenders" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.tendersAndForecasts")}</h3>
            <DossierTable columns={tenderColumns} rows={data.tenders_and_forecasts} rowKey={(r, i) => `${r.title}-${i}`} emptyLabel={t("dossiers.emptySections.tendersAndForecasts")} caption={t("dossiers.sections.tendersAndForecasts")} />
          </section>

          <section id="dossier-gaps" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.risksAndGaps")}</h3>
            <DossierSentenceList sentences={data.risks_and_gaps} citations={citations} emptyLabel={t("dossiers.emptySections.risksAndGaps")} />
          </section>

          <section id="dossier-bd" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.bdImplications")}</h3>
            <DossierSentenceList sentences={data.bd_implications} citations={citations} emptyLabel={t("dossiers.emptySections.bdImplications")} />
          </section>

          <section id="dossier-changed" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.whatChanged")}</h3>
            <DossierSentenceList sentences={data.what_changed} citations={citations} emptyLabel={t("dossiers.emptySections.whatChanged")} />
          </section>

          <section id="dossier-sources" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.sources")}</h3>
            {data.sources.length === 0 ? (
              <NotFoundInSources />
            ) : (
              <DossierTable columns={sourceColumns} rows={data.sources} rowKey={(r) => r.n} emptyLabel={t("dossiers.emptySections.sources")} caption={t("dossiers.sections.sources")} />
            )}
          </section>

          {d.dossiers.length > 0 && (
            <section aria-label={t("dossiers.runHistory.title")}>
              <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.runHistory.title")}</h3>
              <DossierRunHistoryList productKey={d.product_key} runs={d.dossiers} />
            </section>
          )}
        </div>
      )}

      <Link to="/dossiers" className="inline-block text-xs text-accent hover:underline">
        {t("dossiers.backToList")}
      </Link>
    </div>
  );
}
