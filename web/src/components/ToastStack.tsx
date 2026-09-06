import { Link } from "react-router-dom";
import { X } from "lucide-react";
import type { ToastMessage } from "@/hooks/useToastQueue";
import { cn } from "@/lib/cn";

const TONE_CLASS: Record<ToastMessage["tone"], string> = {
  ok: "border-ok/40 bg-ok/10",
  warn: "border-warn/40 bg-warn/10",
  danger: "border-danger/40 bg-danger/10",
  info: "border-border-strong bg-bg-raised",
};

/**
 * Renders the toasts produced by `useToastQueue` (Q5-3/Q5-6, docs/qa/findings_Q5_r1.md) as a
 * bottom-start stack, each auto-dismissing and individually closeable.
 */
export function ToastStack({
  toasts,
  onDismiss,
}: {
  toasts: ToastMessage[];
  onDismiss: (id: number) => void;
}) {
  if (toasts.length === 0) return null;
  return (
    <div className="fixed bottom-[calc(1rem+env(safe-area-inset-bottom))] start-4 z-50 flex flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role="status"
          aria-live="polite"
          className={cn(
            "flex items-center gap-3 rounded-lg border bg-bg-raised px-4 py-2.5 text-sm shadow-panel",
            TONE_CLASS[toast.tone],
          )}
        >
          <span>{toast.text}</span>
          {toast.linkTo && (
            <Link
              to={toast.linkTo}
              onClick={() => onDismiss(toast.id)}
              className="font-medium text-accent hover:underline"
            >
              {toast.linkLabel ?? "פתח"}
            </Link>
          )}
          <button
            type="button"
            onClick={() => onDismiss(toast.id)}
            aria-label="סגור התראה"
            className="tap-target inline-flex items-center justify-center rounded p-0.5 text-fg-dim hover:bg-bg-sunken"
          >
            <X size={14} aria-hidden="true" />
          </button>
        </div>
      ))}
    </div>
  );
}
