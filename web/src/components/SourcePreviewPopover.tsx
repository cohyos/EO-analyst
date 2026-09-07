import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { SourcePreviewCard, type SourcePreviewFallback } from "./SourcePreviewCard";
import { useT } from "@/i18n";
import { cn } from "@/lib/cn";

const POPOVER_WIDTH = 288; // px, matches SourcePreviewCard's w-72
const HOVER_OPEN_DELAY_MS = 150;
const HOVER_CLOSE_DELAY_MS = 150;

function isTouchDevice(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  // No fine pointer with hover support -> treat as touch (tablets/phones, and any device the
  // test environment reports as such). Desktop-with-touchscreen still reads as hover-capable here
  // on purpose: hovering still works there, so the richer desktop interaction is the better fit.
  return !window.matchMedia("(hover: hover) and (pointer: fine)").matches;
}

/**
 * Wraps a source link/trigger (report citation marker, appendix row, chat-footer source row,
 * tender/forecast source link, ...) with the shared `SourcePreviewCard` so an analyst can read
 * the Hebrew summary before deciding to actually leave the app -- "read an article's summary
 * before being sent to the article" (2026-09-07).
 *
 * Desktop (hover-capable): hovering or focusing `children` opens a floating card near it after a
 * short delay (so a passing cursor doesn't flicker it open); the trigger's own click behavior
 * (navigate/open) is left completely alone -- the analyst reads the preview on the way to
 * clicking, they don't have to change how they click.
 *
 * Touch/mobile: a tap on `children` is intercepted (capture-phase preventDefault) and opens the
 * card as a bottom sheet instead of immediately navigating away, since there is no hover there to
 * preview it first. The sheet's own "פתח פריט"/"פתח מקור" actions are then the real, deliberate
 * way to leave the app. Escape, the backdrop, and the sheet's own close button all dismiss it and
 * return focus to the trigger.
 */
export function SourcePreviewPopover({
  itemId,
  fallback,
  children,
}: {
  itemId?: number | null;
  fallback?: SourcePreviewFallback;
  children: ReactNode;
}) {
  const t = useT();
  const titleId = useId();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const [sheet, setSheet] = useState(false);
  const wrapperRef = useRef<HTMLSpanElement>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  function clearTimers() {
    if (openTimer.current) clearTimeout(openTimer.current);
    if (closeTimer.current) clearTimeout(closeTimer.current);
    openTimer.current = null;
    closeTimer.current = null;
  }

  function openAt() {
    clearTimers();
    const rect = wrapperRef.current?.getBoundingClientRect();
    if (rect) {
      let left = rect.left;
      if (left + POPOVER_WIDTH > window.innerWidth - 8) {
        left = Math.max(8, window.innerWidth - POPOVER_WIDTH - 8);
      }
      setPos({ top: rect.bottom + 6, left });
    }
    setOpen(true);
  }

  function scheduleOpen() {
    if (isTouchDevice()) return; // touch opens via the click handler below, not hover
    clearTimers();
    openTimer.current = setTimeout(openAt, HOVER_OPEN_DELAY_MS);
  }

  function scheduleClose() {
    clearTimers();
    closeTimer.current = setTimeout(() => setOpen(false), HOVER_CLOSE_DELAY_MS);
  }

  function closeNow() {
    clearTimers();
    setOpen(false);
    setSheet(false);
  }

  function handleTriggerClick(e: React.MouseEvent) {
    if (!isTouchDevice()) return; // desktop: let the trigger's own click behavior run as-is
    if (open) return; // already open (or was just closed by this same tap) -- don't re-intercept
    e.preventDefault();
    e.stopPropagation();
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    setSheet(true);
    setOpen(true);
  }

  useEffect(() => {
    if (!open) return;
    function onDocPointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (popRef.current?.contains(target) || wrapperRef.current?.contains(target)) return;
      closeNow();
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        closeNow();
        restoreFocusRef.current?.focus?.();
      }
    }
    document.addEventListener("mousedown", onDocPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocPointerDown);
      document.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (sheet && open) closeButtonRef.current?.focus();
  }, [sheet, open]);

  useEffect(() => clearTimers, []);

  return (
    <>
      {/* `display: contents` keeps this wrapper out of layout entirely -- no extra box, no shift
          around the trigger it wraps -- while still receiving the bubbled mouse/focus events
          needed to drive the preview. */}
      <span
        ref={wrapperRef}
        className="contents"
        onMouseEnter={scheduleOpen}
        onMouseLeave={scheduleClose}
        onFocus={scheduleOpen}
        onBlur={scheduleClose}
        onClickCapture={handleTriggerClick}
      >
        {children}
      </span>

      {open && !sheet && pos && (
        <div
          ref={popRef}
          role="tooltip"
          id={titleId}
          style={{ position: "fixed", top: pos.top, left: pos.left, width: POPOVER_WIDTH }}
          className="z-50 rounded-lg border border-border-strong bg-bg-raised p-2.5 shadow-panel"
          onMouseEnter={clearTimers}
          onMouseLeave={scheduleClose}
        >
          <SourcePreviewCard itemId={itemId} fallback={fallback} />
        </div>
      )}

      {open && sheet && (
        <div className="fixed inset-0 z-50 flex flex-col justify-end">
          <div
            className="absolute inset-0 bg-black/40"
            aria-hidden="true"
            onClick={closeNow}
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t("sourcePreview.previewAria")}
            className={cn(
              "relative max-h-[80vh] overflow-y-auto rounded-t-xl border-t border-border-strong",
              "bg-bg-raised p-4 pt-3 shadow-panel",
            )}
          >
            <div className="mb-2 flex items-center justify-end">
              <button
                ref={closeButtonRef}
                type="button"
                onClick={closeNow}
                aria-label={t("sourcePreview.close")}
                className="rounded-md p-1 text-fg-dim hover:bg-bg-sunken hover:text-fg"
              >
                <X size={16} aria-hidden="true" />
              </button>
            </div>
            <SourcePreviewCard itemId={itemId} fallback={fallback} className="w-full max-w-none" />
          </div>
        </div>
      )}
    </>
  );
}
