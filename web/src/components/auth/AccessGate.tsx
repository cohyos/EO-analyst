import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import { useI18n } from "@/i18n";
import { ApiError, getAuthRequired, loginRemoteAccess, subscribeAuthRequired } from "@/api/real";

/**
 * ADR-008 (docs/adr/008-remote-access.md): renders instead of the whole app whenever the API has
 * told us (a 401 `{"error":{"code":"auth_required"}}`, surfaced via `real.ts`'s tiny pub/sub) that
 * this client is reaching the API from a non-loopback host (Tailscale/LAN) and needs to log in
 * first. A complete no-op for the primary local usage this app was built around -- loopback
 * clients never see a 401 here, so `required` never flips true and `children` render unchanged.
 *
 * No passcode is ever kept client-side: a successful `POST /api/auth/login` sets an HttpOnly
 * session cookie the browser manages on its own; this component only tracks whether the gate
 * should currently be shown.
 */
export function AccessGate({ children }: { children: ReactNode }) {
  const [required, setRequired] = useState(getAuthRequired);
  const [passcode, setPasscode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const { t, dir } = useI18n();

  useEffect(() => subscribeAuthRequired(setRequired), []);

  useEffect(() => {
    if (required) inputRef.current?.focus();
  }, [required]);

  if (!required) return <>{children}</>;

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!passcode || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await loginRemoteAccess(passcode);
      setPasscode("");
      await queryClient.invalidateQueries();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("accessGate.genericError"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      dir={dir}
      className="flex min-h-dvh items-center justify-center bg-bg p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="access-gate-title"
    >
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-border-strong bg-bg-raised p-6 shadow-panel"
      >
        <div className="mb-3 flex items-center gap-2">
          <Lock size={18} aria-hidden="true" className="text-fg-muted" />
          <h1 id="access-gate-title" className="text-lg font-semibold">
            {t("accessGate.title")}
          </h1>
        </div>
        <p className="mb-4 text-sm text-fg-muted">{t("accessGate.subtitle")}</p>

        <label htmlFor="access-gate-passcode" className="mb-1 block text-sm font-medium">
          {t("accessGate.passcodeLabel")}
        </label>
        <input
          ref={inputRef}
          id="access-gate-passcode"
          type="password"
          inputMode="text"
          autoComplete="current-password"
          value={passcode}
          onChange={(e) => setPasscode(e.target.value)}
          className="mb-3 w-full rounded-md border border-border-strong bg-bg px-3 py-2 text-sm outline-none focus:border-accent"
          required
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "access-gate-error" : undefined}
        />

        {error && (
          <p id="access-gate-error" role="alert" className="mb-3 text-sm text-danger">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting || !passcode}
          className="w-full rounded-md bg-accent px-3 py-2 text-sm font-medium text-accent-fg hover:opacity-90 disabled:opacity-50"
        >
          {submitting ? t("accessGate.submitting") : t("accessGate.submit")}
        </button>
      </form>
    </div>
  );
}
