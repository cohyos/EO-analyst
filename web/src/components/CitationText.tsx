import { useState } from "react";
import type { AskCitation } from "@/types/api";
import { cn } from "@/lib/cn";

/**
 * Renders assistant text containing `[n]` markers, turning each into a
 * hover chip that shows the cited source and links to the item.
 */
export function CitationText({
  text,
  citations,
  onOpenItem,
}: {
  text: string;
  citations: AskCitation[];
  onOpenItem?: (itemId: number) => void;
}) {
  const byN = new Map(citations.map((c) => [c.n, c]));
  const parts = text.split(/(\[\d+\])/g);

  return (
    <span>
      {parts.map((part, i) => {
        const match = part.match(/^\[(\d+)\]$/);
        if (!match) return <span key={i}>{part}</span>;
        const n = Number(match[1]);
        const citation = byN.get(n);
        if (!citation) return <span key={i}>{part}</span>;
        return (
          <CitationChip key={i} n={n} citation={citation} onOpenItem={onOpenItem} />
        );
      })}
    </span>
  );
}

function CitationChip({
  n,
  citation,
  onOpenItem,
}: {
  n: number;
  citation: AskCitation;
  onOpenItem?: (itemId: number) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-block">
      <button
        type="button"
        className={cn(
          "mx-0.5 inline-flex h-4 min-w-[1rem] items-center justify-center rounded",
          "bg-accent-muted px-1 align-super text-[10px] font-mono font-semibold text-accent-fg",
          "hover:bg-accent hover:text-accent-fg",
        )}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => onOpenItem?.(citation.item_id)}
        aria-describedby={`citation-${n}-tip`}
      >
        {n}
      </button>
      {open && (
        <span
          id={`citation-${n}-tip`}
          role="tooltip"
          className="absolute bottom-full z-20 mb-1 w-64 -translate-x-1/2 rounded-md border border-border-strong bg-bg-raised p-2 text-xs shadow-panel"
          style={{ insetInlineStart: "50%" }}
        >
          <bdi className="block truncate font-medium text-fg">{citation.title}</bdi>
          <bdi className="block truncate text-fg-dim" dir="ltr">
            {citation.url}
          </bdi>
        </span>
      )}
    </span>
  );
}
