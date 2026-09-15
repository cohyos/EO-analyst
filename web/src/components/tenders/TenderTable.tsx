import { ContentShareActions } from "@/components/ContentShareActions";
import { Fragment, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ExternalLink, ThumbsDown, ThumbsUp } from "lucide-react";
import type { TenderCard, TenderFeedbackVerdict } from "@/types/api";
import { countryFlagEmoji } from "@/lib/countryFlag";
import { formatDate } from "@/lib/time";
import { cn } from "@/lib/cn";
import {
  DEADLINE_URGENT_DAYS,
  TENDER_CANDIDATE_BADGE_LABEL,
  TENDER_INTAKE_CHIP_CLASS,
  TENDER_STATUS_CHIP_CLASS,
  TENDER_STATUS_LABEL,
  daysLeft,
  relevanceScorePercent,
  tenderSourceLabel,
} from "@/lib/tenders";
import { EmptyState } from "@/components/states";
import { SourcePreviewPopover } from "@/components/SourcePreviewPopover";
import { useIsNarrowViewport } from "@/hooks/useIsNarrowViewport";
import { useT } from "@/i18n";

function DeadlineChip({ deadline }: { deadline: string | null }) {
  if (!deadline) return <span className="text-fg-dim">—</span>;
  const days = daysLeft(deadline);
  const urgent = days != null && days < DEADLINE_URGENT_DAYS;
  const daysLabel =
    days == null ? "" : days < 0 ? `עברו ${Math.abs(days)} ימים` : `נותרו ${days} ימים`;
  return (
    <span
      className={cn(
        "inline-flex flex-col items-start gap-0.5 rounded-md px-1.5 py-0.5 text-xs",
        urgent ? "bg-danger/15 text-danger" : "bg-bg-sunken text-fg-dim",
      )}
      title={daysLabel}
    >
      <span className="font-mono">{formatDate(deadline)}</span>
      {daysLabel && <span className="font-mono">{daysLabel}</span>}
    </span>
  );
}

function RelevanceDots({ value }: { value: number | null }) {
  const v = Math.max(0, Math.min(5, value ?? 0));
  return (
    <span
      className="flex items-center gap-0.5"
      role="img"
      aria-label={`רלוונטיות ${v} מתוך 5`}
      title={`רלוונטיות: ${v}/5`}
    >
      {[1, 2, 3, 4, 5].map((i) => (
        <span
          key={i}
          className={cn("h-1.5 w-1.5 rounded-full", i <= v ? "bg-accent" : "bg-border-strong")}
        />
      ))}
    </span>
  );
}

// W2b: quick one-click 👍/👎 shared by both the compact row control and the expanded detail row's
// reason-carrying variant.
function FeedbackButtons({
  onVote,
  disabled,
}: {
  onVote: (verdict: TenderFeedbackVerdict) => void;
  disabled?: boolean;
}) {
  const t = useT();
  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        disabled={disabled}
        onClick={(e) => {
          e.stopPropagation();
          onVote("relevant");
        }}
        aria-label={t("tenders.feedback.thumbsUpAria")}
        title={t("tenders.feedback.thumbsUpAria")}
        className="tap-target inline-flex items-center justify-center rounded p-1 text-fg-muted hover:bg-ok/15 hover:text-ok disabled:opacity-50"
      >
        <ThumbsUp size={14} aria-hidden="true" />
      </button>
      <button
        type="button"
        disabled={disabled}
        onClick={(e) => {
          e.stopPropagation();
          onVote("irrelevant");
        }}
        aria-label={t("tenders.feedback.thumbsDownAria")}
        title={t("tenders.feedback.thumbsDownAria")}
        className="tap-target inline-flex items-center justify-center rounded p-1 text-fg-muted hover:bg-danger/15 hover:text-danger disabled:opacity-50"
      >
        <ThumbsDown size={14} aria-hidden="true" />
      </button>
    </span>
  );
}

