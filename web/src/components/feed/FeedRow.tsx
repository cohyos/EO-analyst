import { ExternalLink } from "lucide-react";
import type { ItemCard, StoryMember, TriageLevel } from "@/types/api";
import { LevelBadge } from "@/components/LevelBadge";
import { SecurityStatusIcon } from "./SecurityStatusIcon";
import { ExplainScorePopover } from "./ExplainScorePopover";
import { DuplicateOutletsPopover } from "./DuplicateOutletsPopover";
import { CorroborationBadge } from "./CorroborationBadge";
import { domainLabel } from "@/lib/taxonomy";
import { timeAgo } from "@/lib/time";
import { cn } from "@/lib/cn";
import { useT } from "@/i18n";

export function FeedRow({
  item,
  selected,
  onSelect,
  onOpen,
  onRate,
  isRating,
  investigating,
  duplicates,
  style,
}: {
  item: ItemCard;
  selected: boolean;
  onSelect: () => void;
  onOpen: () => void;
  onRate: (level: TriageLevel) => void;
  isRating?: boolean;
  /** Q5-3 (docs/qa/findings_Q5_r1.md): a deep_search job is queued/running for this item. */
  investigating?: boolean;
  /** W9 (docs/REVIEW_2026-09-06_evening.md round 4): other outlets covering the same story,
   * folded into this row by `lib/dedupGroups.ts` -- renders as a "+N מקורות" chip. */
  duplicates?: StoryMember[];
  style?: React.CSSProperties;
}) {
  const t = useT();
  const hasUrl = Boolean(item.url);
  // Real data (2026-09-04 QA against the live backend): a handful of ingested
  // items carry an empty `title` (a fetch/parse gap upstream, out of scope
  // here) — render a visible placeholder instead of a blank, mysterious
  // clickable strip.
  const displayTitle = item.title || "(ללא כותרת)";
  return (
    <div
      role="listitem"
      data-testid={`feed-row-${item.id}`}
      data-selected={selected}
      tabIndex={-1}
      aria-current={selected ? "true" : undefined}
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData(
          "application/x-eo-context",
          JSON.stringify({ kind: "item", id: item.id, label: displayTitle }),
        );
        e.dataTransfer.effectAllowed = "copy";
      }}
      onClick={() => {
        // W8 (docs/REVIEW_2026-09-06_evening.md round 4): a single click on a row now opens the
        // inline detail drawer directly (previously only double-click/Space did -- a single click
        // just selected the row, which read as "clicking a row does nothing"). Keeps updating
        // selection too, so J/K keyboard nav continues from wherever the mouse last clicked.
        onSelect();
        onOpen();
      }}
      onDoubleClick={onOpen}
      style={style}
      className={cn(
        // Mobile fix (UI-MOBILE-iphone.md #1): below `sm`, a fixed `h-16` row packed the headline
        // in beside four always-visible `shrink-0` badges left it with almost no width -- headlines
        // rendered as a single clipped character. `<sm:` now wraps into two rows (headline full
        // width on row 1, the badge/meta cluster wrapping on row 2 -- see the title `order-first
        // w-full` below, which forces the flex-wrap break); `sm:` and up keep the original
        // single-row layout unchanged. The row height itself must stay a fixed, JS-known constant
        // (not organic) because the feed list is a simple fixed-row-height virtualizer
        // (`useVirtualList`) -- callers pass a taller `ROW_HEIGHT` on phones via
        // `useIsNarrowViewport`, and `overflow-hidden` here is the safety net if content still
        // slightly overflows that slot at extreme widths (~280px, several wrapped badge chips).
        "absolute inset-x-0 flex h-28 flex-wrap items-center gap-x-3 gap-y-1.5 overflow-hidden border-b border-border px-3 py-2 text-sm sm:h-16 sm:flex-nowrap sm:overflow-visible sm:py-0",
        "cursor-pointer",
        selected ? "bg-accent-muted/70" : "hover:bg-bg-sunken",
      )}
    >
      {/* Row 1 (mobile): the headline. `order-first` + `w-full` forces the flex-wrap break so
          everything else lands on row 2+; `sm:order-2` restores its original desktop position
          (after the level/score cluster below, before the domain/duplicate/security cluster). */}
      <div className="order-first w-full min-w-0 sm:order-2 sm:w-auto sm:flex-1">
        <a
          href={hasUrl ? item.url : undefined}
          target={hasUrl ? "_blank" : undefined}
          rel={hasUrl ? "noopener noreferrer" : undefined}
          onClick={(e) => {
            if (!hasUrl) e.preventDefault();
          }}
          data-testid={`feed-row-title-link-${item.id}`}
          // Content review (docs/qa/content_review/CR-ui.md): this link's own text truncates
          // (`block truncate`) at the row's fixed height, but `title` used to be a fixed action
          // hint ("פתח מקור בכרטיסייה חדשה") instead of the title itself -- hovering a cut-off
          // headline surfaced no way to read the rest of it. Keeps the action hint (still useful,
          // still distinct from the row's own click-to-open behavior) alongside the full title,
          // since an element only gets one `title` attribute.
          title={hasUrl ? `${displayTitle} — פתח מקור בכרטיסייה חדשה` : displayTitle}
          className={cn(
            "block font-medium",
            // Mobile fix (#1): 2-line clamp on phones so the headline -- the point of the row --
            // is actually readable instead of cut to one character; back to the original
            // single-line truncate from `sm:` up.
            "line-clamp-2 sm:line-clamp-none sm:truncate",
            item.title ? "text-fg" : "italic text-fg-dim",
            hasUrl && "hover:text-accent hover:underline",
          )}
        >
          <bdi>{displayTitle}</bdi>
        </a>
        <div className="flex items-center gap-2 text-xs text-fg-dim">
          <bdi className="truncate" title={item.source_name || undefined}>
            {item.source_name}
          </bdi>
          <span>·</span>
          <span className="font-mono">{timeAgo(item.published_at)}</span>
        </div>
      </div>
      {/* Row 2 (mobile, wraps as needed): level/corroboration/score/explain cluster. A plain
          `shrink-0` group (no responsive classes needed on the group itself) that naturally lands
          on row 2 since the title above it forces the wrap point; `sm:order-1` keeps it first on
          the desktop single-row layout, exactly where it was before this wrapper existed. */}
      <div className="flex shrink-0 items-center gap-3 sm:order-1">
        <LevelBadge level={item.level} size="sm" />
        {/* CORR (cross-source corroboration): "unknown" renders nothing here (showUnknown=false)
            -- no layout shift for the majority of rows that haven't been checked yet. */}
        <CorroborationBadge corroboration={item.corroboration} size="sm" />
        <span className="w-10 shrink-0 text-end font-mono font-tabular text-fg-muted">
          {item.score}
        </span>
        <ExplainScorePopover item={item} onRate={onRate} isRating={isRating} size="sm" />
      </div>
      {/* Row 2 (mobile, continued): domain/duplicate/israel/investigating/link/security cluster.
          `sm:order-3` keeps it after the title on the desktop single-row layout (unchanged from
          before this wrapper existed). */}
      <div className="flex shrink-0 flex-wrap items-center gap-3 sm:order-3">
        <span className="hidden shrink-0 rounded-full bg-bg-sunken px-2 py-0.5 text-xs text-fg-muted md:inline">
          {domainLabel(item.domain)}
        </span>
        {duplicates && duplicates.length > 0 && (
          <DuplicateOutletsPopover duplicates={duplicates} size="sm" />
        )}
        {/* A13 (מיקוד תעשייה ישראלית): small flag badge, mirroring the entity "★ watchlist" badge
            pattern — visible whenever the deterministic scoring pipeline marked this item relevant. */}
        {(item.israel_relevance ?? 0) >= 0.5 && (
          <span
            data-testid={`feed-row-israel-badge-${item.id}`}
            role="img"
            aria-label={t("feed.israelBadgeAria")}
            title={t("feed.israelBadgeAria")}
            className="shrink-0 text-xs"
          >
            🇮🇱
          </span>
        )}
        {investigating && (
          <span
            data-testid={`feed-row-investigating-${item.id}`}
            role="status"
            aria-label={t("feed.investigatingIndicatorAria")}
            title={t("feed.investigatingIndicatorAria")}
            className="hidden shrink-0 rounded-full bg-accent-muted px-2 py-0.5 text-xs text-accent md:inline"
          >
            {t("feed.investigatingIndicator")}
          </span>
        )}
        {hasUrl && (
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            title="פתח מקור ↗ (O)"
            aria-label="פתח מקור במקור המקורי"
            className="hidden shrink-0 items-center gap-1 rounded-md border border-border-strong px-1.5 py-0.5 text-xs text-fg-dim hover:border-accent hover:text-accent md:flex"
          >
            <ExternalLink size={12} aria-hidden="true" />
            פתח מקור
          </a>
        )}
        <SecurityStatusIcon status={item.security_status} />
      </div>
    </div>
  );
}
