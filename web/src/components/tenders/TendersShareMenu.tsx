import { useEffect, useRef, useState } from "react";
import {
  Copy,
  Download,
  ExternalLink,
  FileCode,
  Link2,
  Mail,
  MessageCircle,
  Share2,
  type LucideIcon,
} from "lucide-react";
import { useI18n, useT } from "@/i18n";
import type { TenderFiltersState } from "@/components/tenders/TenderFilters";
import { buildTendersHtml, buildTendersShareText } from "@/lib/tendersExport";
import type { ForecastCard, TenderCard } from "@/types/api";

/** mailto:/wa.me bodies stay well under either channel's own URL-length limits. */
const BODY_CAP = 1800;

function capBody(text: string, max = BODY_CAP): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

/**
 * "שתף HTML" -- the tenders board's share-as-standalone-HTML menu (product request, 2026-09-15).
 * Builds the export document lazily per action (never stored in state) so every action always
 * reflects the tenders/filters currently passed in, mirroring `ContentShareActions`'s inline
 * status-message pattern instead of the app-level toast queue (no page-level wiring needed).
 */
export function TendersShareMenu({
  tenders,
  forecasts,
  filters,
  showClosedArchived,
  includeForecasts,
}: {
  tenders: TenderCard[];
  forecasts?: ForecastCard[];
  filters: TenderFiltersState;
  showClosedArchived: boolean;
  /** Say-so requirement: only the forecasts tab bundles forecasts into the export, and the
   * trigger's own label/title reflects that (`tenders.share.triggerWithForecasts`). */
  includeForecasts: boolean;
}) {
  const t = useT();
  const { locale } = useI18n();
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState("");
  const [canShareFile, setCanShareFile] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const disabled = tenders.length === 0;

  // Feature-detect the Web Share API's file-sharing support once on mount -- `navigator.share`
  // exists on more browsers than actually accept `files`, so probe with a throwaway File rather
  // than just checking for the function's presence (desktop Chrome supports `share()` for text
  // but not files on some platforms).
  useEffect(() => {
    try {
      const probe = new File(["x"], "probe.html", { type: "text/html" });
      setCanShareFile(
        typeof navigator !== "undefined" &&
          typeof navigator.canShare === "function" &&
          navigator.canShare({ files: [probe] }),
      );
    } catch {
      setCanShareFile(false);
    }
  }, []);

  useEffect(() => {
    if (!open) return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    function onPointerDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("mousedown", onPointerDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("mousedown", onPointerDown);
    };
  }, [open]);

  function buildHtml(): string {
    return buildTendersHtml({
      tenders,
      forecasts: includeForecasts ? forecasts : undefined,
      filters: { ...filters, showClosedArchived },
      locale,
      appUrl: window.location.href,
      generatedAt: new Date(),
    });
  }
  function buildText(): string {
    return buildTendersShareText(tenders);
  }
  function fileName(): string {
    return `tenders-${new Date().toISOString().slice(0, 10)}.html`;
  }

  const docTitle = includeForecasts
    ? t("tenders.share.docTitleWithForecasts")
    : t("tenders.share.docTitle");

  async function handleShareFile() {
    try {
      const file = new File([buildHtml()], fileName(), { type: "text/html" });
      await navigator.share({ files: [file], title: docTitle });
      setOpen(false);
    } catch (err) {
      // AbortError: the user cancelled the native share sheet -- not an error worth surfacing.
      if ((err as { name?: string } | null)?.name === "AbortError") {
        setOpen(false);
        return;
      }
      setStatus(t("tenders.share.shareFailed"));
      setOpen(false);
    }
  }

  function handleDownload() {
    const blob = new Blob([buildHtml()], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName();
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setStatus(t("tenders.share.downloaded"));
    setOpen(false);
  }

  function handleOpenTab() {
    const blob = new Blob([buildHtml()], { type: "text/html" });
    const url = URL.createObjectURL(blob);
    window.open(url, "_blank", "noopener,noreferrer");
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
    setOpen(false);
  }

  async function handleCopyHtml() {
    try {
      if (navigator.clipboard?.write && typeof ClipboardItem !== "undefined") {
        await navigator.clipboard.write([
          new ClipboardItem({
            "text/html": new Blob([buildHtml()], { type: "text/html" }),
            "text/plain": new Blob([buildText()], { type: "text/plain" }),
          }),
        ]);
      } else if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(buildHtml());
      } else {
        throw new Error("Clipboard unavailable");
      }
      setStatus(t("tenders.share.copiedHtml"));
    } catch {
      setStatus(t("tenders.share.copyFailed"));
    }
    setOpen(false);
  }

  async function handleCopyLinks() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(buildText());
      setStatus(t("tenders.share.copiedLinks"));
    } catch {
      setStatus(t("tenders.share.copyFailed"));
    }
    setOpen(false);
  }

  function handleEmail() {
    const url = `mailto:?subject=${encodeURIComponent(docTitle)}&body=${encodeURIComponent(capBody(buildText()))}`;
    window.open(url, "_blank", "noopener,noreferrer");
    setOpen(false);
  }

  function handleWhatsapp() {
    const url = `https://wa.me/?text=${encodeURIComponent(capBody(buildText()))}`;
    window.open(url, "_blank", "noopener,noreferrer");
    setOpen(false);
  }

  const items: Array<{ key: string; label: string; icon: LucideIcon; onSelect: () => void }> = [
    ...(canShareFile
      ? [
          {
            key: "shareFile",
            label: t("tenders.share.shareFile"),
            icon: Share2,
            onSelect: () => void handleShareFile(),
          },
        ]
      : []),
    { key: "download", label: t("tenders.share.download"), icon: Download, onSelect: handleDownload },
    { key: "openTab", label: t("tenders.share.openTab"), icon: ExternalLink, onSelect: handleOpenTab },
    {
      key: "copyHtml",
      label: t("tenders.share.copyHtml"),
      icon: Copy,
      onSelect: () => void handleCopyHtml(),
    },
    {
      key: "copyLinks",
      label: t("tenders.share.copyLinks"),
      icon: Link2,
      onSelect: () => void handleCopyLinks(),
    },
    { key: "email", label: t("tenders.share.email"), icon: Mail, onSelect: handleEmail },
    { key: "whatsapp", label: t("tenders.share.whatsapp"), icon: MessageCircle, onSelect: handleWhatsapp },
  ];

  const triggerLabel = includeForecasts
    ? t("tenders.share.triggerWithForecasts")
    : t("tenders.share.trigger");

  return (
    <div className="relative inline-block shrink-0" ref={rootRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={triggerLabel}
        title={disabled ? t("tenders.share.disabledTitle") : triggerLabel}
        className="tap-target inline-flex h-10 items-center justify-center gap-1.5 rounded-md border border-border-strong px-2 text-sm text-fg-muted hover:bg-bg-sunken disabled:cursor-not-allowed disabled:opacity-50 md:px-3"
      >
        <FileCode size={16} aria-hidden="true" />
        <span className="hidden md:inline">{triggerLabel}</span>
      </button>
      {open && !disabled && (
        <div
          role="menu"
          aria-label={triggerLabel}
          className="absolute end-0 top-full z-30 mt-1 w-64 rounded-md border border-border-strong bg-bg-raised p-1.5 shadow-panel"
        >
          {items.map(({ key, label, icon: Icon, onSelect }) => (
            <button
              key={key}
              type="button"
              role="menuitem"
              onClick={onSelect}
              className="tap-target flex w-full min-h-10 items-center gap-2 rounded px-2 py-1.5 text-start text-sm text-fg hover:bg-bg-sunken focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent"
            >
              <Icon size={16} aria-hidden="true" />
              {label}
            </button>
          ))}
        </div>
      )}
      {status && (
        <p
          role="status"
          className="absolute end-0 top-full mt-1 w-64 rounded-md border border-border bg-bg-raised p-2 text-xs text-fg-dim shadow-panel"
        >
          {status}
        </p>
      )}
    </div>
  );
}
