import { CitationText, type CitationLike } from "@/components/CitationText";
import { renderBidiRuns } from "@/lib/bidiText";
import { useT } from "@/i18n";

/**
 * PD-ui (docs/PLAN_PRODUCT_DOSSIER.md section 6): "every fact cell carries citations; a field the
 * research could not establish is rendered as 'לא נמצא במקורות' -- never estimated, never filled
 * from model memory" (section 1). Every grounded value in a `ProductDossierOut` carries its own
 * `cites: number[]` alongside the value -- this renders the value with `[n]` citation chips
 * appended (reusing `CitationText`/`CitationChip`, exactly like every other cited-prose surface in
 * this app) when there is a value, or the honest "not found in sources" placeholder when there
 * isn't.
 */
export function DossierFactText({
  text,
  cites,
  citations,
  className,
}: {
  text: string | null | undefined;
  cites: number[];
  citations: CitationLike[];
  className?: string;
}) {
  if (!text || !text.trim()) return <NotFoundInSources className={className} />;
  const suffix = cites.length > 0 ? ` ${cites.map((n) => `[${n}]`).join("")}` : "";
  return (
    <span className={className}>
      <CitationText text={`${text}${suffix}`} citations={citations} />
    </span>
  );
}

/** Plain, non-cited display value (e.g. a bare number/date already normalized for display) that
 * still needs the empty-value placeholder + bidi-safe Latin/digit isolation, but has no citation
 * markers of its own to render (nothing to append `[n]` to). */
export function DossierPlainText({ text, className }: { text: string | null | undefined; className?: string }) {
  if (!text || !text.trim()) return <NotFoundInSources className={className} />;
  return <span className={className}>{renderBidiRuns(text)}</span>;
}

export function NotFoundInSources({ className }: { className?: string }) {
  const t = useT();
  return (
    <span className={className ? `${className} italic text-fg-dim` : "italic text-fg-dim"}>
      {t("dossiers.notFoundInSources")}
    </span>
  );
}

/** Trailing "sources" column shared by every dossier table (`DossierTable`): just the row's own
 * `[n]` citation chips, or the "not found in sources" placeholder when a row carries none --
 * keeps every table's other cells plain values (see that component's own doc comment for why the
 * citations live in one dedicated column instead of being appended per-cell). */
export function DossierCiteChips({ cites, citations }: { cites: number[]; citations: CitationLike[] }) {
  if (cites.length === 0) return <NotFoundInSources />;
  return <CitationText text={cites.map((n) => `[${n}]`).join(" ")} citations={citations} />;
}

/** A `list[Sentence]` field (summary/risks_and_gaps/bd_implications/what_changed): one `<li>` per
 * sentence, each cited independently, or the section's own empty-state copy when the list is
 * empty. */
export function DossierSentenceList({
  sentences,
  citations,
  emptyLabel,
}: {
  sentences: { text_he: string; cites: number[] }[];
  citations: CitationLike[];
  emptyLabel: string;
}) {
  if (sentences.length === 0) {
    return <p className="text-sm text-fg-dim">{emptyLabel}</p>;
  }
  return (
    <ul className="list-disc space-y-1.5 ps-5 text-sm leading-relaxed">
      {sentences.map((s, i) => (
        <li key={i}>
          <DossierFactText text={s.text_he} cites={s.cites} citations={citations} />
        </li>
      ))}
    </ul>
  );
}
