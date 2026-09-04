import { useEffect, useRef, useState } from "react";
import { HelpCircle } from "lucide-react";
import type { ItemCard, TriageLevel } from "@/types/api";
import { LEVEL_THRESHOLDS } from "@/lib/taxonomy";
import { cn } from "@/lib/cn";

const RATE_KEYS: Array<{ key: string; level: TriageLevel; label: string }> = [
  { key: "1", level: "red", label: "קריטי" },
  { key: "2", level: "orange", label: "חשוב" },
  { key: "3", level: "yellow", label: "רקע" },
  { key: "4", level: "archive", label: "ארכיון" },
];

const POPOVER_WIDTH = 288; // px, matches w-72

/**
 * "למה הציון?" — a click-to-open popover (fixed-positioned so it escapes
 * the virtualized feed row's clipped, absolutely-positioned box) showing
 * the triage reason, the config-driven level thresholds, and one-click
 * re-rate buttons. Shared between FeedRow (compact) and FeedDetailPanel.
 */
export function ExplainScorePopover({
  item,
  onRate,
  isRating,
  size = "md",
}: {
  item: ItemCard;
  onRate: (level: TriageLevel) => void;
  isRating?: boolean;
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
        aria-label="למה הציון?"
        title="למה הציון?"
        className={cn(
          "flex shrink-0 items-center justify-center rounded-md text-fg-dim hover:bg-bg-sunken hover:text-fg",
          size === "sm" ? "h-5 w-5" : "h-7 w-7",
        )}
      >
        <HelpCircle size={size === "sm" ? 13 : 15} aria-hidden="true" />
      </button>
      {open && pos && (
        <div
          ref={popRef}
          role="dialog"
          aria-label="הסבר ציון"
          style={{ position: "fixed", top: pos.top, left: pos.left, width: POPOVER_WIDTH }}
          className="z-50 rounded-lg border border-border-strong bg-bg-raised p-3 text-xs shadow-panel"
          onClick={(e) => e.stopPropagation()}
        >
          <p className="mb-2 font-semibold text-fg">למה הציון? (ציון: {item.score})</p>
          <p className="mb-3 leading-relaxed text-fg-muted" dir="auto">
            {item.triage_reason ?? "אין נימוק זמין."}
          </p>
          <p className="mb-1 font-semibold text-fg-dim">סף רמות</p>
          <ul className="mb-3 space-y-0.5 font-mono text-fg-dim">
            <li>קריטי: ציון ≥ {LEVEL_THRESHOLDS.red}</li>
            <li>חשוב: ציון ≥ {LEVEL_THRESHOLDS.orange}</li>
            <li>רקע: ציון ≥ {LEVEL_THRESHOLDS.yellow}</li>
            <li>ארכיון: מתחת ל-{LEVEL_THRESHOLDS.yellow}</li>
          </ul>
          <p className="mb-1 font-semibold text-fg-dim">דירוג מהיר</p>
          <div className="flex flex-wrap gap-1.5">
            {RATE_KEYS.map((r) => (
              <button
                key={r.level}
                type="button"
                onClick={() => onRate(r.level)}
                disabled={isRating}
                className={cn(
                  "rounded-md border px-2 py-1 hover:bg-bg-sunken disabled:opacity-50",
                  item.level === r.level
                    ? "border-accent text-accent"
                    : "border-border-strong text-fg-dim",
                )}
              >
                {r.key} {r.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