// Mobile fix (UI-MOBILE-iphone.md #5): factored out of the former `TenderDetailRow` so both the
// desktop table's expanded `<tr>` and the `<md:` card list's expanded panel render identical
// content from one implementation instead of two copies drifting apart.
function TenderDetailContent({
  t,
  onFeedback,
}: {
  t: TenderCard;
  onFeedback: (tenderId: number, verdict: TenderFeedbackVerdict, reason?: string) => void;
}) {
  const translate = useT();
  const [reason, setReason] = useState("");
  const whyRelevant = [t.matched_terms.join(", "), t.summary_he].filter(Boolean).join(" — ");
  return (
    <>
      <ContentShareActions title={t.title ?? undefined} links={[{ url: t.url, title: t.title }]} />
      {whyRelevant && (
        <p className="mb-2">
          <span className="text-fg-dim">{translate("tenders.whyRelevantPrefix")}</span>
          <bdi dir="auto">{whyRelevant}</bdi>
        </p>
      )}
      <div className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-fg-dim">
        <span>
          פורסם: <span className="font-mono text-fg">{formatDate(t.published_at)}</span>
        </span>
        <span>
          גוף מזמין: <bdi className="text-fg">{t.agency ?? "—"}</bdi>
        </span>
        <span>
          מדינה: <span className="font-mono text-fg">{t.country ?? "—"}</span>
        </span>
        <span>
          ציון רלוונטיות:{" "}
          <span className="font-mono text-fg">{relevanceScorePercent(t.relevance_score)}</span>
        </span>
      </div>
      <div className="grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
        {t.entities.length > 0 && (
          <div>
            <span className="text-fg-dim">ישויות: </span>
            <span className="inline-flex flex-wrap gap-1">
              {t.entities.map((e) => (
                <span key={e} className="rounded-full bg-bg-raised px-2 py-0.5">
                  <bdi>{e}</bdi>
                </span>
              ))}
            </span>
          </div>
        )}
        {t.cpv_naics.length > 0 && (
          <div>
            <span className="text-fg-dim">CPV/NAICS: </span>
            <span className="font-mono">{t.cpv_naics.join(", ")}</span>
          </div>
        )}
      </div>
      {t.item_id != null && (
        <Link to={`/items/${t.item_id}`} className="mt-2 inline-block text-accent hover:underline">
          פתח פריט מקושר ←
        </Link>
      )}
      {/* W2b: a reason-carrying feedback control, separate from the compact row's one-click
          vote -- filling in a reason here and clicking a verdict submits both together. */}
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-border pt-2">
        <FeedbackButtons onVote={(verdict) => onFeedback(t.id, verdict, reason.trim() || undefined)} />
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          placeholder={translate("tenders.feedback.reasonPlaceholder")}
          className="min-w-0 flex-1 rounded-md border border-border bg-bg px-2 py-1 text-xs text-fg placeholder:text-fg-dim"
        />
      </div>
    </>
  );
}

function TenderDetailRow({
  t,
  onFeedback,
}: {
  t: TenderCard;
  onFeedback: (tenderId: number, verdict: TenderFeedbackVerdict, reason?: string) => void;
}) {
  return (
    <tr className="border-t border-border bg-bg-sunken/60">
      <td data-share-content colSpan={9} className="p-3 text-xs">
        <TenderDetailContent t={t} onFeedback={onFeedback} />
      </td>
    </tr>
  );
}

