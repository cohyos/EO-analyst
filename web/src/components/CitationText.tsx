import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { cn } from "@/lib/cn";
import { renderBidiRuns } from "@/lib/bidiText";

/** Structural shape shared by AskCitation and InvestigationSource. */
export interface CitationLike {
  n: number;
  item_id: number | null;
  title: string;
  url: string;
}

/**
 * Renders assistant text containing `[n]` markers, turning each into a hover chip that shows the
 * cited source and, on click, navigates to it (U3, docs/REVIEW_2026-09-05.md): `/items/:id` when
 * the citation resolves to a real item, otherwise the source URL in a new tab. Previously this
 * only showed a tooltip -- `onOpenItem` (when a caller still passes one, e.g. to open an inline
 * panel instead of a full navigation) took over for the item_id case, but nothing handled a
 * citation with no item_id at all, and callers that passed no `onOpenItem` (silently swallowing
 * every click) were exactly the "click does nothing" bug this fixes.
 */
export function CitationText({
  text,
  citations,
  onOpenItem,
}: {
  text: string;
  citations: CitationLike[];
  onOpenItem?: (itemId: number) => void;
}) {
  const byN = new Map((citations ?? []).map((c) => [c.n, c]));
  const parts = (text ?? "").split(/(\[\d+\])/g);

  return (
    <span>
      {parts.map((part, i) => {
        const match = part.match(/^\[(\d+)\]$/);
        // CR-invest.md: a non-citation segment still embeds unquoted Latin/number runs (company
        // names, model numbers, units) glued against Hebrew with no bidi markup of its own --
        // isolate them the same way `AnswerText` does for headings/paragraphs/bullets, so a plain
        // sentence rendered through `CitationText` alone (this component's only real contract) is
        // just as bidi-safe as one that went through the fuller section parser.
        if (!match) return <span key={i}>{renderBidiRuns(part)}</span>;
        const n = Number(match[1]);
        const citation = byN.get(n);
        if (!citation) return <span key={i}>{renderBidiRuns(part)}</span>;
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
  citation: CitationLike;
  onOpenItem?: (itemId: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  function handleClick() {
    if (citation.item_id != null) {
      if (onOpenItem) onOpenItem(citation.item_id);
      else navigate(`/items/${citation.item_id}`);
    } else if (citation.url) {
      window.open(citation.url, "_blank", "noopener,noreferrer");
    }
  }

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
        onClick={handleClick}
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
