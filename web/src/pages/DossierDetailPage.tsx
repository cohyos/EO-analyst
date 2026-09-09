import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { api } from "@/api";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { DossierSectionNav, type DossierSectionNavItem } from "@/components/dossiers/DossierSectionNav";
import { DossierTable, type DossierTableColumn } from "@/components/dossiers/DossierTable";
import { DossierSpecTable } from "@/components/dossiers/DossierSpecTable";
import {
  DossierCiteChips,
  DossierFactText,
  DossierPlainText,
  DossierSentenceList,
  NotFoundInSources,
} from "@/components/dossiers/DossierFact";
import { DossierRunHistoryList } from "@/components/dossiers/DossierRunHistoryList";
import { DossierProgressList } from "@/components/dossiers/DossierProgressBanner";
import { eventKindLabel } from "@/components/entities/eventKindLabel";
import type { CitationLike } from "@/components/CitationText";
import { formatDateTime } from "@/lib/time";
import { useI18n, useT } from "@/i18n";
import type {
  DossierClaimReviewRow,
  DossierCompetitorRow,
  DossierDealRow,
  DossierGapTrackingRow,
  DossierPartnerRow,
  DossierPatentRef,
  DossierPlatformRow,
  DossierPriceRow,
  DossierRowConfidence,
  DossierTenderRef,
  DossierTimelineRow,
  DossierVersionRow,
} from "@/types/api";

// LESSONS-2 (2026-09-09, docs/qa/content_review/LESSONS-fable-dossier.md item 4): the shared
// high/medium/low -> localized label helper, used by every confidence-bearing column below.
function confidenceLabel(t: ReturnType<typeof useT>, level: DossierRowConfidence | null | undefined): string | null {
  return level ? t(`dossiers.confidenceLevel.${level}` as never) : null;
}

/** PD-fix-2 (2026-09-08, item 3): Hebrew label for a deal's `kind` -- reuses the shared
 * event-kind map (`eventKindLabel`, `@/components/entities/eventKindLabel`) for the one kind the
 * two enums share (`contract_award`), extended with the deal-only kinds that map doesn't cover;
 * an unrecognised kind falls back to the raw value, same as `eventKindLabel` itself. Mirrors
 * `agent/eoa/dossier/report.py`'s `_deal_kind_label` so the docx/md/html renderer and this page
 * agree on the same Hebrew text. */
const DEAL_KIND_LABEL_HE_EXTRA: Record<string, string> = {
  FMS: "מכירת ציוד ביטחוני זר (FMS)",
  framework: "הסכם מסגרת",
  option: "אופציה בחוזה",
  export_license: "רישיון ייצוא",
};

function dealKindLabel(kind: string | null | undefined): string {
  if (!kind) return "אחר";
  return DEAL_KIND_LABEL_HE_EXTRA[kind] ?? eventKindLabel(kind);
}

/** PD-fix-2 (2026-09-08, item 3): a deal's `date`/backfilled `published_at` can carry a
 * time-of-day and timezone offset (e.g. `"2026-09-02 09:04:00+03:00"`); the deals table shows a
 * date only. Deliberately a plain prefix match (not `formatDate`/`Intl`+timezone conversion,
 * `@/lib/time`) -- a deal date can be partial-precision ("2021-06", year+month only, no day), and
 * reformatting/TZ-shifting a partial date risks changing what it says; this only strips a
 * trailing time/offset when one is present, exactly mirroring `report.py`'s `_date_only`. */
