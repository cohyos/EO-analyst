import { Fragment } from "react";

/**
 * Content review (docs/qa/content_review/CR-ui.md): several free-text fields the server
 * generates -- most visibly `investigation.question` (`GET /api/investigations`,
 * `.../:jobId`) -- embed a quoted Latin/English title inline inside an otherwise-Hebrew
 * sentence, e.g. `אמת והרחב את הדיווח "The all-new 15-300 mm f/4 MWIR zoom engineered for 10 µm
 * SXGA detectors": מי הצדדים...`. Rendered as a single plain-text node in an RTL context, the
 * quote marks are bidi-neutral characters with nothing to pin them to the LTR run they visually
 * belong with, so they (and the punctuation right after the closing quote) can land in a
 * visually confusing spot. Report prose gets the equivalent fix server-side (already-marked-up
 * `<bdi>` runs, see lib/reportHtml.ts `fixBdiSpacing`); this covers the same pattern for plain
 * strings with no markup to begin with, by finding a `"…"` span that opens with a Latin letter
 * and wrapping just that span in a real `<bdi dir="ltr">`, leaving the surrounding Hebrew text
 * (including the quote marks themselves, which stay adjacent to their content) as plain text.
 *
 * Deliberately narrow: only a double-quoted run starting with an ASCII letter counts (a
 * Hebrew-quoted phrase, e.g. Hebrew text in "מרכאות", is left untouched -- it doesn't have this
 * bidi problem since it's already all one direction).
 */
const QUOTED_LATIN_RE = /"([A-Za-z][^"]*)"/g;

export function renderBidiText(text: string | null | undefined): React.ReactNode {
  if (!text) return text;
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  QUOTED_LATIN_RE.lastIndex = 0;
  while ((match = QUOTED_LATIN_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(<Fragment key={key++}>{text.slice(lastIndex, match.index)}</Fragment>);
    }
    parts.push(
      <Fragment key={key++}>
        {'"'}
        <bdi dir="ltr">{match[1]}</bdi>
        {'"'}
      </Fragment>,
    );
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < text.length) {
    parts.push(<Fragment key={key++}>{text.slice(lastIndex)}</Fragment>);
  }
  return parts;
}
