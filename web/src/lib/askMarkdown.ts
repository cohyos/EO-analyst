import { marked } from "marked";
import DOMPurify from "dompurify";

/**
 * The chat answer format rewrite (see `ask_answer_format.md` /
 * `services.ask_build_messages`) makes the model return real Markdown -- headings, bullet
 * lists, a blockquote-free analyst-assessment section -- instead of the previous
 * plain-text-with-asterisks blob the UI rendered verbatim (`### **מקור 1**`, `**הערת
 * איכות:**` visible as literal text, see the 2026-09-06 bug report). This module turns that
 * Markdown into sanitized, RTL-correct HTML for `dangerouslySetInnerHTML`:
 *
 * 1. `marked` parses Markdown -> HTML (GFM tables/lists included).
 * 2. `[n]` markers are turned into `.eo-citation` anchors when `n` resolves against the
 *    citations the backend streamed (same `data-item-id`/`data-url` contract
 *    `web/src/lib/reportHtml.ts` established for reports, so `AskAnswer`'s click handler can
 *    reuse that exact pattern) -- an unresolved `[n]` is left as plain text, same as
 *    `CitationText`.
 * 3. Block elements (`p`, `li`, headings, `blockquote`, `td`/`th`) get `dir="auto"` and runs of
 *    Latin/URL-ish characters inside prose get wrapped in `<bdi>` so an English model name or a
 *    URL embedded in Hebrew RTL text doesn't scramble the surrounding punctuation.
 * 4. Headings/lists/blockquotes/code/tables get the app's own Tailwind tokens (no ad-hoc
 *    "prose" plugin), and every `<table>` is wrapped in an `overflow-x-auto` div.
 * 5. The result is sanitized *twice* -- once right after `marked` (before any citation/bdi
 *    processing touches it) and once more on the final serialized markup -- because step 2/3
 *    write attribute values (`title`, `href`) sourced from citation data (item titles/URLs come
 *    from ingested OSINT content, not from this module's own fixed allowlist) straight onto DOM
 *    nodes; the second pass is what actually strips a `javascript:` URL or stray `on*` handler
 *    that DOM property assignment itself would not.
 */

export interface AskCitationLike {
  n: number;
  item_id: number | null;
  title: string;
  url: string;
}

const ALLOWED_TAGS = [
  "p",
  "br",
  "strong",
  "em",
  "b",
  "i",
  "ul",
  "ol",
  "li",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "blockquote",
  "code",
  "pre",
  "a",
  "span",
  "bdi",
  "hr",
  "table",
  "thead",
  "tbody",
  "tr",
  "th",
  "td",
  "div",
];

const ALLOWED_ATTR = ["href", "target", "rel", "class", "dir", "data-item-id", "data-url", "title"];

function sanitize(html: string): string {
  return DOMPurify.sanitize(html, { ALLOWED_TAGS, ALLOWED_ATTR });
}

// Runs of Latin letters/digits and the punctuation that typically glues a URL, model name, or
// acronym together (e.g. "XM30", "https://example.com/x-y", "GPT-4o") -- isolated with <bdi> so
// they read correctly embedded inside RTL Hebrew prose.
const LATIN_RUN_RE = /[A-Za-z0-9][A-Za-z0-9._/:@+#=?&%-]*/g;

// `[n]` citation markers, same shape CitationText/reportHtml already parse.
const CITE_RE = /\[(\d+)\]/g;

// Citation URLs come from ingested OSINT item rows, not from a fixed allowlist -- DOMPurify's
// final sanitize pass strips a `javascript:` (or similar) scheme from `href`, but it does not
// know `data-url` is meant to hold a URL our own click handler later opens via `window.open`, so
// that one needs its own scheme check before it ever reaches the DOM.
function isSafeUrl(url: string | null | undefined): url is string {
  return !!url && /^https?:\/\//i.test(url);
}

function collectTextNodes(root: Node): Text[] {
  const doc = root.ownerDocument ?? (root as Document);
  const walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes: Text[] = [];
  let cur = walker.nextNode();
  while (cur) {
    nodes.push(cur as Text);
    cur = walker.nextNode();
  }
  return nodes;
}

function linkifyCitations(doc: Document, root: HTMLElement, citationsByN: Map<number, AskCitationLike>): void {
  for (const node of collectTextNodes(root)) {
    const parent = node.parentElement;
    if (parent && (parent.tagName === "CODE" || parent.tagName === "A")) continue;
    const text = node.data;
    CITE_RE.lastIndex = 0;
    if (!CITE_RE.test(text)) continue;
    CITE_RE.lastIndex = 0;

    const frag = doc.createDocumentFragment();
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = CITE_RE.exec(text))) {
      if (m.index > last) frag.appendChild(doc.createTextNode(text.slice(last, m.index)));
      const n = Number(m[1]);
      const citation = citationsByN.get(n);
      const linkable = citation && (citation.item_id != null || isSafeUrl(citation.url));
      if (citation && linkable) {
        const a = doc.createElement("a");
        a.className =
          "eo-citation mx-0.5 inline-flex h-4 min-w-[1rem] items-center justify-center rounded " +
          "bg-accent-muted px-1 align-super text-[10px] font-mono font-semibold text-accent-fg no-underline " +
          "hover:bg-accent hover:text-accent-fg";
        a.textContent = `[${n}]`;
        if (citation.item_id != null) {
          a.setAttribute("href", `/items/${citation.item_id}`);
          a.setAttribute("data-item-id", String(citation.item_id));
        } else {
          a.setAttribute("href", citation.url);
          a.setAttribute("data-url", citation.url);
          a.setAttribute("target", "_blank");
          a.setAttribute("rel", "noopener noreferrer");
        }
        if (citation.title) a.title = citation.title;
        frag.appendChild(a);
      } else {
        frag.appendChild(doc.createTextNode(m[0]));
      }
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(doc.createTextNode(text.slice(last)));
    node.replaceWith(frag);
  }
}

