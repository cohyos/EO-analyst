import { useRef, useState } from "react";
import { Copy, Mail, MessageCircle } from "lucide-react";
import { useT } from "@/i18n";
import {
  composeShareUrl,
  copyShareContent,
  prepareShareContent,
  type ShareLink,
} from "@/lib/shareContent";

export function ContentShareActions({
  title,
  links = [],
}: {
  title?: string;
  links?: ShareLink[];
}) {
  const root = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState("");
  const [shareTitle, setShareTitle] = useState(title || "EO-Analyst");
  const [manualText, setManualText] = useState<string | null>(null);
  const t = useT();

  async function act(channel: "copy" | "email" | "whatsapp") {
    const target = root.current?.closest<HTMLElement>("[data-share-content]");
    if (!target) return;
    const content = prepareShareContent(target, title, links);
    setShareTitle(content.title);
    setManualText(null);
    if (channel !== "copy") {
      const compose = composeShareUrl(channel, content);
      if (!compose.needsPaste) {
        window.open(compose.url, "_blank", "noopener,noreferrer");
        setStatus(t("share.composeOpened"));
        return;
      }
      // Do not open an empty composer until the user has copied the full long report.
      setManualText(content.text);
      try {
        await copyShareContent(content);
        setStatus(t("share.longContent"));
      } catch {
        setStatus(t("share.manualCopy"));
      }
      return;
    }
    try {
      await copyShareContent(content);
      setStatus(t("share.copied"));
    } catch {
      setManualText(content.text);
      setStatus(t("share.manualCopy"));
    }
  }

  return (
    <div ref={root} data-share-actions className="my-2 space-y-2 text-xs">
      {/* Below `md:` this collapses to a single compact row of icon-only buttons (defect #2):
          the label text is hidden (`hidden md:inline`) and the visible icon shrinks to a
          centered glyph inside a 40x40 tap target, so three stacked full-text rows never push
          the page's own heading below the fold on a phone. `aria-label` keeps the accessible
          name identical to the md:+ visible label. At `md:`+ this is byte-for-byte the
          original row: auto height, icon + visible label, start-aligned. */}
      <div className="flex flex-wrap gap-2" role="group" aria-label={t("share.actions")}>
        {(["copy", "email", "whatsapp"] as const).map((channel) => {
          const Icon =
            channel === "copy" ? Copy : channel === "email" ? Mail : MessageCircle;
          const label = t(`share.${channel}`);
          return (
            <button
              key={channel}
              type="button"
              onClick={() => void act(channel)}
              aria-label={label}
              className="inline-flex h-10 w-10 items-center justify-center gap-0 rounded-md border border-border bg-bg-raised px-0 py-0 hover:bg-bg-sunken focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent md:h-auto md:w-auto md:justify-start md:gap-1.5 md:px-2.5 md:py-1.5"
            >
              <Icon size={14} aria-hidden="true" className="shrink-0" />
              <span className="hidden md:inline">{label}</span>
            </button>
          );
        })}
      </div>
      {status && (
        <p role="status" className="text-fg-muted">
          {status}
        </p>
      )}
      {manualText !== null && (
        <div className="space-y-2 rounded-md border border-border p-2">
          <textarea
            readOnly
            value={manualText}
            aria-label={t("share.fullContent")}
            dir="auto"
            onFocus={(e) => e.currentTarget.select()}
            className="h-40 w-full rounded border border-border bg-bg-raised p-2 text-fg"
          />
          <div className="flex flex-wrap gap-3">
            <a
              href={`mailto:?subject=${encodeURIComponent(shareTitle)}`}
              className="text-accent underline"
            >
              {t("share.openEmail")}
            </a>
            <a
              href="https://wa.me/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-accent underline"
            >
              {t("share.openWhatsapp")}
            </a>
            <button
              type="button"
              onClick={() => setManualText(null)}
              className="text-fg-dim"
            >
              {t("share.close")}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
