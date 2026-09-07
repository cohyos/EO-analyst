import { useEffect, useRef, useState } from "react";
import { CheckCircle2, ChevronDown, CircleDashed, ExternalLink, ShieldCheck, TriangleAlert } from "lucide-react";
import type { Corroboration, CorroborationSourceKind } from "@/types/api";
import { timeAgo } from "@/lib/time";
import { cn } from "@/lib/cn";
import { useT } from "@/i18n";
import type { TranslationKey } from "@/i18n/types";

const POPOVER_WIDTH = 288; // px, matches w-72 -- same convention as ExplainScorePopover/DuplicateOutletsPopover.

const KIND_LABEL_KEY: Record<CorroborationSourceKind, TranslationKey> = {
  duplicate: "corr.kindDuplicate",
  same_event: "corr.kindSameEvent",
  official: "corr.kindOfficial",
};

/**
 * CORR (cross-source corroboration, 2026-09-07): the four-state badge described in
 * docs/qa/loop/round_7_fixes.md -- amber "מקור יחיד" (single_source), green "מאומת ב-N מקורות"
 * (corroborated, click-to-expand source list -- same fixed-positioned popover technique as
 * `DuplicateOutletsPopover`/`ExplainScorePopover` so it escapes the virtualized feed row's
 * clipped box), blue "מקור ראשוני רשמי" (official_primary), and no chip at all for "unknown" in
 * the feed row -- except in the item drawer/detail header, where `showUnknown` renders a subtle
 * grey "לא נבדק" chip instead of nothing, per spec ("a subtle grey 'לא נבדק' only in the drawer").
 *
 * Accepts `corroboration` as possibly `undefined`/`null` (the field is absent from any backend
 * build that predates this feature, and the client-side `ItemCard.corroboration` field is
 * declared optional for that reason) and treats that exactly like an explicit
 * `{status: "unknown"}` -- callers never need to null-check before rendering this component.
 */
export function CorroborationBadge({
  corroboration,
  size = "md",
  showUnknown = false,
}: {
  corroboration: Corroboration | null | undefined;
  size?: "sm" | "md";
  /** Render a subtle "לא נבדק" chip for status "unknown" instead of nothing -- pass this in the
   * item drawer / detail header, leave it false in feed/list rows so an unchecked item's row
   * doesn't grow a chip most other rows don't have (no layout shift). */
  showUnknown?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDocPointerDown(e: MouseEvent) {
      const target = e.target as Node;
      if (popRef.current?.contains(target) || btnRef.current?.contains(target)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const status = corroboration?.status ?? "unknown";
  const sizeClass = size === "sm" ? "h-5 text-[10px]" : "h-6 text-xs";
  const baseChip =
    "inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 font-medium";

  if (status === "single_source") {
    return (
      <span
        data-testid="corroboration-badge-single_source"
        role="img"
        aria-label={`${t("corr.singleSourceLabel")} — ${t("corr.singleSourceTooltip")}`}
        title={t("corr.singleSourceTooltip")}
        className={cn(baseChip, sizeClass, "border-warn/40 bg-warn/10 text-warn")}
      >
        <TriangleAlert size={size === "sm" ? 10 : 12} aria-hidden="true" />
        {t("corr.singleSourceLabel")}
      </span>
    );
  }

  if (status === "official_primary") {
    return (
      <span
        data-testid="corroboration-badge-official_primary"
        role="img"
        aria-label={t("corr.officialPrimaryLabel")}
        title={t("corr.officialPrimaryLabel")}
        className={cn(
          baseChip,
          sizeClass,
          "border-accent/40 bg-accent-muted text-accent",
        )}
      >
        <ShieldCheck size={size === "sm" ? 10 : 12} aria-hidden="true" />
        {t("corr.officialPrimaryLabel")}
      </span>
    );
  }

  if (status === "unknown") {
    if (!showUnknown) return null;
    return (
      <span
        data-testid="corroboration-badge-unknown"
        role="img"
        aria-label={t("corr.unknownLabel")}
        title={t("corr.unknownLabel")}
        className={cn(
          baseChip,
          sizeClass,
          "border-border-strong bg-bg-sunken text-fg-dim",
        )}
      >
        <CircleDashed size={size === "sm" ? 10 : 12} aria-hidden="true" />
        {t("corr.unknownLabel")}
      </span>
    );
  }

  // status === "corroborated" -- click-to-open popover listing the corroborating sources.
  const sources = corroboration?.sources ?? [];
  const count = corroboration?.count ?? sources.length;

  function toggle(e: React.MouseEvent) {
    e.stopPropagation();
    if (!open) {
      const rect = btnRef.current?.getBoundingClientRect();
      if (rect) {
        let left = rect.left;
        if (left + POPOVER_WIDTH > window.innerWidth - 8) {
          left = Math.max(8, window.innerWidth - POPOVER_WIDTH - 8);
        }
        setPos({ top: rect.bottom + 4, left });
      }
    }
    setOpen((v) => !v);
  }

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={toggle}
        onDoubleClick={(e) => e.stopPropagation()}
        aria-expanded={open}
        aria-label={t(open ? "corr.toggleListAriaHide" : "corr.toggleListAriaShow")}
        title={t("corr.corroboratedLabel", { count })}
        data-testid="corroboration-badge-corroborated"
        className={cn(
          baseChip,
          sizeClass,
          "border-ok/40 bg-ok/10 text-ok hover:border-ok",
        )}
      >
        <CheckCircle2 size={size === "sm" ? 10 : 12} aria-hidden="true" />
        {t("corr.corroboratedLabel", { count })}
        <ChevronDown size={size === "sm" ? 9 : 10} aria-hidden="true" />
      </button>
      {open && pos && (
        <div
          ref={popRef}
          role="dialog"
          aria-label={t("corr.sourcesListAriaLabel")}
          style={{ position: "fixed", top: pos.top, left: pos.left, width: POPOVER_WIDTH }}
          className="z-50 max-h-72 overflow-y-auto rounded-lg border border-border-strong bg-bg-raised p-2 text-xs shadow-panel"
          onClick={(e) => e.stopPropagation()}
        >
          <p className="mb-1.5 px-1 font-semibold text-fg-dim">
            {t("corr.sourcesListTitle", { count })}
          </p>
          <ul className="space-y-1">
            {sources.map((s, i) => (
              <li key={`${s.item_id}-${i}`} className="rounded-md p-1.5 hover:bg-bg-sunken">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <bdi className="block truncate font-medium text-fg" title={s.source_name || undefined}>
                      {s.source_name || "—"}
                    </bdi>
                    <div className="flex flex-wrap items-center gap-1.5 text-fg-dim">
                      <span className="rounded bg-bg-sunken px-1 py-0.5">
                        {t(KIND_LABEL_KEY[s.kind])}
                      </span>
                      <span>·</span>
                      <span className="font-mono">{timeAgo(s.published_at)}</span>
                    </div>
                  </div>
                  {s.url && (
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      aria-label={t("corr.openSource")}
                      title={t("corr.openSource")}
                      className="flex shrink-0 items-center gap-1 rounded border border-border-strong px-1.5 py-0.5 text-fg-dim hover:border-accent hover:text-accent"
                    >
                      <ExternalLink size={11} aria-hidden="true" />
                      {t("corr.openSource")}
                    </a>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}
