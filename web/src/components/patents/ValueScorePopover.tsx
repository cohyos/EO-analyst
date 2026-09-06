import { useEffect, useRef, useState } from "react";
import { HelpCircle } from "lucide-react";

const POPOVER_WIDTH = 288; // px, matches w-72

/**
 * A14: "מהו ציון-הערך?" -- a click-to-open popover explaining the deterministic value-score proxy
 * (agent/eoa/patents/valuation.py) for one patent row, including its own per-factor reasons and
 * the mandatory "not a financial valuation" disclaimer. Mirrors
 * web/src/components/feed/ExplainScorePopover.tsx's fixed-positioned popover shape.
 */
export function ValueScorePopover({ score, reasons }: { score: number | null; reasons: string[] }) {
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
    <span className="inline-flex items-center gap-1">
      <span className="font-mono font-tabular">{score ?? "—"}</span>
      <button
        ref={btnRef}
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-label="מהו ציון-הערך?"
        title="מהו ציון-הערך?"
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-fg-dim hover:bg-bg-sunken hover:text-fg"
      >
        <HelpCircle size={13} aria-hidden="true" />
      </button>
      {open && pos && (
        <div
          ref={popRef}
          role="dialog"
          aria-label="הסבר ציון-ערך"
          style={{ position: "fixed", top: pos.top, left: pos.left, width: POPOVER_WIDTH }}
          className="z-50 rounded-lg border border-border-strong bg-bg-raised p-3 text-xs shadow-panel"
          onClick={(e) => e.stopPropagation()}
        >
          <p className="mb-2 font-semibold text-fg">ציון-ערך: {score ?? "—"} / 100</p>
          {reasons.length ? (
            <ul className="list-inside list-disc space-y-1 leading-relaxed text-fg-muted" dir="auto">
              {reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          ) : (
            <p className="text-fg-muted">אין עדיין פירוט לציון (הפטנט טרם עבר הערכה).</p>
          )}
        </div>
      )}
    </span>
  );
}
