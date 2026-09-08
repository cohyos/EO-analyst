import { ChevronDown, ExternalLink, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import type { RecentErrorLogEntry } from "@/types/api";
import { formatDateTime } from "@/lib/time";
import { stageLabelHe } from "@/lib/pipelineTimeline";
import { useI18n } from "@/i18n";

/**
 * UI-ERRORS (docs/qa/content_review/UI-ERRORS.md): replaces the old ErrorsDrawer's bare
 * stage/time/message table -- a dead end per the user's report ("an error, and then what do I do
 * with it? what is the error? what caused it? why is it reported?"). Each `recent_errors` entry
 * (agent/eoa/api/services.py) now carries a Hebrew cause/action/impact classification computed
 * server-side, plus a technical-details expander (exception type + traceback tail) and a deep
 * link, so the reader always has a next step.
 */
export function RunErrorsPanel({
  errors,
  onClose,
}: {
  errors: RecentErrorLogEntry[];
  onClose: () => void;
}) {
  const { t } = useI18n();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      // Focus trap: Tab/Shift+Tab cycles within the dialog instead of escaping into the page
      // behind it.
      if (e.key !== "Tab" || !dialogRef.current) return;
      const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
        'button, a[href], [tabindex]:not([tabindex="-1"])',
      );
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    }
    window.addEventListener("keydown", onKeyDown);
    dialogRef.current?.focus();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-40 flex items-start justify-center bg-black/30 p-4 pt-20"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={t("morning.errorsPanelTitle")}
        onClick={(e) => e.stopPropagation()}
        className="flex max-h-[80vh] w-full max-w-xl flex-col rounded-lg border border-border-strong bg-bg-raised shadow-panel outline-none"
      >
        <div className="flex items-center justify-between border-b border-border p-4 pb-3">
          <h2 className="text-sm font-semibold text-fg">{t("morning.errorsPanelTitle")}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            className="rounded p-1 text-fg-dim hover:bg-bg-sunken"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="overflow-y-auto p-4 pt-3">
          {errors.length === 0 ? (
            <p className="text-sm text-ok">{t("morning.errorsPanelEmpty")}</p>
          ) : (
            <ul className="space-y-3">
              {errors.map((e) => (
                <ErrorCard key={e.id} error={e} onNavigate={onClose} />
              ))}
            </ul>
          )}
        </div>

        <Link
          to="/morning#pipeline-replay"
          onClick={onClose}
          className="border-t border-border p-3 text-center text-xs font-medium text-accent hover:underline"
        >
          {t("morning.errorsPanelViewLog")}
        </Link>
      </div>
    </div>
  );
}

function ErrorCard({ error, onNavigate }: { error: RecentErrorLogEntry; onNavigate: () => void }) {
  const { t } = useI18n();
  const hasTechnicalDetails = Boolean(error.error_type) || error.traceback_tail.length > 0;

  return (
    <li className="rounded-lg border border-border bg-bg-sunken p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 text-xs text-fg-dim">
        <span className="font-medium text-fg">
          {t("morning.errorsPanelStage")}: {error.stage ? stageLabelHe(error.stage) : "—"}
        </span>
        <span className="whitespace-nowrap font-mono text-fg-muted">
          {error.at ? formatDateTime(error.at) : "—"}
        </span>
      </div>

      <bdi className="mb-2 block text-sm text-fg" dir="auto">
        {error.message}
      </bdi>

      {/* Older backends (pre-UI-ERRORS, before the API process restarts) return `recent_errors`
          entries without cause_he/action_he/impact_he -- degrade to just the message above
          rather than a row of empty labels. */}
      {(error.cause_he || error.action_he || error.impact_he) && (
        <dl className="grid grid-cols-1 gap-y-1 text-xs">
          {error.cause_he && (
            <div className="flex gap-1">
              <dt className="shrink-0 text-fg-dim">{t("morning.errorsPanelCause")}:</dt>
              <dd className="text-fg">{error.cause_he}</dd>
            </div>
          )}
          {error.action_he && (
            <div className="flex gap-1">
              <dt className="shrink-0 text-fg-dim">{t("morning.errorsPanelAction")}:</dt>
              <dd className="text-fg">{error.action_he}</dd>
            </div>
          )}
          {error.impact_he && (
            <div className="flex gap-1">
              <dt className="shrink-0 text-fg-dim">{t("morning.errorsPanelImpact")}:</dt>
              <dd className="text-fg">{error.impact_he}</dd>
            </div>
          )}
        </dl>
      )}

      {hasTechnicalDetails && (
        <details className="mt-2 text-xs">
          <summary className="flex cursor-pointer select-none items-center gap-1 text-fg-dim hover:text-fg">
            <ChevronDown size={12} aria-hidden="true" />
            {t("morning.errorsPanelTechnicalDetails")}
          </summary>
          <div className="mt-1.5 space-y-1 rounded border border-border bg-bg-raised p-2 font-mono">
            {error.error_type && (
              <p>
                {t("morning.errorsPanelErrorType")}: {error.error_type}
              </p>
            )}
            {error.traceback_tail.length > 0 && (
              <div>
                <p className="text-fg-dim">{t("morning.errorsPanelTraceback")}:</p>
                <ol className="list-inside list-decimal space-y-0.5">
                  {error.traceback_tail.map((frame, i) => (
                    <li key={i} className="break-all">
                      {frame}
                    </li>
                  ))}
                </ol>
              </div>
            )}
          </div>
        </details>
      )}

      {error.link && (
        <Link
          to={error.link}
          onClick={onNavigate}
          className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-accent hover:underline"
        >
          <ExternalLink size={12} aria-hidden="true" />
          {t("morning.errorsPanelOpenLink")}
        </Link>
      )}
    </li>
  );
}