// Mobile fix (#5): the title link + "candidate" badge, shared verbatim by the desktop table cell
// and the mobile card so there is exactly one implementation of the source-preview-on-hover/tap
// behavior (R10-preview) to keep in sync, instead of two copies of the same interactive element.
function TenderTitleLink({
  t,
  titleClassName,
  wrapperClassName,
}: {
  t: TenderCard;
  titleClassName?: string;
  /** The mobile card needs this block to actually claim the row's available width (`min-w-0
   * flex-1`) so `line-clamp-3` has something to wrap against and the expand chevron next to it
   * gets pushed to the row's true edge, instead of sizing to content and sitting flush against
   * the chevron. The table cell doesn't need it (the `<td>` already constrains the width). */
  wrapperClassName?: string;
}) {
  return (
    <div className={cn("flex items-start gap-1.5", wrapperClassName)}>
      {t.url ? (
        // R10-preview (2026-09-07): hovering/focusing shows the summary before the link's own
        // click still opens the source directly -- reading first, leaving second. On touch the
        // first tap opens the preview instead.
        <SourcePreviewPopover itemId={t.item_id} fallback={{ title: t.title, sourceName: t.source, url: t.url }}>
          <a
            href={t.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            className="flex min-w-0 items-start gap-1 text-fg hover:text-accent hover:underline"
          >
            <bdi className={cn("min-w-0", titleClassName)}>{t.title || "(ללא כותרת)"}</bdi>
            <ExternalLink size={12} className="mt-0.5 shrink-0" aria-hidden="true" />
          </a>
        </SourcePreviewPopover>
      ) : (
        <bdi className={cn("min-w-0", titleClassName)}>{t.title || "(ללא כותרת)"}</bdi>
      )}
      {/* W2b: a 'candidate' (below the learned threshold) is shown but visually distinguished
          from a confirmed/accepted tender. */}
      {t.intake === "candidate" && (
        <span
          className={cn("shrink-0 rounded-full px-1.5 py-0.5 text-xs font-medium", TENDER_INTAKE_CHIP_CLASS.candidate)}
        >
          {TENDER_CANDIDATE_BADGE_LABEL}
        </span>
      )}
    </div>
  );
}

// Mobile fix (#5): the title column sits mid-table, so at the default RTL scroll position on a
// 282px-wide phone screen it showed mid-word fragments ("ES SOUGHT-") instead of the title. Below
// `md`, `TenderTable` renders this card list instead of the table -- title full width
// (line-clamp-3) on top, then the agency/country/deadline/status meta as a wrapping chip row,
// with the same click target (row toggles the expanded detail panel) and the same title link
// (`TenderTitleLink`) as the table.
function TenderMobileCard({
  t,
  expanded,
  onToggleExpand,
  onFeedback,
}: {
  t: TenderCard;
  expanded: boolean;
  onToggleExpand: (id: number) => void;
  onFeedback: (tenderId: number, verdict: TenderFeedbackVerdict, reason?: string) => void;
}) {
  return (
    <div className="border-b border-border last:border-b-0">
      <div
        onClick={() => onToggleExpand(t.id)}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggleExpand(t.id);
          }
        }}
        className="cursor-pointer p-3 hover:bg-bg-sunken"
      >
        <div className="mb-1.5 flex items-start justify-between gap-2">
          <TenderTitleLink t={t} titleClassName="line-clamp-3 font-medium" wrapperClassName="min-w-0 flex-1" />
          <ChevronDown
            size={14}
            className={cn("mt-0.5 shrink-0 text-fg-dim transition-transform", expanded && "rotate-180")}
            aria-hidden="true"
          />
        </div>
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-fg-muted">
          <span className="inline-flex items-center gap-1">
            <span aria-hidden="true">{countryFlagEmoji(t.country)}</span>
            <span className="font-mono">{t.country ?? "—"}</span>
          </span>
          {t.agency && (
            <bdi className="max-w-[12rem] truncate rounded-full bg-bg-sunken px-1.5 py-0.5">{t.agency}</bdi>
          )}
          <DeadlineChip deadline={t.deadline} />
          <span className={cn("rounded-md px-1.5 py-0.5 font-medium", TENDER_STATUS_CHIP_CLASS[t.status])}>
            {TENDER_STATUS_LABEL[t.status]}
          </span>
          <RelevanceDots value={t.relevance} />
        </div>
        <div className="mt-1.5">
          <FeedbackButtons onVote={(verdict) => onFeedback(t.id, verdict)} />
        </div>
      </div>
      {expanded && (
        <div data-share-content className="border-t border-border bg-bg-sunken/60 p-3 text-xs">
          <TenderDetailContent t={t} onFeedback={onFeedback} />
        </div>
      )}
    </div>
  );
}

