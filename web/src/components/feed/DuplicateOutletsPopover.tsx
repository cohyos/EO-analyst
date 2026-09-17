import { useEffect, useRef, useState } from "react";
import { ExternalLink, Layers } from "lucide-react";
import type { StoryMember } from "@/types/api";
import { timeAgo } from "@/lib/time";
import { cn } from "@/lib/cn";

const POPOVER_WIDTH = 288; // px, matches w-72

/**
 * W9 (docs/REVIEW_2026-09-06_evening.md round 4, feed side): "+N מקורות" -- a click-to-open
 * popover (fixed-positioned so it escapes the virtualized feed row's clipped box, same technique
 * as `ExplainScorePopover`) listing the other outlets `FeedPage`'s `groupDuplicateItems` folded
 * into this row's primary card, each with its own "פתח מקור" outbound link.
 *
 * 2026-09-17 (story-clustering task): takes `StoryMember[]` (a full `ItemCard` still satisfies
 * this shape) instead of `ItemCard[]` -- `groupDuplicateItems` can now also surface members the
 * backend's own story lookup found beyond this page (`GET /api/items`'s `group_stories` mode),
 * which arrive as the lighter `StoryMember` shape, not a full item card.
 */
export function DuplicateOutletsPopover({
  duplicates,
  size = "md",
}: {
  duplicates: StoryMember[];
  size?: "sm" | "md";
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocPointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (popRef.current?.contains(target) || btnRef.current?.contains(target)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (duplicates.length === 0) return null;

  function toggle(e: React.MouseEvent) {
    e.stopPropagation();
    if (!open) {
      const rect = btnRef.current?.getBoundingClientRect();
      if (rect) {
        let left = rect.left;
        if (left + POPOVER_WIDTH > window.innerWidth - 8) {
          left = Math.max(8, window.innerWidth - POPOVER_WIDTH - 8);
        }
        setPos({ top: rect.bottom + 4, left });
      }
    }
    setOpen((v) => !v);
  }

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={toggle}
        onDoubleClick={(e) => e.stopPropagation()}
        aria-expanded={open}
        aria-label={`${duplicates.length} מקורות נוספים לאותה ידיעה`}
        title="אותה ידיעה מכמה פרסומים"
        data-testid="duplicate-outlets-toggle"
        className={cn(
          "flex shrink-0 items-center gap-1 rounded-full border border-border-strong px-1.5 text-fg-dim hover:border-accent hover:text-accent",
          // Mobile fix (UI-MOBILE-iphone.md #9): 10px text is below the readable floor.
          size === "sm" ? "h-5 text-xs" : "h-6 text-xs",
        )}
      >
        <Layers size={size === "sm" ? 10 : 12} aria-hidden="true" />+{duplicates.length}{" "}
        מקורות
      </button>
      {open && pos && (
        <div
          ref={popRef}
          role="dialog"
          aria-label="מקורות נוספים לאותה ידיעה"
          style={{
            position: "fixed",
            top: pos.top,
            left: pos.left,
            width: POPOVER_WIDTH,
          }}
          className="z-50 max-h-72 overflow-y-auto rounded-lg border border-border-strong bg-bg-raised p-2 text-xs shadow-panel"
          onClick={(e) => e.stopPropagation()}
        >
          <p className="mb-1.5 px-1 font-semibold text-fg-dim">
            אותה ידיעה, {duplicates.length + 1} מקורות
          </p>
          <ul className="space-y-1">
            {duplicates.map((d) => (
              <li key={d.id} className="rounded-md p-1.5 hover:bg-bg-sunken">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <bdi className="block truncate font-medium text-fg" title={d.title || "(ללא כותרת)"}>
                      {d.title || "(ללא כותרת)"}
                    </bdi>
                    <div className="flex items-center gap-1.5 text-fg-dim">
                      <bdi className="truncate" title={d.source_name || undefined}>
                        {d.source_name || "—"}
                      </bdi>
                      <span>·</span>
                      <span className="font-mono">{timeAgo(d.published_at)}</span>
                    </div>
                  </div>
                  {d.url && (
                    <a
                      href={d.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label="פתח מקור"
                      title="פתח מקור"
                      className="flex shrink-0 items-center gap-1 rounded border border-border-strong px-1.5 py-0.5 text-fg-dim hover:border-accent hover:text-accent"
                    >
                      <ExternalLink size={11} aria-hidden="true" />
                      פתח מקור
                    </a>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
