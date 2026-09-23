import { ShieldAlert } from "lucide-react";
import { useT } from "@/i18n";

/**
 * W10 (docs/REVIEW_2026-09-06_evening.md round 4): shown whenever an investigation's answer was
 * partially blocked by the L2 prompt-injection guard (`security_review: true` on the job's
 * result). Two actions: a rerun button re-runs the investigation as a brand-new, independent
 * job, screened again from scratch (`POST /api/security-reviews/{job_id}/approve`), "דחה" marks
 * it reviewed with no re-run (`POST /api/security-reviews/{job_id}/dismiss}`). Shared between
 * `InvestigationDetailPage` (one banner, this investigation) and `InboxPage` (a list of these,
 * one per pending review).
 *
 * F28/N08 (SOL-REVIEW-2026-09-24): the rerun button used to read "אשר והמשך" ("approve and
 * continue"), implying the flagged content gets approved/whitelisted for this re-run -- there is
 * no such override anywhere in the backend (`eoa.api.routes.security_review
 * .approve_security_review`'s own docstring: "an honest, ordinary duplicate investigation").
 * The label now comes from `investigations.securityReviewRerun`/`securityReviewRerunning` (i18n)
 * and says plainly that it re-runs, not that it approves/whitelists anything.
 */
export function SecurityReviewBanner({
  reasonHe,
  snippet,
  onApprove,
  onDismiss,
  approving,
  dismissing,
}: {
  reasonHe: string | null;
  snippet: string | null;
  onApprove: () => void;
  onDismiss: () => void;
  approving?: boolean;
  dismissing?: boolean;
}) {
  const t = useT();
  return (
    <div
      role="alert"
      data-testid="security-review-banner"
      className="rounded-lg border border-danger bg-level-red-bg p-3 text-sm"
    >
      <div className="flex items-start gap-2">
        <ShieldAlert
          size={18}
          className="mt-0.5 shrink-0 text-danger"
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <p className="font-semibold text-danger">נחסם חלקית לבדיקת אבטחה</p>
          {reasonHe && (
            <bdi className="mt-1 block text-fg" dir="auto">
              {reasonHe}
            </bdi>
          )}
          {snippet && (
            <div className="mt-2 rounded-md border border-border-strong bg-bg-raised p-2">
              <p className="mb-1 text-xs font-semibold text-fg-dim">הקטע שנחסם</p>
              <bdi
                className="block break-words font-mono text-xs text-fg-muted"
                dir="auto"
              >
                {snippet}
              </bdi>
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={onApprove}
              disabled={approving || dismissing}
              className="rounded-md bg-danger px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {approving
                ? t("investigations.securityReviewRerunning")
                : t("investigations.securityReviewRerun")}
            </button>
            <button
              type="button"
              onClick={onDismiss}
              disabled={approving || dismissing}
              className="rounded-md border border-border-strong px-3 py-1.5 text-xs hover:bg-bg-sunken disabled:opacity-50"
            >
              {dismissing ? "דוחה…" : "דחה"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