export function TenderTable({
  tenders,
  expandedId,
  onToggleExpand,
  onFeedback,
}: {
  tenders: TenderCard[];
  expandedId: number | null;
  onToggleExpand: (id: number) => void;
  onFeedback: (tenderId: number, verdict: TenderFeedbackVerdict, reason?: string) => void;
}) {
  const t = useT();
  if (tenders.length === 0) {
    return (
      <EmptyState
        title={t("tenders.emptyFilteredTitle")}
        description={t("tenders.emptyFilteredDescription")}
      />
    );
  }
  // Computed here (not inside the `tenders.map((t) => ...)` below, where `t` is shadowed by each
  // row's TenderCard) so the relevance-score cell can still reach the translate function.
  const relevanceScoreAriaLabel = (score: string) => t("tenders.relevanceScoreAria", { score });

  // Mobile fix (UI-MOBILE-iphone.md #5): below `md`, a card list replaces the table entirely --
  // the title column sits mid-table, so a horizontally-scrolling table always opened on a
  // mid-word fragment of it on phones ("ES SOUGHT-"). Picked in JS (not a CSS `md:hidden` /
  // `hidden md:block` pair on both variants) so only one of them is ever actually mounted --
  // rendering both and hiding one with CSS would double up every interactive element (source
  // links, feedback buttons, the expand toggle) in the accessibility tree and in tests, which
  // don't apply the app's stylesheet.
  const isMobile = useIsNarrowViewport(768);
  if (isMobile) {
    return (
      <div className="rounded-lg border border-border">
        {tenders.map((tender) => (
          <TenderMobileCard
            key={tender.id}
            t={tender}
            expanded={expandedId === tender.id}
            onToggleExpand={onToggleExpand}
            onFeedback={onFeedback}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full min-w-[960px] text-sm">
        <thead className="bg-bg-raised text-xs text-fg-dim">
          <tr>
            <th className="p-2 text-start"></th>
            <th className="p-2 text-start">מועד סגירה</th>
            <th className="p-2 text-start">כותרת</th>
            <th className="p-2 text-start">גוף מזמין / מדינה</th>
            <th className="p-2 text-start">מקור</th>
            <th className="p-2 text-start">רלוונטיות</th>
            <th className="p-2 text-start">מונחים תואמים</th>
            <th className="p-2 text-start">סטטוס</th>
            <th className="p-2 text-start">משוב</th>
          </tr>
        </thead>
        <tbody>
          {tenders.map((t) => {
            const expanded = expandedId === t.id;
            return (
              <Fragment key={t.id}>
                <tr
                  onClick={() => onToggleExpand(t.id)}
                  className="cursor-pointer border-t border-border hover:bg-bg-sunken"
                  aria-expanded={expanded}
                >
                  <td className="p-2 text-fg-dim">
                    <ChevronDown
                      size={14}
                      className={cn("transition-transform", expanded && "rotate-180")}
                      aria-hidden="true"
                    />
                  </td>
                  <td className="p-2">
                    <DeadlineChip deadline={t.deadline} />
                  </td>
                  <td className="p-2">
                    <TenderTitleLink t={t} />
                  </td>
                  <td className="p-2 text-fg-muted">
                    <div className="flex items-center gap-1.5">
                      <span aria-hidden="true">{countryFlagEmoji(t.country)}</span>
                      <span className="font-mono text-xs">{t.country ?? "—"}</span>
                    </div>
                    <bdi className="block truncate text-xs">{t.agency ?? "—"}</bdi>
                  </td>
                  <td className="p-2 text-fg-muted">
                    <bdi className="block max-w-[10rem] truncate" title={t.source ?? undefined}>
                      {tenderSourceLabel(t.source)}
                    </bdi>
                  </td>
                  <td className="p-2">
                    <div className="flex flex-col gap-0.5">
                      <RelevanceDots value={t.relevance} />
                      {/* W2b: the 0-1 self-tuning relevance_score, visible alongside the older
                          1-10 relevance dots so an operator can see exactly what the learned
                          threshold is comparing against. */}
                      <span
                        className="font-mono text-xs text-fg-dim"
                        aria-label={relevanceScoreAriaLabel(relevanceScorePercent(t.relevance_score))}
                      >
                        {relevanceScorePercent(t.relevance_score)}
                      </span>
                    </div>
                  </td>
                  <td className="p-2">
                    <div className="flex flex-wrap gap-1">
                      {t.matched_terms.slice(0, 3).map((m) => (
                        <span
                          key={m}
                          className="rounded-full bg-bg-sunken px-1.5 py-0.5 text-xs text-fg-dim"
                        >
                          {m}
                        </span>
                      ))}
                      {t.matched_terms.length > 3 && (
                        <span className="text-xs text-fg-dim">+{t.matched_terms.length - 3}</span>
                      )}
                    </div>
                  </td>
                  <td className="p-2">
                    <span
                      className={cn(
                        "rounded-md px-1.5 py-0.5 text-xs font-medium",
                        TENDER_STATUS_CHIP_CLASS[t.status],
                      )}
                    >
                      {TENDER_STATUS_LABEL[t.status]}
                    </span>
                  </td>
                  <td className="p-2">
                    <FeedbackButtons onVote={(verdict) => onFeedback(t.id, verdict)} />
                  </td>
                </tr>
                {expanded && <TenderDetailRow t={t} onFeedback={onFeedback} />}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
