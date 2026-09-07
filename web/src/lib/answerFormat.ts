/**
 * CR-invest.md (docs/qa/content_review/CR-invest.md): `AnswerText` renders an investigation's
 * `answer_he` (and `what_was_tried_he`/`contradictions_he`) as structured Hebrew instead of one
 * wall of text with literal "###"/"- " markup. This module holds the two pure, testable pieces
 * that structuring needs: cleaning up legacy-era text artifacts, and parsing the backend's fixed
 * markdown shape (`eoa.search.deep_search.format_investigation_answer_he`, W27) into sections a
 * component can map onto real `<h4>`/`<p>`/`<ul>` elements.
 *
 * Why this exists client-side at all: the backend's own `eoa.report.textnorm.normalize_hebrew_
 * punctuation` already applies the same punctuation fixes before `answer_he` is stored -- but
 * only for investigations run *after* that pass landed. An already-stored answer (job 175, the
 * fixture this module's tests use) keeps its original text forever ("do not rewrite DB rows" --
 * CR-invest.md's explicit instruction), so the one place that can still clean it up is the
 * renderer. `normalizeHebrewPunctuation` below is therefore a deliberate, independent port of
 * `agent/eoa/report/textnorm.py`'s six-pass algorithm (not a copy-paste the two sides could drift
 * apart from unnoticed -- see each function's own docstring for the exact same reasoning as the
 * Python original) rather than an import, since nothing here can import Python.
 */

// --------------------------------------------------------------------------------------------
// Hebrew punctuation / legacy-artifact cleanup -- mirrors agent/eoa/report/textnorm.py's six
// passes, in the same order, for the same reasons (see that module's own docstring for the full
// rationale of each pass). Applied only to *display* text, never persisted.
// --------------------------------------------------------------------------------------------

const GERSHAYIM = "״"; // ״
const GERESH = "׳"; // ׳

const HEBREW_RANGES: ReadonlyArray<readonly [number, number]> = [
  [0x0590, 0x05ff],
  [0xfb1d, 0xfb4f],
];

function isHebrewChar(ch: string): boolean {
  const cp = ch.codePointAt(0) ?? 0;
  return HEBREW_RANGES.some(([lo, hi]) => cp >= lo && cp <= hi);
}

/** A literal backslash immediately before a quote/apostrophe (an unescaped JSON/string escape
 * that leaked into stored text, e.g. `כטב\"ם`) -- drop the backslash, leaving the quote for the
 * gershayim/geresh passes below. */
