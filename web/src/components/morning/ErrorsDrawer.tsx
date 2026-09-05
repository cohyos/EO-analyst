import { X } from "lucide-react";
import { Link } from "react-router-dom";
import type { RecentErrorLogEntry } from "@/types/api";
import { formatDateTime } from "@/lib/time";
import { useI18n } from "@/i18n";

/**
 * U2: the "שגיאות אחרונות" KPI card opens this drawer instead of going nowhere — one row per
 * `run_log` error event in the last 24h (agent/eoa/api/services.py `recent_errors`).
 */
export function ErrorsDrawer({
  errors,
  onClose,
}: {
  errors: RecentErrorLogEntry[];
  onClose: () => void;
}) {
  const { t } = useI18n();
  return (
    <div className="fixed inset-0 z-40 flex items-start justify-center bg-black/30 p-4 pt-20" onClick={onClose}>
      <div
        role="dialog"
        aria-label={t("morning.errorsDrawerTitle")}
        className="w-full max-w-lg rounded-lg border border-border-strong bg-bg-raised p-4 shadow-panel"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-fg">{t("morning.errorsDrawerTitle")}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("morning.errorsDrawerClose")}
            className="rounded p-1 text-fg-dim hover:bg-bg-sunken"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        {errors.length === 0 ? (
          <p className="text-sm text-fg-dim">{t("morning.errorsDrawerEmpty")}</p>
        ) : (
          <div className="max-h-96 overflow-y-auto">
            <table className="w-full text-start text-xs">
              <thead>
                <tr className="text-fg-dim">
                  <th className="p-1.5 text-start font-medium">{t("morning.errorsDrawerStage")}</th>
                  <th className="p-1.5 text-start font-medium">{t("morning.errorsDrawerTime")}</th>
                  <th className="p-1.5 text-start font-medium">{t("morning.errorsDrawerMessage")}</th>
                </tr>
              </thead>
              <tbody>
                {errors.map((e) => (
                  <tr key={e.id} className="border-t border-border">
                    <td className="p-1.5 align-top text-fg-dim">{e.stage ?? "—"}</td>
                    <td className="whitespace-nowrap p-1.5 align-top font-mono text-fg-muted">
                      {e.at ? formatDateTime(e.at) : "—"}
                    </td>
                    <td className="p-1.5 align-top">
                      <bdi className="block" dir="auto">
                        {e.message}
                      </bdi>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <Link
          to="/morning#pipeline-replay"
          onClick={onClose}
          className="mt-3 block text-center text-xs font-medium text-accent hover:underline"
        >
          {t("morning.errorsDrawerViewLog")}
        </Link>
      </div>
    </div>
  );
}
