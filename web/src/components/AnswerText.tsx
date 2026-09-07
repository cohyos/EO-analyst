import { Fragment } from "react";
import { CitationText, type CitationLike } from "@/components/CitationText";
import { parseAnswerSections } from "@/lib/answerFormat";
import { cn } from "@/lib/cn";

/**
 * CR-invest.md (docs/qa/content_review/CR-invest.md): renders an investigation's `answer_he` (or
 * `what_was_tried_he`/`contradictions_he`) as structured Hebrew instead of the raw assembled
 * markdown string the page used to dump straight into one `<bdi>` block -- literal "###"/"- "
 * markers, citation numbers glued to words, and a trailing "### מקורות" URL dump the page already
 * shows separately (see `InvestigationDetailPage`'s own sources rendering via `CitationText`'s
 * `citations` prop / the provenance section).
 *
 * `parseAnswerSections` (web/src/lib/answerFormat.ts) does the actual cleanup + parsing (backend
 * artifacts from before the W27 punctuation-normalization pass landed, grouped `[1,2]`-style
 * citation markers, heading/bullet/paragraph structure) -- this component only maps that
 * structure onto real elements and hands each block's text to `CitationText` for citation-chip +
 * per-run bidi isolation, exactly as the page's own plain-text answer rendering already did.
 *
 * `null`/empty/whitespace-only `text` renders nothing (`null`), so a caller can use this
 * unconditionally behind its own existing `{data.answer.answer_he && (...)}` guard or drop that
 * guard entirely.
 */
export function AnswerText({
  text,
  citations = [],
  onOpenItem,
  className,
  size = "sm",
}: {
  text: string | null | undefined;
  citations?: CitationLike[];
  onOpenItem?: (itemId: number) => void;
  className?: string;
  /** Text size for headings/paragraphs/list items -- "sm" (default) for the main answer, "xs"
   * for the smaller "מה נוסה"/"פערים" sub-blocks that previously rendered at `text-xs`. */
  size?: "sm" | "xs";
}) {
  const sections = parseAnswerSections(text);
  if (sections.length === 0) return null;
  const bodyTextClass = size === "xs" ? "text-xs" : "text-sm";

  return (
    <div dir="rtl" className={className}>
      {sections.map((section, i) => (
        <Fragment key={i}>
          {section.title && (
            <h4 className={cn("text-xs font-semibold text-fg-dim", i > 0 && "mt-3", "mb-1")}>
              {section.title}
            </h4>
          )}
          {section.blocks.map((block, j) =>
            block.type === "list" ? (
              <ul key={j} className={cn("list-disc space-y-1 ps-5 leading-relaxed", bodyTextClass)}>
                {block.items.map((item, k) => (
                  <li key={k}>
                    <CitationText text={item} citations={citations} onOpenItem={onOpenItem} />
                  </li>
                ))}
              </ul>
            ) : (
              <p key={j} className={cn("leading-relaxed", bodyTextClass, j > 0 && "mt-2")}>
                <CitationText text={block.text} citations={citations} onOpenItem={onOpenItem} />
              </p>
            ),
          )}
        </Fragment>
      ))}
    </div>
  );
}
