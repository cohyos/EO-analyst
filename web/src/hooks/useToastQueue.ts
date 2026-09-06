import { useCallback, useEffect, useState } from "react";

export type ToastTone = "ok" | "warn" | "danger" | "info";

export interface ToastMessage {
  id: number;
  text: string;
  tone: ToastTone;
  linkTo?: string;
  linkLabel?: string;
}

let nextToastId = 1;

/**
 * Small reusable local toast queue -- used by the feed's "I" investigate shortcut (Q5-3) and the
 * new-investigation dialog (Q5-6, docs/qa/findings_Q5_r1.md), both of which needed a lightweight
 * success/conflict/error toast and had none available to reuse (RunNowButton.tsx has its own
 * bespoke single-slot toast wired to its specific `topBar.runNow*` i18n keys, predating this and
 * left as-is). Each call to `push` appends a toast; toasts auto-dismiss after `autoDismissMs`.
 */
export function useToastQueue(autoDismissMs = 6000) {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (text: string, opts?: { tone?: ToastTone; linkTo?: string; linkLabel?: string }) => {
      const id = nextToastId++;
      setToasts((prev) => [
        ...prev,
        { id, text, tone: opts?.tone ?? "info", linkTo: opts?.linkTo, linkLabel: opts?.linkLabel },
      ]);
      return id;
    },
    [],
  );

  useEffect(() => {
    if (toasts.length === 0) return;
    const timers = toasts.map((t) => setTimeout(() => dismiss(t.id), autoDismissMs));
    return () => timers.forEach(clearTimeout);
  }, [toasts, autoDismissMs, dismiss]);

  return { toasts, push, dismiss };
}
