import { useMemo, type MouseEvent } from "react";
import { useNavigate } from "react-router-dom";
import type { AskCitation } from "@/types/api";
import { renderAskMarkdown } from "@/lib/askMarkdown";

/**
 * Renders one assistant chat answer as sanitized, RTL-correct HTML (see `askMarkdown.ts` for the
 * Markdown -> HTML pipeline) instead of the raw markdown text the UI showed before U11 (the
 * 2026-09-06 bug report: `### **מקור 1**`, `**הערת איכות:**`, `> …` all rendered as literal
 * characters). `[n]` markers become `.eo-citation` chips exactly like `ReportBody` renders
 * report citations -- same click-delegation pattern (`.closest(".eo-citation")` on the
 * container, `data-item-id` -> SPA navigate, `data-url`-only -> let the anchor's own
 * `target="_blank"` open it) so the two citation UIs behave identically.
 */
export function AskAnswer({ text, citations }: { text: string; citations: AskCitation[] }) {
  const navigate = useNavigate();
  const html = useMemo(() => renderAskMarkdown(text, citations), [text, citations]);

  function citationTargetOf(e: MouseEvent<HTMLDivElement>): HTMLElement | null {
    return (e.target as HTMLElement).closest<HTMLElement>(".eo-citation");
  }

  function handleClick(e: MouseEvent<HTMLDivElement>) {
    const target = citationTargetOf(e);
    if (!target) return;
    const idAttr = target.getAttribute("data-item-id");
    if (idAttr) {
      e.preventDefault();
      navigate(`/items/${idAttr}`);
    }
    // No data-item-id but a data-url: the anchor itself already carries
    // target="_blank" rel="noopener noreferrer" -- let the browser handle it.
  }

  return (
    <div
      className="eo-ask-answer text-sm"
      dir="auto"
      onClick={handleClick}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
