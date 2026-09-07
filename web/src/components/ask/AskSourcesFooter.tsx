import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, ChevronUp } from "lucide-react";
import type { AskCitation } from "@/types/api";
import { LevelBadge } from "@/components/LevelBadge";
import { CorroborationBadge } from "@/components/feed/CorroborationBadge";
import { SourcePreviewPopover } from "@/components/SourcePreviewPopover";

/**
 * The compact "מקורות (n)" footer (U11, ask-answer-format rewrite): one row per source with
 * title, source/domain name and a level badge, each expandable to show the model's optional
 * one-line relevance note (`source_notes` in the prompt contract) -- the per-source relevance
 * the previous UI dumped straight into the answer body ("הערת איכות", "ציטוט מדויק" blocks) now
 * lives only here, never inline.
 *
 * R10-preview (2026-09-07): the title button is wrapped in `SourcePreviewPopover` -- desktop
 * hover/focus shows the summary before the button's own click still opens it (internal item page
 * or, if none, the external source); on touch, the first tap opens the preview as a bottom sheet
 * instead, so a summary is always read before actually leaving the app.
 */
export function AskSourcesFooter({ sources }: { sources: AskCitation[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const navigate = useNavigate();

  if (sources.length === 0) return null;

  function toggle(n: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  }

  function openSource(c: AskCitation) {
    if (c.item_id != null) navigate(`/items/${c.item_id}`);
    else if (c.url) window.open(c.url, "_blank", "noopener,noreferrer");
  }

  return (
    <div className="border-t border-border p-3">
      <h3 className="mb-2 text-xs font-medium text-fg-dim">מקורות ({sources.length})</h3>
      <ul className="space-y-1">
        {sources.map((c) => {
          const isOpen = expanded.has(c.n);
          return (
            <li key={`${c.n}-${c.item_id}`} className="rounded-md border border-border bg-bg-sunken/40">
              <div className="flex items-center gap-1.5 px-2 py-1.5">
                <span className="shrink-0 font-mono text-[10px] text-fg-dim">[{c.n}]</span>
                {c.level && <LevelBadge level={c.level} size="sm" />}
                <SourcePreviewPopover
                  itemId={c.item_id}
                  fallback={{ title: c.title, sourceName: c.source_name, url: c.url }}
                >
                  <button
                    type="button"
                    onClick={() => openSource(c)}
                    className="min-w-0 flex-1 text-start text-xs text-fg hover:underline"
                  >
                    <bdi className="block truncate" title={c.title || "(ללא כותרת)"}>
                      {c.title || "(ללא כותרת)"}
                    </bdi>
                  </button>
                </SourcePreviewPopover>
                {c.source_name && (
                  <bdi className="max-w-[8rem] shrink-0 truncate text-[10px] text-fg-dim" title={c.source_name}>
                    {c.source_name}
                  </bdi>
                )}
                {c.note && (
                  <button
                    type="button"
                    aria-label={isOpen ? "הסתר הערת רלוונטיות" : "הצג הערת רלוונטיות"}
                    aria-expanded={isOpen}
                    onClick={() => toggle(c.n)}
                    className="shrink-0 text-fg-dim hover:text-fg"
                  >
                    {isOpen ? (
                      <ChevronUp size={14} aria-hidden="true" />
                    ) : (
                      <ChevronDown size={14} aria-hidden="true" />
                    )}
                  </button>
                )}
              </div>
              {/* CORR (2026-09-07): only rendered once the backend enriches this citation with a
                  corroboration object -- absent on every citation until then, per the frozen API
                  contract (the field isn't part of `AskCitation` before this feature). */}
              {c.corroboration && (
                <div className="flex items-center gap-1.5 px-2 pb-1.5">
                  <CorroborationBadge corroboration={c.corroboration} size="sm" showUnknown />
                </div>
              )}
              {isOpen && c.note && (
                <p dir="auto" className="border-t border-border px-2 py-1.5 text-xs text-fg-muted">
                  {c.note}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
