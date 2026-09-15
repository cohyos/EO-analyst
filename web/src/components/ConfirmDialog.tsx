import { useEffect, useRef } from "react";
import { cn } from "@/lib/cn";

/**
 * Round-3 mobile fix (UI-MOBILE-iphone-r3.md #3): a small reusable confirmation dialog for
 * expensive/slow actions (deep-search investigations, dossier reruns) that previously fired
 * straight from a single tap with no way to back out. Same backdrop/role="dialog"/Escape-to-close
 * shape as `NewInvestigationDialog`, kept intentionally generic (title/message/labels are all
 * caller-supplied) so it can front any confirm-before-mutate button.
 */
export function ConfirmDialog({
  title,
  message,
  confirmLabel = "אישור",
  cancelLabel = "ביטול",
  confirmingLabel = "מבצע…",
  confirming = false,
  danger = false,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  confirmingLabel?: string;
  confirming?: boolean;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKeyDown);
    dialogRef.current?.focus();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onCancel]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onCancel}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-sm rounded-lg border border-border-strong bg-bg-raised p-4 shadow-panel outline-none"
      >
        <h2 className="mb-2 text-sm font-semibold text-fg">{title}</h2>
        <p className="mb-4 text-sm text-fg-muted">{message}</p>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="inline-flex min-h-10 items-center rounded-md border border-border px-3 py-1.5 text-sm text-fg-muted hover:bg-bg-sunken"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={confirming}
            className={cn(
              "inline-flex min-h-10 items-center rounded-md px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50",
              danger ? "bg-danger" : "bg-accent",
            )}
          >
            {confirming ? confirmingLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
