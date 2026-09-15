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

// =================================================================================================
// CR-invest.md (docs/qa/content_review/CR-invest.md): `renderBidiText` above only isolates a
// *quoted* Latin span -- deliberately narrow, per its own docstring. Investigation answers
// (`answer_he`/`key_facts`/`what_was_tried_he`, rendered by `AnswerText`/`CitationText`) embed
// unquoted Latin/number runs constantly ("Ophir Optronics", "MKS Instruments", "15-300 mm",
// "MWIR") with nothing marking their boundaries either -- the exact bug behind the reported
// "Ophir® SupIR-Xהוא"/"MKS Instruments) לקונצרן" glued-together text. `renderBidiRuns` below
// generalizes: it segments text into Hebrew-script vs. Latin-letter/digit ("other") runs -- the
// same algorithm as `eoa.search.deep_search._split_bidi_runs` (Hebrew-block codepoint ranges,
// punctuation inherits the surrounding run, a closing bracket takes its matching opening
// bracket's class for symmetry) -- and wraps every "other" run in a real `<bdi dir="ltr">`,
// instead of the backend's own invisible-Unicode-isolate-mark approach (which several
// viewers/fonts render as a visible glyph -- see `eoa.report.textnorm`'s docstring for that
// exact caveat, and CR-invest.md for why that's the display-time fix here rather than a change to
// what the backend emits). Deliberately a *separate* function from `renderBidiText` rather than a
// generalization of it: `renderBidiText`'s narrower quoted-span contract (and its existing tests)
// stay exactly as they are for its own call sites (`InvestigationsListPage`'s question rendering).
// =================================================================================================

const _BIDI_HEBREW_RANGES: ReadonlyArray<readonly [number, number]> = [
  [0x0590, 0x05ff],
  [0xfb1d, 0xfb4f],
];

function isHebrewChar(ch: string): boolean {
  const cp = ch.codePointAt(0) ?? 0;
  return _BIDI_HEBREW_RANGES.some(([lo, hi]) => cp >= lo && cp <= hi);
}

function isLatinOrDigitChar(ch: string): boolean {
  return /[A-Za-z0-9]/.test(ch);
}

type BidiRunClass = "he" | "other";

function bidiRunClass(ch: string): BidiRunClass | null {
  if (isHebrewChar(ch)) return "he";
  if (isLatinOrDigitChar(ch)) return "other";
  return null;
}

const _BIDI_BRACKET_OPEN_TO_CLOSE: Record<string, string> = { "(": ")", "[": "]", "{": "}" };
const _BIDI_BRACKET_CLOSE_TO_OPEN: Record<string, string> = { ")": "(", "]": "[", "}": "{" };

/** Segment `text` into (class, chunk) runs -- see the module note above. Ports
 * `eoa.search.deep_search._split_bidi_runs` to TypeScript, minus its bidi-space-insertion
 * sibling (`_bidi_space_and_isolate`): the frontend only isolates for display, it never mutates
 * the underlying text (that would risk inserting a space into content a citation-marker regex
 * downstream still needs to match verbatim). */
function splitBidiRuns(text: string): Array<[BidiRunClass, string]> {
  const runs: Array<[BidiRunClass, string]> = [];
  let cur: BidiRunClass | null = null;
  let buf: string[] = [];
  const bracketStack: Array<[string, BidiRunClass]> = [];

  for (const ch of text) {
    const base = bidiRunClass(ch);
    let c: BidiRunClass;
    if (base !== null) {
      c = base;
    } else if (ch in _BIDI_BRACKET_OPEN_TO_CLOSE) {
      c = cur ?? "he";
    } else if (
      ch in _BIDI_BRACKET_CLOSE_TO_OPEN &&
      bracketStack.length > 0 &&
      bracketStack[bracketStack.length - 1][0] === _BIDI_BRACKET_CLOSE_TO_OPEN[ch]
    ) {
      c = bracketStack[bracketStack.length - 1][1];
    } else {
      c = cur ?? "he";
    }

    if (cur !== null && c !== cur && buf.length > 0) {
      runs.push([cur, buf.join("")]);
      buf = [];
    }
    cur = c;
    buf.push(ch);

    if (base === null) {
      if (ch in _BIDI_BRACKET_OPEN_TO_CLOSE) {
        bracketStack.push([ch, cur]);
      } else if (
        ch in _BIDI_BRACKET_CLOSE_TO_OPEN &&
        bracketStack.length > 0 &&
        bracketStack[bracketStack.length - 1][0] === _BIDI_BRACKET_CLOSE_TO_OPEN[ch]
      ) {
        bracketStack.pop();
      }
    }
  }
  if (buf.length > 0 && cur !== null) runs.push([cur, buf.join("")]);
  return runs;
}

/** Wrap every Latin-letter/digit run in `text` in a real `<bdi dir="ltr">`, leaving Hebrew runs
 * (and punctuation, which inherits its surrounding run -- see `splitBidiRuns`) as plain text. A
 * no-op passthrough for `null`/`undefined`/empty input, and for text with no Latin/digit run at
 * all (returns the original string unchanged rather than a single-element array, so a caller
 * doing a plain string comparison/length check on non-Latin text still works). */
export function renderBidiRuns(text: string | null | undefined): React.ReactNode {
  if (!text) return text;
  const runs = splitBidiRuns(text);
  if (runs.length === 0) return text;
  if (runs.length === 1 && runs[0][0] === "he") return text;
  return runs.map(([cls, chunk], i) => {
    if (cls !== "other") return <Fragment key={i}>{chunk}</Fragment>;
    // Round-2 mobile fix (UI-MOBILE-iphone.md #5): a leading/trailing space that lands *inside*
    // a `<bdi>` is swallowed by the isolate -- it's atomic, so the space collapses against the
    // isolated run's own edge instead of visually separating it from the Hebrew word beside it.
    // That's exactly the "מערכת SPECTRO XRהיא" glued-together bug (no visible gap between the
    // English run and the following Hebrew word) -- the same class of bug `fixBdiSpacing`
    // (lib/reportHtml.ts) already fixes for server-rendered report HTML. `splitBidiRuns` itself
    // still folds a boundary space into whichever run was open (needed for run-splitting to
    // agree with the backend's own `_split_bidi_runs`), so the fix lives here at render time:
    // move any leading/trailing whitespace of an "other" chunk back outside the `<bdi>` as plain
    // text, keeping the same visible characters in the same order.
    const leadingMatch = /^\s+/.exec(chunk);
    const leading = leadingMatch ? leadingMatch[0] : "";
    const rest = chunk.slice(leading.length);
    const trailingMatch = /\s+$/.exec(rest);
    const trailing = trailingMatch ? trailingMatch[0] : "";
    const core = trailing ? rest.slice(0, rest.length - trailing.length) : rest;
    if (!core) return <Fragment key={i}>{chunk}</Fragment>;
    return (
      <Fragment key={i}>
        {leading}
        <bdi dir="ltr">{core}</bdi>
        {trailing}
      </Fragment>
    );
  });
}