function dealDateOnly(value: string | null | undefined): string | null {
  if (!value) return value ?? null;
  const m = /^(\d{4}(?:-\d{2}(?:-\d{2})?)?)/.exec(value.trim());
  return m ? m[1] : value;
}

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
  const navigate = useNavigate();

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

  // PD-vocab-ui (2026-09-09, docs/PLAN_SPEC_VOCABULARY.md §5.2 entry point 2): the competitors
  // table's own "השווה"/"הרץ סקירה למוצר זה" action needs the full dossier list to best-effort
  // match a competitor's `product`/`vendor` names against an existing dossier -- same list
  // `DossiersPage` itself already fetches under the same query key (react-query dedupes the
  // request rather than firing it twice).
  const dossierListQuery = useQuery({ queryKey: ["dossiers"], queryFn: () => api.getDossiers() });
  const launchCompetitorMutation = useMutation({
    mutationFn: (body: { product_name: string; vendor?: string | null }) => api.postDossier(body),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["dossiers"] });
      navigate(`/dossiers/${res.product_key}`);
    },
  });

  function findMatchingDossier(competitor: DossierCompetitorRow) {
    const target = competitor.product.trim().toLowerCase();
    if (!target) return null;
    return (
      (dossierListQuery.data ?? []).find((s) => s.product_name.trim().toLowerCase() === target) ?? null
    );
  }

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
    { id: "dossier-platforms", label: t("dossiers.sections.platforms") },
    { id: "dossier-performance", label: t("dossiers.sections.performance") },
    { id: "dossier-maturity", label: t("dossiers.sections.maturity") },
    { id: "dossier-timeline", label: t("dossiers.sections.timeline") },
    { id: "dossier-deals", label: t("dossiers.sections.deals") },
    { id: "dossier-pricing", label: t("dossiers.sections.pricing") },
    { id: "dossier-pricing-estimate", label: t("dossiers.sections.pricingEstimate") },
    { id: "dossier-partnerships", label: t("dossiers.sections.partnerships") },
    { id: "dossier-competitors", label: t("dossiers.sections.competitors") },
    { id: "dossier-claims-review", label: t("dossiers.sections.claimsReview") },
    { id: "dossier-patents", label: t("dossiers.sections.patents") },
    { id: "dossier-tenders", label: t("dossiers.sections.tendersAndForecasts") },
    { id: "dossier-gaps", label: t("dossiers.sections.risksAndGaps") },
    { id: "dossier-gaps-tracking", label: t("dossiers.sections.gapsTracking") },
    { id: "dossier-bd", label: t("dossiers.sections.bdImplications") },
    { id: "dossier-changed", label: t("dossiers.sections.whatChanged") },
    { id: "dossier-sources", label: t("dossiers.sections.sources") },
  ];

  const versionColumns: DossierTableColumn<DossierVersionRow>[] = [
    { key: "name", label: t("dossiers.table.colName"), render: (r) => <DossierPlainText text={r.name} /> },
    { key: "year", label: t("dossiers.table.colYear"), render: (r) => <DossierPlainText text={r.year != null ? String(r.year) : null} /> },
    { key: "changes", label: t("dossiers.table.colChanges"), render: (r) => <DossierPlainText text={r.changes_he} /> },
    // LESSONS-2 item 7: "platforms" moved to its own dedicated table (below) -- the freed column
    // slot carries evidence + confidence instead, matching eoa.dossier.report._variants_table.
    { key: "evidence", label: t("dossiers.table.colEvidence"), render: (r) => <DossierPlainText text={r.evidence_he ?? null} /> },
    { key: "confidence", label: t("dossiers.table.colConfidence"), render: (r) => <DossierPlainText text={confidenceLabel(t, r.confidence)} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const platformColumns: DossierTableColumn<DossierPlatformRow>[] = [
    { key: "platform", label: t("dossiers.table.colPlatform"), render: (r) => <DossierPlainText text={r.platform} /> },
    { key: "domain", label: t("dossiers.table.colDomain"), render: (r) => <DossierPlainText text={r.domain} /> },
    { key: "evidence", label: t("dossiers.table.colIntegrationEvidence"), render: (r) => <DossierPlainText text={r.integration_evidence_he} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const timelineKindLabel = (kind: string): string => t(`dossiers.timelineKind.${kind}` as never) ?? kind;

  const timelineColumns: DossierTableColumn<DossierTimelineRow>[] = [
    { key: "date", label: t("dossiers.table.colDate"), render: (r) => <DossierPlainText text={r.date} /> },
    { key: "event", label: t("dossiers.table.colEvent"), render: (r) => <DossierPlainText text={r.event_he} /> },
    { key: "kind", label: t("dossiers.table.colKind"), render: (r) => <DossierPlainText text={timelineKindLabel(r.kind)} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const claimsReviewColumns: DossierTableColumn<DossierClaimReviewRow>[] = [
    { key: "claim", label: t("dossiers.table.colClaim"), render: (r) => <DossierPlainText text={r.claim_he} /> },
    { key: "basis", label: t("dossiers.table.colBasis"), render: (r) => <DossierPlainText text={r.basis_he} /> },
    { key: "verifiability", label: t("dossiers.table.colVerifiability"), render: (r) => <DossierPlainText text={r.verifiability_he} /> },
    { key: "comparability", label: t("dossiers.table.colComparability"), render: (r) => <DossierPlainText text={r.comparability_he} /> },
    { key: "verdict", label: t("dossiers.table.colVerdict"), render: (r) => <DossierPlainText text={t(`dossiers.claimVerdict.${r.verdict}` as never) ?? r.verdict} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const gapsTrackingColumns: DossierTableColumn<DossierGapTrackingRow>[] = [
    { key: "gap", label: t("dossiers.table.colGap"), render: (r) => <DossierPlainText text={r.gap_he} /> },
    { key: "status", label: t("dossiers.table.colStatus"), render: (r) => <DossierPlainText text={t(`dossiers.gapStatus.${r.status}` as never) ?? r.status} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const dealColumns: DossierTableColumn<DossierDealRow>[] = [
    {
      key: "date",
      label: t("dossiers.table.colDate"),
      render: (r) => {
        const text = dealDateOnly(r.date);
        // PD-fix item 3: a date backfilled from the cited source's own publish date (never the
        // actual deal-closing date) says so, rather than reading as indistinguishable from one
        // that was -- mirrors `report.py`'s `_deal_date_cell`.
        const suffix = r.date && r.date_kind === "published" ? " (תאריך פרסום)" : "";
        return <DossierPlainText text={text ? `${text}${suffix}` : null} />;
      },
    },
    {
      key: "customer",
      label: t("dossiers.table.colCustomer"),
      render: (r) => {
        // PD-fix-2 item 3: a region-only source ("Asia-Pacific country") never fabricates a
        // specific country here -- when `country` is empty, `region_he` carries the region text.
        const place = r.country || r.region_he;
        return <DossierPlainText text={[r.customer, place].filter(Boolean).join(" · ") || null} />;
      },
    },
    {
      key: "kind",
      label: t("dossiers.table.colKind"),
      render: (r) => {
        const label = dealKindLabel(r.kind);
        return <DossierPlainText text={r.platform ? `${label} (${r.platform})` : label} />;
      },
    },
    {
      key: "amount",
      label: t("dossiers.table.colAmount"),
      render: (r) => <DossierPlainText text={r.amount && r.currency ? `${r.amount} ${r.currency}` : r.amount} />,
    },
    { key: "quantity", label: t("dossiers.table.colQuantity"), render: (r) => <DossierPlainText text={r.quantity != null ? String(r.quantity) : null} /> },
    // LESSONS-2 item 4: the deals table is already at the 6-column cap (date/customer/kind/amount/
    // quantity/sources) -- confidence is merged into the citations cell rather than a 7th column,
    // matching eoa.dossier.report._deal_cite_cell_with_confidence.
    {
      key: "cites",
      label: t("dossiers.table.colSources"),
      render: (r) => (
        <span className="inline-flex flex-wrap items-center gap-1">
          <DossierCiteChips cites={r.cites} citations={citations} />
          {r.confidence_level && (
            <span className="whitespace-nowrap text-fg-dim">({confidenceLabel(t, r.confidence_level)})</span>
          )}
        </span>
      ),
    },
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
    { key: "confidence", label: t("dossiers.table.colConfidence"), render: (r) => <DossierPlainText text={confidenceLabel(t, r.confidence)} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
  ];

  const competitorColumns: DossierTableColumn<DossierCompetitorRow>[] = [
    { key: "product", label: t("dossiers.table.colProduct"), render: (r) => <DossierPlainText text={r.product} /> },
    { key: "vendor", label: t("dossiers.table.colVendor"), render: (r) => <DossierPlainText text={r.vendor} /> },
    { key: "comparison", label: t("dossiers.table.colComparison"), render: (r) => <DossierPlainText text={r.comparison_he} /> },
    { key: "confidence", label: t("dossiers.table.colConfidence"), render: (r) => <DossierPlainText text={confidenceLabel(t, r.confidence)} /> },
    { key: "cites", label: t("dossiers.table.colSources"), render: (r) => <DossierCiteChips cites={r.cites} citations={citations} /> },
    {
      key: "action",
      label: t("dossiers.table.colAction"),
      render: (r) => {
        const match = findMatchingDossier(r);
        if (match) {
          return (
            <Link
              to={`/dossiers/compare?keys=${d.product_key},${match.product_key}`}
              className="whitespace-nowrap text-accent hover:underline"
            >
              {t("dossiers.compareLink")}
            </Link>
          );
        }
        const pending =
          launchCompetitorMutation.isPending && launchCompetitorMutation.variables?.product_name === r.product;
        return (
          <button
            type="button"
            onClick={() => launchCompetitorMutation.mutate({ product_name: r.product, vendor: r.vendor })}
            disabled={pending}
            className="whitespace-nowrap text-xs text-accent hover:underline disabled:opacity-60"
          >
            {pending ? t("common.loading") : t("dossiers.runForCompetitor")}
          </button>
        );
      },
    },
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
            <DossierSpecTable
              table="specifications"
              rows={data.specifications}
              otherRows={data.other_specifications ?? []}
              productLine={d.product_line ?? null}
              citations={citations}
              emptyLabel={t("dossiers.emptySections.specifications")}
              caption={t("dossiers.sections.specifications")}
            />
          </section>

          <section id="dossier-versions" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.versions")}</h3>
            <DossierTable columns={versionColumns} rows={data.variants_and_versions} rowKey={(r, i) => `${r.name}-${i}`} emptyLabel={t("dossiers.emptySections.versions")} caption={t("dossiers.sections.versions")} />
          </section>

          {/* LESSONS-2 item 7: platforms as their own table -- built deterministically by the
              backend from maturity.platforms_integrated + every deal's own platform. */}
          <section id="dossier-platforms" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.platforms")}</h3>
            <DossierTable columns={platformColumns} rows={data.platforms ?? []} rowKey={(r, i) => `${r.platform}-${i}`} emptyLabel={t("dossiers.emptySections.platforms")} caption={t("dossiers.sections.platforms")} />
          </section>

          <section id="dossier-performance" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.performance")}</h3>
            <DossierSpecTable
              table="performance"
              rows={data.performance}
              productLine={d.product_line ?? null}
              citations={citations}
              emptyLabel={t("dossiers.emptySections.performance")}
              caption={t("dossiers.sections.performance")}
            />
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

          {/* LESSONS-2 item 1: chronological timeline built from deals/programme-deals/variants/
              identity.first_announced (deterministic) plus LLM-extracted milestones with a real
              cited date. */}
          <section id="dossier-timeline" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.timeline")}</h3>
            <DossierTable columns={timelineColumns} rows={data.timeline ?? []} rowKey={(r, i) => `${r.date}-${i}`} emptyLabel={t("dossiers.emptySections.timeline")} caption={t("dossiers.sections.timeline")} />
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

          {/* LESSONS-2 item 2: a labelled analyst pricing ESTIMATE, wholly separate from the
              published-figures "pricing" section above -- only ever present when the backend's
              gate (a cited contract total with duration/scope AND a cited market anchor) passed;
              the disclaimer is rendered unconditionally whenever the block is present. */}
          <section id="dossier-pricing-estimate" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.pricingEstimate")}</h3>
            {!data.pricing_estimate ? (
              <p className="text-sm text-fg-dim">{t("dossiers.emptySections.pricingEstimate")}</p>
            ) : (
              <div className="space-y-3 rounded-md border border-border bg-bg-sunken p-3 text-sm">
                <p className="text-xs font-semibold text-level-orange">{t("dossiers.pricingEstimate.disclaimer")}</p>
                <p>
                  <span className="text-fg-dim">{t("dossiers.pricingEstimate.methodLabel")}: </span>
                  <DossierPlainText text={data.pricing_estimate.method_he} />
                </p>
                {data.pricing_estimate.assumptions.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-semibold text-fg-dim">{t("dossiers.pricingEstimate.assumptionsLabel")}</p>
                    <ul className="list-disc space-y-1 ps-5">
                      {data.pricing_estimate.assumptions.map((a, i) => (
                        <li key={i}><DossierFactText text={a.text_he} cites={a.cites} citations={citations} /></li>
                      ))}
                    </ul>
                  </div>
                )}
                {(data.pricing_estimate.range_low != null || data.pricing_estimate.range_high != null) && (
                  <p>
                    <span className="text-fg-dim">{t("dossiers.pricingEstimate.rangeLabel")}: </span>
                    {[data.pricing_estimate.range_low, data.pricing_estimate.range_high]
                      .map((v) => (v != null ? v.toLocaleString() : "—"))
                      .join("–")}
                    {data.pricing_estimate.currency ? ` ${data.pricing_estimate.currency}` : ""}
                  </p>
                )}
                {data.pricing_estimate.market_anchors.length > 0 && (
                  <div>
                    <p className="mb-1 text-xs font-semibold text-fg-dim">{t("dossiers.pricingEstimate.marketAnchorsLabel")}</p>
                    <DossierTable
                      columns={[
                        { key: "product", label: t("dossiers.table.colComparableProduct"), render: (r: { product_he: string }) => <DossierPlainText text={r.product_he} /> },
                        { key: "range", label: t("dossiers.table.colPriceRange"), render: (r: { price_range_he: string }) => <DossierPlainText text={r.price_range_he} /> },
                        { key: "cites", label: t("dossiers.table.colSources"), render: (r: { cites: number[] }) => <DossierCiteChips cites={r.cites} citations={citations} /> },
                      ]}
                      rows={data.pricing_estimate.market_anchors}
                      rowKey={(r, i) => `${r.product_he}-${i}`}
                      emptyLabel={t("dossiers.emptySections.pricingEstimate")}
                      caption={t("dossiers.pricingEstimate.marketAnchorsLabel")}
                    />
                  </div>
                )}
              </div>
            )}
          </section>

          <section id="dossier-partnerships" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.partnerships")}</h3>
            <DossierTable columns={partnerColumns} rows={data.partnerships} rowKey={(r, i) => `${r.partner}-${i}`} emptyLabel={t("dossiers.emptySections.partnerships")} caption={t("dossiers.sections.partnerships")} />
          </section>

          <section id="dossier-competitors" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.competitors")}</h3>
            <DossierTable columns={competitorColumns} rows={data.competitors} rowKey={(r, i) => `${r.product}-${i}`} emptyLabel={t("dossiers.emptySections.competitors")} caption={t("dossiers.sections.competitors")} />
          </section>

          {/* LESSONS-2 item 3: up to 5 critically-reviewed vendor claims. */}
          <section id="dossier-claims-review" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.claimsReview")}</h3>
            <DossierTable columns={claimsReviewColumns} rows={data.claims_review ?? []} rowKey={(r, i) => `${r.claim_he}-${i}`} emptyLabel={t("dossiers.emptySections.claimsReview")} caption={t("dossiers.sections.claimsReview")} />
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

          {/* LESSONS-2 item 5: cross-run gap tracking (closed/open/new), when the backend's
              gap-status lane has produced data for this product. */}
          <section id="dossier-gaps-tracking" className="scroll-mt-16">
            <h3 className="mb-2 text-sm font-semibold text-fg">{t("dossiers.sections.gapsTracking")}</h3>
            <DossierTable columns={gapsTrackingColumns} rows={data.gaps_tracking ?? []} rowKey={(r, i) => `${r.gap_he}-${i}`} emptyLabel={t("dossiers.emptySections.gapsTracking")} caption={t("dossiers.sections.gapsTracking")} />
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
