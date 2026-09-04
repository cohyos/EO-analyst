import { AlertCircle, Inbox, Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export function LoadingState({ label = "טוען…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 p-8 text-fg-muted" role="status">
      <Loader2 className="animate-spin" size={18} aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({
  message = "אירעה שגיאה בטעינת הנתונים",
  onRetry,
}: {
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div
      className="flex flex-col items-center justify-center gap-3 p-8 text-center text-danger"
      role="alert"
    >
      <AlertCircle size={22} aria-hidden="true" />
      <p>{message}</p>
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
}: {
  title?: string;
  description?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-10 text-center text-fg-muted">
      {icon ?? <Inbox size={26} aria-hidden="true" />}
      <p className="font-medium text-fg">{title}</p>
      {description && <p className="max-w-sm text-sm">{description}</p>}
    </div>
  );
}
