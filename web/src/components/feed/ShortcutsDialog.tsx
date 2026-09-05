import { X } from "lucide-react";
import { useEffect, useRef } from "react";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";

const SHORTCUT_ROWS: Array<{ keys: string; labelKey: TranslationKey }> = [
  { keys: "J / K", labelKey: "shortcuts.navigate" },
  { keys: "1 – 4", labelKey: "shortcuts.rate" },
  { keys: "X", labelKey: "shortcuts.archive" },
  { keys: "Enter", labelKey: "shortcuts.openFull" },
  { keys: "Space", labelKey: "shortcuts.quickPreview" },
  { keys: "I", labelKey: "shortcuts.investigate" },
  { keys: "A", labelKey: "shortcuts.addToContext" },
  { keys: "O", labelKey: "shortcuts.openSource" },
];

/**
 * U5: the feed's keyboard-shortcut reference, previously crammed into the
 * blue status line as mixed Hebrew/English ("ניווט: J/K · ארכיון X ·
 * Enter פרטים · ..."). Moved to an on-demand dialog with a plain two-column
 * table (key | action), opened via a "קיצורי מקלדת" (?) button.
 */
export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    dialogRef.current?.focus();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={t("feed.shortcutsDialogTitle")}
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-lg border border-border-strong bg-bg-raised shadow-panel outline-none"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">{t("feed.shortcutsDialogTitle")}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("common.close")}
            className="rounded p-1 text-fg-muted hover:bg-bg-sunken"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-start text-xs text-fg-dim">
              <th className="w-28 px-4 py-2 text-start font-medium">{t("feed.shortcutsColumnKey")}</th>
              <th className="px-4 py-2 text-start font-medium">{t("feed.shortcutsColumnAction")}</th>
            </tr>
          </thead>
          <tbody>
            {SHORTCUT_ROWS.map((row) => (
              <tr key={row.keys} className="border-b border-border last:border-b-0">
                <td className="px-4 py-2 font-mono text-xs text-accent">{row.keys}</td>
                <td className="px-4 py-2 text-fg">{t(row.labelKey)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
