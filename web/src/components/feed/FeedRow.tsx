import type { ItemCard } from "@/types/api";
import { LevelBadge } from "@/components/LevelBadge";
import { SecurityStatusIcon } from "./SecurityStatusIcon";
import { domainLabel } from "@/lib/taxonomy";
import { timeAgo } from "@/lib/time";
import { cn } from "@/lib/cn";

export function FeedRow({
  item,
  selected,
  onSelect,
  onOpen,
  style,
}: {
  item: ItemCard;
  selected: boolean;
  onSelect: () => void;
  onOpen: () => void;
  style?: React.CSSProperties;
}) {
  return (
    <div
      role="row"
      data-testid={`feed-row-${item.id}`}
      data-selected={selected}
      tabIndex={-1}
      aria-selected={selected}
      onClick={onSelect}
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
      <div className="min-w-0 flex-1">
        <bdi className="block truncate font-medium text-fg">{item.title}</bdi>
        <div className="flex items-center gap-2 text-xs text-fg-dim">
          <bdi className="truncate">{item.source_name}</bdi>
          <span>·</span>
          <span className="font-mono">{timeAgo(item.published_at)}</span>
        </div>
      </div>
      <span className="hidden shrink-0 rounded-full bg-bg-sunken px-2 py-0.5 text-xs text-fg-muted md:inline">
        {domainLabel(item.domain)}
      </span>
      <SecurityStatusIcon status={item.security_status} />
    </div>
  );
}
