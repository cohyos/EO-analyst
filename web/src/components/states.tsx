import { AlertCircle, Inbox, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export function LoadingState({ label = "טוען…" }: { label?: string }) {
  return (
    <div
      className="flex items-center justify-center gap-2 p-8 text-fg-muted"
      role="status"
    >
      <Loader2 className="animate-spin" size={18} aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

/** True for the `ApiError("timeout", ...)` `real.ts`'s `request()` throws when a call is aborted
 * after its 10s (default) deadline (W13, docs/REVIEW_2026-09-06_evening.md round 4) -- duck-typed
 * on `code`/`message` rather than importing `ApiError`/`instanceof` so this file (used by every
 * page) never has to depend on `@/api`. */
function isTimeoutError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { code?: unknown }).code === "timeout"
  );
}

export function ErrorState({
  message,
  onRetry,
  error,
}: {
  message?: string;
  onRetry?: () => void;
  /** W13: the query's own `error` (e.g. TanStack Query's `query.error`) -- when it's the API
   * client's timeout error, overrides `message` with the dedicated Hebrew timeout copy so a hung
   * request reads as "the server didn't respond, try again" instead of a generic failure. */
  error?: unknown;
}) {
  const resolvedMessage =
    message ??
    (isTimeoutError(error) ? "השרת לא הגיב, נסה שוב" : "אירעה שגיאה בטעינת הנתונים");
  return (
    <div
      className="flex flex-col items-center justify-center gap-3 p-8 text-center text-danger"
      role="alert"
    >
      <AlertCircle size={22} aria-hidden="true" />
      <p>{resolvedMessage}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="rounded-md border border-border-strong px-3 py-1.5 text-sm text-fg hover:bg-bg-raised"
        >
          נסה שוב
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title = "אין נתונים להצגה",
  description,
  icon,
  action,
}: {
  title?: string;
  description?: string;
  icon?: ReactNode;
  /** Q5-11 (docs/qa/findings_Q5_r2.md): an optional inline action (e.g. a button) rendered below
   * the description -- for an empty state that a control elsewhere on the page can actually fix
   * (e.g. "X מכרזים סגורים מוסתרים — הצג"), so the fix sits right where the dead end was. */
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-10 text-center text-fg-muted">
      {icon ?? <Inbox size={26} aria-hidden="true" />}
      <p className="font-medium text-fg">{title}</p>
      {description && <p className="max-w-sm text-sm">{description}</p>}
      {action}
    </div>
  );
}
