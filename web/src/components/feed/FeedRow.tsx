import { ExternalLink } from "lucide-react";
import type { ItemCard, TriageLevel } from "@/types/api";
import { LevelBadge } from "@/components/LevelBadge";
import { SecurityStatusIcon } from "./SecurityStatusIcon";
import { ExplainScorePopover } from "./ExplainScorePopover";
import { DuplicateOutletsPopover } from "./DuplicateOutletsPopover";
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
  duplicates?: ItemCard[];
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
        "absolute inset-x-0 flex h-16 items-center gap-3 border-b border-border px-3 text-sm",
        "cursor-pointer",
        selected ? "bg-accent-muted/70" : "hover:bg-bg-sunken",
      )}
    >
      <LevelBadge level={item.level} size="sm" />
      <span className="w-10 shrink-0 text-end font-mono font-tabular text-fg-muted">
        {item.score}
      </span>
      <ExplainScorePopover item={item} onRate={onRate} isRating={isRating} size="sm" />
      <div className="min-w-0 flex-1">
        <a
          href={hasUrl ? item.url : undefined}
          target={hasUrl ? "_blank" : undefined}
          rel={hasUrl ? "noopener noreferrer" : undefined}
          onClick={(e) => {
            if (!hasUrl) e.preventDefault();
          }}
          data-testid={`feed-row-title-link-${item.id}`}
          title={hasUrl ? "פתח מקור בכרטיסייה חדשה" : undefined}
          className={cn(
            "block truncate font-medium",
            item.title ? "text-fg" : "italic text-fg-dim",
            hasUrl && "hover:text-accent hover:underline",
          )}
        >
          <bdi>{displayTitle}</bdi>
        </a>
        <div className="flex items-center gap-2 text-xs text-fg-dim">
          <bdi className="truncate">{item.source_name}</bdi>
          <span>·</span>
          <span className="font-mono">{timeAgo(item.published_at)}</span>
        </div>
      </div>
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
  );
}