function unescapeStrayBackslashQuotes(text: string): string {
  return text.replace(/\\(["'])/g, "$1");
}

/** Bidi-isolate control characters (U+2066-U+2069) some legacy investigation answers embed
 * literally -- pure formatting noise once `AnswerText`/`CitationText` do their own `<bdi>`-based
 * isolation for display; several fonts/viewers render the raw control character as a visible
 * glyph instead of treating it as invisible, which is the exact "Ophir® SupIR-Xהוא"-adjacent
 * artifact CR-invest.md flagged. */
function stripBidiIsolates(text: string): string {
  return text.replace(/[⁦-⁩]/g, "");
}

/** Two-or-more literal ASCII `"` in a row collapse to one. */
function collapseDoubledQuotes(text: string): string {
  return text.replace(/"{2,}/g, '"');
}

/** A lone ASCII `"` directly between two Hebrew-script characters becomes the Hebrew gershayim
 * mark (U+05F4) -- the correct punctuation for a Hebrew acronym/abbreviation. */
function asciiQuoteToGershayim(text: string): string {
  const chars = Array.from(text);
  const last = chars.length - 1;
  for (let i = 0; i < chars.length; i++) {
    if (chars[i] !== '"' || i === 0 || i === last) continue;
    if (isHebrewChar(chars[i - 1]) && isHebrewChar(chars[i + 1])) chars[i] = GERSHAYIM;
  }
  return chars.join("");
}

/** An ASCII `'` directly after a Hebrew-script character becomes the Hebrew geresh mark
 * (U+05F3). */
function asciiApostropheToGeresh(text: string): string {
  const chars = Array.from(text);
  for (let i = 1; i < chars.length; i++) {
    if (chars[i] === "'" && isHebrewChar(chars[i - 1])) chars[i] = GERESH;
  }
  return chars.join("");
}

/** A stray space between a closing bracket/paren/quote and the sentence punctuation right after
 * it (e.g. `"[1, 5, 6] ."` -> `"[1, 5, 6]."`), often left behind by the two passes above. */
function collapseSpaceBeforeClosingPunctuation(text: string): string {
  return text.replace(/([\])"])\s+([.,;:!?])/g, "$1$2");
}

/** All six passes, in order -- see the module docstring for why this lives here independently of
 * `agent/eoa/report/textnorm.py`'s `normalize_hebrew_punctuation`, which it mirrors. Idempotent;
 * safe to call on already-clean text (a no-op) or on `null`/`undefined`/empty input. */
export function normalizeHebrewPunctuation(text: string | null | undefined): string {
  if (!text) return "";
  let out = text;
  out = unescapeStrayBackslashQuotes(out);
  out = stripBidiIsolates(out);
  out = collapseDoubledQuotes(out);
  out = asciiQuoteToGershayim(out);
  out = asciiApostropheToGeresh(out);
  out = collapseSpaceBeforeClosingPunctuation(out);
  return out;
}

/** A grouped citation marker like `[1,2]` (a single bracket pair holding several comma-separated
 * numbers) renders as one inert, unclickable blob under `CitationText`'s single-number `\[\d+\]`
 * regex -- and, per the CR-invest.md screenshot, visually "breaks" the word it sits against.
 * Expands it into individual adjacent markers (`[1,2]` -> `[1][2]`) so each becomes its own
 * clickable chip; `CitationChip`'s existing `mx-0.5` margin already provides visual separation
 * between adjacent chips, so no extra separator is inserted. A lone `[3]` is left untouched. */
export function expandGroupedCitationMarkers(text: string): string {
  return text.replace(/\[(\d+(?:\s*,\s*\d+)+)\]/g, (_match, nums: string) =>
    nums
      .split(/\s*,\s*/)
      .map((n) => `[${n}]`)
      .join(""),
  );
}

// --------------------------------------------------------------------------------------------
// Section parsing -- turns the backend's fixed markdown shape (see
// `eoa.search.deep_search.format_investigation_answer_he`'s module docstring: a heading-less
// direct-answer paragraph, then zero or more `### <title>` sections, each either bullet lines or
// paragraph(s)) into a small structured tree `AnswerText` maps onto real elements.
// --------------------------------------------------------------------------------------------

export interface AnswerParagraphBlock {
  type: "paragraph";
  text: string;
}

export interface AnswerListBlock {
  type: "list";
  items: string[];
}

export type AnswerBlock = AnswerParagraphBlock | AnswerListBlock;

export interface AnswerSection {
  /** `null` for the leading, heading-less "direct answer" section. */
  title: string | null;
  blocks: AnswerBlock[];
}

/** Section titles the backend never allows into `answer_he` any more (CR-invest.md: the
 * "### מקורות" block was removed from `format_investigation_answer_he` since `sources` is
 * already a separate, structured field the page renders on its own) -- dropped defensively here
 * too, purely for a legacy stored answer from before that backend change landed. Matched after
 * trimming, so trailing bidi-isolate/whitespace noise in a legacy heading doesn't defeat it. */
const DROPPED_SECTION_TITLES = new Set(["מקורות"]);

function splitIntoParagraphs(body: string): string[] {
  return body
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
}

/** A paragraph is a bullet list when every one of its non-empty lines starts with "- " (the
 * backend's own bullet marker for `key_facts`) -- anything else (including a mix) is treated as
 * ordinary prose, its lines joined with a space. */
function paragraphToBlock(paragraph: string): AnswerBlock {
  const lines = paragraph.split("\n").filter((l) => l.trim().length > 0);
  const isList = lines.length > 0 && lines.every((l) => l.trimStart().startsWith("- "));
  if (isList) {
    return { type: "list", items: lines.map((l) => l.trimStart().replace(/^-\s+/, "").trim()) };
  }
  return { type: "paragraph", text: lines.join(" ").trim() };
}

/**
 * Parse a raw `answer_he`-shaped string into sections. Cleans the text first (see
 * `normalizeHebrewPunctuation`/`expandGroupedCitationMarkers`), then walks it line by line,
 * starting a new section every time a `### <title>` heading line appears -- a section's body may
 * itself span several blank-line-separated paragraphs (the `context_paragraphs` case in
 * `format_investigation_answer_he`), which is why this does not simply split the whole text on
 * every blank line up front (that would sever a multi-paragraph section from its own heading).
 * A section whose title is in `DROPPED_SECTION_TITLES` is omitted entirely. Returns `[]` for
 * empty/whitespace-only input.
 */
export function parseAnswerSections(raw: string | null | undefined): AnswerSection[] {
  const cleaned = expandGroupedCitationMarkers(normalizeHebrewPunctuation(raw));
  if (!cleaned.trim()) return [];

  const lines = cleaned.split("\n");
  const rawSections: Array<{ title: string | null; bodyLines: string[] }> = [];
  let current: { title: string | null; bodyLines: string[] } = { title: null, bodyLines: [] };
  let started = false;

  for (const line of lines) {
    const heading = line.match(/^###\s+(.*)$/);
    if (heading) {
      if (started) rawSections.push(current);
      current = { title: heading[1].trim(), bodyLines: [] };
      started = true;
    } else {
      current.bodyLines.push(line);
      started = true;
    }
  }
  if (started) rawSections.push(current);

  const sections: AnswerSection[] = [];
  for (const { title, bodyLines } of rawSections) {
    if (title !== null && DROPPED_SECTION_TITLES.has(title)) continue;
    const paragraphs = splitIntoParagraphs(bodyLines.join("\n"));
    const blocks = paragraphs.map(paragraphToBlock);
    if (title === null && blocks.length === 0) continue; // nothing but blank lines before a heading
    sections.push({ title, blocks });
  }
  return sections;
}