function shouldSkipBidi(el: Element | null): boolean {
  let cur: Element | null = el;
  while (cur) {
    if (cur.tagName === "CODE" || cur.tagName === "PRE" || cur.classList.contains("eo-citation")) return true;
    cur = cur.parentElement;
  }
  return false;
}

function wrapBidiRuns(doc: Document, root: HTMLElement): void {
  for (const node of collectTextNodes(root)) {
    if (shouldSkipBidi(node.parentElement)) continue;
    const text = node.data;
    LATIN_RUN_RE.lastIndex = 0;
    if (!LATIN_RUN_RE.test(text)) continue;
    LATIN_RUN_RE.lastIndex = 0;

    const frag = doc.createDocumentFragment();
    let last = 0;
    let m: RegExpExecArray | null;
    while ((m = LATIN_RUN_RE.exec(text))) {
      if (m.index > last) frag.appendChild(doc.createTextNode(text.slice(last, m.index)));
      const bdi = doc.createElement("bdi");
      bdi.textContent = m[0];
      frag.appendChild(bdi);
      last = m.index + m[0].length;
    }
    if (last < text.length) frag.appendChild(doc.createTextNode(text.slice(last)));
    node.replaceWith(frag);
  }
}

const TOKEN_CLASSES: Array<[string, string]> = [
  ["h1", "mt-3 mb-1.5 text-base font-bold text-fg"],
  ["h2", "mt-3 mb-1.5 text-base font-semibold text-fg"],
  ["h3", "mt-2.5 mb-1 text-sm font-semibold text-fg"],
  ["h4", "mt-2 mb-1 text-sm font-semibold text-fg"],
  ["h5", "mt-2 mb-1 text-xs font-semibold text-fg-muted"],
  ["h6", "mt-2 mb-1 text-xs font-semibold text-fg-muted"],
  ["p", "leading-relaxed"],
  ["ul", "my-1.5 ms-4 list-disc space-y-0.5"],
  ["ol", "my-1.5 ms-4 list-decimal space-y-0.5"],
  ["li", "leading-relaxed"],
  ["blockquote", "my-1.5 border-s-2 border-border-strong ps-2 italic text-fg-muted"],
  ["code", "rounded bg-bg-sunken px-1 py-0.5 font-mono text-[0.85em]"],
  ["pre", "my-1.5 overflow-x-auto rounded-md bg-bg-sunken p-2 font-mono text-xs"],
  ["table", "w-full border-collapse text-xs"],
  ["th", "border border-border bg-bg-sunken px-2 py-1 text-start font-semibold"],
  ["td", "border border-border px-2 py-1 text-start"],
  ["hr", "my-2 border-border"],
];

function applyTokenStyling(root: HTMLElement): void {
  for (const [selector, classes] of TOKEN_CLASSES) {
    root.querySelectorAll(selector).forEach((el) => {
      for (const c of classes.split(" ")) el.classList.add(c);
    });
  }
  root.querySelectorAll("a:not(.eo-citation)").forEach((a) => {
    a.classList.add("text-accent", "underline", "hover:opacity-80");
    if (!a.getAttribute("target")) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
    }
  });
  root.querySelectorAll("pre, code").forEach((el) => el.setAttribute("dir", "ltr"));
}

function markBlockDirection(root: HTMLElement): void {
  root.querySelectorAll("p, li, h1, h2, h3, h4, h5, h6, blockquote, td, th").forEach((el) => {
    el.setAttribute("dir", "auto");
  });
}

function wrapTablesForScroll(doc: Document, root: HTMLElement): void {
  root.querySelectorAll("table").forEach((table) => {
    if (table.parentElement?.classList.contains("eo-table-wrap")) return;
    const wrap = doc.createElement("div");
    wrap.className = "eo-table-wrap overflow-x-auto";
    table.replaceWith(wrap);
    wrap.appendChild(table);
  });
}

/**
 * Markdown -> sanitized, RTL-correct, citation-linked HTML for an assistant chat message.
 * Safe to call on every streamed chunk (re-parses from scratch each time; cheap at this size).
 */
export function renderAskMarkdown(markdown: string, citations: AskCitationLike[] = []): string {
  const source = markdown ?? "";
  const rawHtml = marked.parse(source, { async: false }) as string;
  const safeHtml = sanitize(rawHtml);

  if (typeof document === "undefined") return safeHtml;

  const container = document.createElement("div");
  container.innerHTML = safeHtml;

  const citationsByN = new Map(citations.map((c) => [c.n, c]));
  linkifyCitations(document, container, citationsByN);
  markBlockDirection(container);
  wrapTablesForScroll(document, container);
  applyTokenStyling(container);
  wrapBidiRuns(document, container);

  return sanitize(container.innerHTML);
}
