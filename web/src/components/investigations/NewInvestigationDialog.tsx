import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

/**
 * U12 "חקירה חדשה" (docs/REVIEW_2026-09-05.md): lets the analyst start a free-standing deep-search
 * investigation from a typed question, instead of only being able to trigger one from a feed item.
 */
export function NewInvestigationDialog({
  onClose,
  onSubmit,
  submitting,
}: {
  onClose: () => void;
  onSubmit: (question: string) => void;
  submitting: boolean;
}) {
  const [question, setQuestion] = useState("");
  const dialogRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

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
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="חקירה חדשה"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-lg rounded-lg border border-border-strong bg-bg-raised shadow-panel outline-none"
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold">חקירה חדשה</h2>
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
            if (question.trim()) onSubmit(question.trim());
          }}
          className="space-y-3 p-4"
        >
          <label htmlFor="new-investigation-question" className="block text-sm text-fg-muted">
            הקלד שאלת מחקר עצמאית ומלאה (עם שמות חברות/מערכות/תוכניות רלוונטיים) שהחקירה תנסה לענות עליה.
          </label>
          <textarea
            id="new-investigation-question"
            ref={inputRef}
            dir="auto"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={4}
            className="w-full rounded-md border border-border bg-bg-sunken p-2 text-sm outline-none focus:border-accent"
            placeholder='למשל: "מה היקף החוזה שקיבלה Elbit Systems מחיל האוויר הצרפתי עבור פודי הכיוון, ומתי הוא צפוי להסתיים?"'
          />
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
              disabled={!question.trim() || submitting}
              className="rounded-md bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "פותח…" : "התחל חקירה"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
