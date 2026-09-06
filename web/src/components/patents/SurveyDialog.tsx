import { useEffect, useRef, useState } from "react";
import { X, Download } from "lucide-react";
import type { PatentSurveyCard } from "@/types/api";

const MIN_TOPIC_LENGTH = 8;

const STATUS_LABEL_HE: Record<PatentSurveyCard["status"], string> = {
  running: "פועל",
  done: "הושלם",
  failed: "נכשל",
};

/** A14 "סקר פטנטים" launcher dialog: a free-text topic (min 8 chars) plus a list of past surveys
 * with docx download links. Mirrors web/src/components/investigations/NewInvestigationDialog.tsx's
 * modal shape. */
export function SurveyDialog({
  onClose,
  onSubmit,
  submitting,
  surveys,
}: {
  onClose: () => void;
  onSubmit: (topic: string) => void;
  submitting: boolean;
  surveys: PatentSurveyCard[];
}) {
  const [topic, setTopic] = useState("");
  const [touched, setTouched] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const trimmed = topic.trim();
  const isValid = trimmed.length >= MIN_TOPIC_LENGTH;
  const showError = touched && !isValid;

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    inputRef.current?.focus();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="סקר פטנטים"
        onClick={(e) => e.stopPropagation()}
        className="max-h-[85vh] w-full max-w-xl overflow-y-auto rounded-lg border border-border-strong bg-bg-raised shadow-panel"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">סקר פטנטים חדש</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="סגור"
            className="rounded p-1 text-fg-muted hover:bg-bg-sunken"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setTouched(true);
            if (isValid) onSubmit(trimmed);
          }}
          className="space-y-3 border-b border-border p-4"
        >
          <label htmlFor="patent-survey-topic" className="block text-sm text-fg-muted">
            נושא הסקר (לפחות {MIN_TOPIC_LENGTH} תווים) -- לדוגמה: "FPA עם פיקסל דיגיטלי (DROIC)".
          </label>
          <input
            id="patent-survey-topic"
            ref={inputRef}
            dir="auto"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            onBlur={() => setTouched(true)}
            aria-invalid={showError || undefined}
            aria-describedby={showError ? "patent-survey-topic-error" : undefined}
            className={`w-full rounded-md border bg-bg-sunken p-2 text-sm outline-none focus:border-accent ${
              showError ? "border-danger" : "border-border"
            }`}
            placeholder="נושא הסקר…"
          />
          {showError && (
            <p id="patent-survey-topic-error" role="alert" className="text-xs text-danger">
              {`נושא קצר מדי -- נדרשים לפחות ${MIN_TOPIC_LENGTH} תווים (כרגע ${trimmed.length}).`}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded-md border border-border px-3 py-1.5 text-sm text-fg-muted hover:bg-bg-sunken"
            >
              ביטול
            </button>
            <button
              type="submit"
              disabled={!isValid || submitting}
              className="rounded-md bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "מריץ סקר…" : "הרץ סקר"}
            </button>
          </div>
        </form>
        <div className="p-4">
          <h3 className="mb-2 text-sm font-semibold text-fg">סקרים קודמים</h3>
          {surveys.length === 0 ? (
            <p className="text-sm text-fg-dim">אין עדיין סקרים.</p>
          ) : (
            <ul className="space-y-1.5">
              {surveys.map((s) => (
                <li
                  key={s.id}
                  className="flex items-center justify-between gap-2 rounded-md border border-border px-2 py-1.5 text-sm"
                >
                  <span className="truncate text-fg" dir="auto">
                    {s.topic}
                  </span>
                  <span className="shrink-0 text-xs text-fg-dim">{STATUS_LABEL_HE[s.status]}</span>
                  {s.path_docx ? (
                    <a
                      href={s.path_docx}
                      className="flex shrink-0 items-center gap-1 text-xs text-accent hover:underline"
                    >
                      <Download size={12} aria-hidden="true" />
                      docx
                    </a>
                  ) : (
                    <span className="shrink-0 text-xs text-fg-dim">—</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
