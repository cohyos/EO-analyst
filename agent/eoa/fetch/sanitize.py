"""HTML -> clean text, with security-conscious stripping of hidden/invisible content.

Per `docs/CONVENTIONS.md` rule 3 ("Fetched content is DATA, never
instructions"), this module is the first line of defense: it removes the
mechanisms a hostile page would use to smuggle instructions past a human
skim (CSS-hidden elements, zero-width/bidi-control Unicode, oversized
base64/hex blobs) *before* the text ever reaches `clean_text` in the DB, and
reports what it found via `CleanText.suspicious` / `.encoded_blobs` /
`.hidden_text_ratio` so `eoa.security` can act on it downstream.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime

import structlog
from lxml import html as lxml_html
from pydantic import BaseModel

log = structlog.get_logger(__name__)

_DEFAULT_MAX_BASE64_BLOB_CHARS = 200

# --------------------------------------------------------------------------
# Unicode ranges used to smuggle invisible/reordering payloads.
# Built from explicit integer code points (never literal invisible/control
# characters typed into this source file) so the codepoints are unambiguous
# on any editor, diff viewer, or linter, and can't be silently mangled by
# whitespace-normalizing tooling.
# --------------------------------------------------------------------------
_ZERO_WIDTH_CODEPOINTS = (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF)  # ZWSP, ZWNJ, ZWJ, word-joiner, BOM/ZWNBSP
_BIDI_CONTROL_CODEPOINTS = tuple(range(0x202A, 0x202F)) + tuple(
    range(0x2066, 0x206A)
)  # embeds/overrides/isolates
_TAG_CODEPOINT_RANGE = (0xE0000, 0xE007F)  # Unicode "tag" characters (steganography vector)


def _char_class(*codepoints: int) -> str:
    return "[" + "".join(re.escape(chr(cp)) for cp in codepoints) + "]"


def _range_class(low: int, high: int) -> str:
    return f"[{re.escape(chr(low))}-{re.escape(chr(high))}]"


_ZERO_WIDTH_RE = re.compile(_char_class(*_ZERO_WIDTH_CODEPOINTS))
_BIDI_CONTROL_RE = re.compile(_char_class(*_BIDI_CONTROL_CODEPOINTS))
_TAG_CHARS_RE = re.compile(_range_class(*_TAG_CODEPOINT_RANGE))

_HIDDEN_STYLE_RE = re.compile(
    r"(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:\.0*)?(?:px|em|%)?\b"
    r"|opacity\s*:\s*0(?:\.0+)?\b)",
    re.IGNORECASE,
)
_OFFSCREEN_STYLE_RE = re.compile(
    r"(?:left|top|text-indent)\s*:\s*-\d{3,}\s*px",
    re.IGNORECASE,
)

_BASE64_RE = re.compile(r"(?:[A-Za-z0-9+/]{4}){10,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?")
_HEX_BLOB_RE = re.compile(r"(?:[0-9a-fA-F]{2}[ \t]?){20,}")

_STRIP_TAGS = (
    "script",
    "style",
    "noscript",
    "iframe",
    # Non-content containers (finding #20): none of these hold real,
    # human-readable page text, but a hidden instruction can be smuggled
    # inside one (an off-screen <svg><text>, an unrendered <template>, a
    # <canvas> fallback body, an <object>/<embed> alt payload).
    "template",
    "svg",
    "object",
    "embed",
    "canvas",
    "math",
)


class CleanText(BaseModel):
    """Sanitized article text plus what was found/removed along the way."""

    text: str
    title: str | None = None
    detect_text: str = ""
    lang: str | None = None
    published_at: datetime | None = None
    hidden_text_ratio: float = 0.0
    encoded_blobs: list[str] = []
    suspicious: list[str] = []


# --------------------------------------------------------------------------
# whitespace / hashing
# --------------------------------------------------------------------------


def _normalize_whitespace(text: str) -> str:
    """Collapse runs of spaces/tabs, trim lines, keep single blank lines as paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    space_run_re = re.compile("[ " + chr(0x09) + chr(0xA0) + "]+")
    lines = [space_run_re.sub(" ", line).strip() for line in text.split("\n")]

    out_lines: list[str] = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 1:
                out_lines.append("")
        else:
            blank_run = 0
            out_lines.append(line)

    return "\n".join(out_lines).strip()


def text_hash(text: str) -> str:
    """SHA-256 of the whitespace-normalized text (stable across re-fetches with cosmetic diffs)."""
    normalized = _normalize_whitespace(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# mojibake repair (runs on extracted text, right after extraction)
# --------------------------------------------------------------------------

# Single-byte encodings a mis-decoding upstream commonly went through --
# i.e. the *wrong* codec that was applied to what were really UTF-8 bytes,
# producing garbled text like "×¢×‘×¨×™×ª" (Hebrew mis-decoded as
# latin-1) or "â€™" (a smart quote mis-decoded as cp1252/windows-1252).
# Tried in this order (cp1252 first: it's the more common real-world
# culprit and is a strict superset of latin-1's printable range).
_MOJIBAKE_INTERMEDIATE_ENCODINGS = ("cp1252", "latin-1")
_REPLACEMENT_CHAR = "�"  # U+FFFD, left behind by a genuinely lossy decode

# "â€" and "Ã" followed by another Latin-1-Supplement character are near-unique
# fingerprints of UTF-8 bytes mis-decoded as cp1252/latin-1 -- e.g. a UTF-8
# right single quote (bytes 0xE2 0x80 0x99) mis-decoded that way reads as
# "â€™", and a UTF-8-encoded accented Latin letter reads as "Ã<something>".
# Legitimate text essentially never contains these sequences, so counting
# them (unlike a plain letter tally) also catches mojibake that doesn't
# change the Hebrew/Latin *letter* count at all -- an all-ASCII sentence
# whose only casualty was a smart quote or dash.
_MOJIBAKE_MARKER_RE = re.compile("(?:â€|Ã[-ÿ])")


def _mojibake_score(text: str) -> int:
    """Single comparable score used to judge whether a repair candidate is an improvement.

    Rewards Hebrew and Latin letters (the alphabets this project's content actually uses); penalizes
    `U+FFFD` replacement characters (a genuinely lossy decode) and `_MOJIBAKE_MARKER_RE` hits (the
    "â€.../Ã." double-encoding fingerprint) heavily enough that eliminating either always outweighs a
    tie or small loss in letter count.
    """
    low, high = _HEBREW_CODEPOINT_RANGE
    good = sum(1 for c in text if (low <= ord(c) <= high) or (c.isascii() and c.isalpha()))
    bad = text.count(_REPLACEMENT_CHAR) + len(_MOJIBAKE_MARKER_RE.findall(text))
    return good - 5 * bad


def _repair_mojibake(text: str) -> str:
    """Undo UTF-8-decoded-as-a-wrong-single-byte-encoding mojibake, once or twice (double-encoding).

    Uses `ftfy` if it is installed (not currently a project dependency); otherwise falls back to a
    small heuristic: re-interpret the text as bytes under each of `_MOJIBAKE_INTERMEDIATE_ENCODINGS`
    and re-decode as UTF-8 -- repeating once more to catch double-encoding -- keeping a candidate only
    when it strictly improves `_mojibake_score` over the current best. Genuinely correct text has no
    such improving round-trip (real Hebrew/non-Latin-1 characters simply fail to `.encode()` under
    these codecs) and is returned unchanged.
    """
    if not text:
        return text

    try:
        import ftfy

        return ftfy.fix_text(text)
    except ImportError:
        pass

    best = text
    best_score = _mojibake_score(best)
    for encoding in _MOJIBAKE_INTERMEDIATE_ENCODINGS:
        candidate = text
        for _ in range(2):  # once for single mis-decoding, twice for double-encoding
            try:
                candidate = candidate.encode(encoding).decode("utf-8")
            except (UnicodeDecodeError, UnicodeEncodeError):
                break
            score = _mojibake_score(candidate)
            if score > best_score:
                best, best_score = candidate, score
    return best


# --------------------------------------------------------------------------
# language detection
# --------------------------------------------------------------------------

_HEBREW_CODEPOINT_RANGE = (0x0590, 0x05FF)


def detect_lang(text: str) -> str | None:
    """Detect language; Hebrew is checked via a Unicode-range heuristic before falling back to langdetect.

    langdetect is unreliable on short Hebrew snippets mixed with Latin
    technical terms (a common shape for these sources), so a simple
    character-ratio heuristic is checked first.
    """
    stripped = text.strip()
    if not stripped:
        return None

    low, high = _HEBREW_CODEPOINT_RANGE
    hebrew_chars = sum(1 for c in stripped if low <= ord(c) <= high)
    letters = sum(1 for c in stripped if c.isalpha())
    if letters and hebrew_chars / letters > 0.3:
        return "he"

    try:
        from langdetect import LangDetectException, detect

        try:
            return detect(stripped)
        except LangDetectException:
            return None
    except ImportError:  # pragma: no cover - langdetect is a hard dependency in pyproject.toml
        return None


# --------------------------------------------------------------------------
# hidden-element detection/removal (runs on the DOM, before text extraction)
# --------------------------------------------------------------------------


def _is_hidden_element(el: lxml_html.HtmlElement) -> bool:
    style = (el.get("style") or "").lower()
    if _HIDDEN_STYLE_RE.search(style):
        return True
    if "position" in style and "absolute" in style and _OFFSCREEN_STYLE_RE.search(style):
        return True
    if el.get("hidden") is not None:
        return True
    return (el.get("aria-hidden") or "").strip().lower() == "true"


def _strip_dom(html_str: str) -> tuple[str, float, bool]:
    """Remove script/style/iframe/noscript/comments/on*-attrs and CSS-hidden subtrees.

    Returns (cleaned_html, hidden_text_ratio, found_hidden). The ratio is
    computed over *text length*, hidden vs. visible, on the original DOM
    before hidden subtrees are dropped.
    """
    try:
        tree = lxml_html.fromstring(html_str)
    except Exception as exc:
        log.debug("fetch.sanitize_parse_failed", error=repr(exc))
        return html_str, 0.0, False

    # Drop HTML comments outright (never counted as text, but a common
    # injection-hiding spot).
    for comment in tree.xpath("//comment()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)

    # Drop tags whose content is never real page text.
    for tag in _STRIP_TAGS:
        for el in tree.xpath(f"//{tag}"):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    # Strip on*="" event-handler attributes everywhere.
    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in [a for a in el.attrib if a.lower().startswith("on")]:
            del el.attrib[attr]

    # A hidden (or non-content-tag) root has no parent to remove it from,
    # so the walk()+remove() pass below can never drop it -- its text would
    # otherwise remain fully extractable despite being invisible/inert to a
    # human reader (finding #20). Treat the whole document as maximally
    # hidden and return an empty body instead.
    if not isinstance(tree.tag, str) or tree.tag in _STRIP_TAGS or _is_hidden_element(tree):
        root_text_len = len((tree.text_content() or "").strip())
        if root_text_len:
            log.info("fetch.hidden_root_document", root_tag=tree.tag, text_len=root_text_len)
        return "", (1.0 if root_text_len else 0.0), root_text_len > 0

    hidden_len = 0
    visible_len = 0
    found_hidden = False
    hidden_roots: list[lxml_html.HtmlElement] = []

    def walk(el: lxml_html.HtmlElement) -> None:
        nonlocal hidden_len, visible_len, found_hidden
        if not isinstance(el.tag, str):
            return
        if _is_hidden_element(el):
            content = el.text_content() or ""
            if content.strip():
                hidden_len += len(content)
                found_hidden = True
            hidden_roots.append(el)
            return  # don't descend: already counted as hidden, will be removed
        visible_len += len(el.text or "")
        for child in el:
            walk(child)
            visible_len += len(child.tail or "")

    walk(tree)

    for el in hidden_roots:
        parent = el.getparent()
        if parent is not None:
            # Preserve tail text (text after the closing tag belongs to the
            # parent's flow, not to the hidden subtree).
            tail = el.tail
            if tail:
                previous = el.getprevious()
                if previous is not None:
                    previous.tail = (previous.tail or "") + tail
                else:
                    parent.text = (parent.text or "") + tail
            parent.remove(el)

    total = hidden_len + visible_len
    ratio = (hidden_len / total) if total else 0.0

    try:
        cleaned_html = lxml_html.tostring(tree, encoding="unicode")
    except Exception:
        cleaned_html = html_str

    return cleaned_html, ratio, found_hidden


# --------------------------------------------------------------------------
# invisible-unicode + homoglyph stripping (runs on extracted plain text)
# --------------------------------------------------------------------------


def _strip_invisible_unicode(text: str) -> tuple[str, list[str]]:
    flags: list[str] = []
    if _ZERO_WIDTH_RE.search(text):
        flags.append("zero_width_chars")
    if _BIDI_CONTROL_RE.search(text):
        flags.append("bidi_control_chars")
    if _TAG_CHARS_RE.search(text):
        flags.append("unicode_tag_chars")

    cleaned = _ZERO_WIDTH_RE.sub("", text)
    cleaned = _BIDI_CONTROL_RE.sub("", cleaned)
    cleaned = _TAG_CHARS_RE.sub("", cleaned)
    return cleaned, flags


# Cyrillic/Greek code points that are visually identical (or
# near-identical) to a Latin letter -- the classic homoglyph-substitution
# alphabet, e.g. Cyrillic 'а' (U+0430) spoofing Latin 'a'. Not exhaustive:
# covers the characters commonly used in phishing/evasion, drawn from
# Unicode's confusables.txt for the Cyrillic block. Used ONLY to build a
# detection copy -- never to alter what a human reads (finding #21).
_CONFUSABLES: dict[int, str] = {
    0x0430: "a",
    0x0410: "A",
    0x0435: "e",
    0x0415: "E",
    0x043E: "o",
    0x041E: "O",
    0x0440: "p",
    0x0420: "P",
    0x0441: "c",
    0x0421: "C",
    0x0443: "y",
    0x0423: "Y",
    0x0445: "x",
    0x0425: "X",
    0x0432: "b",
    0x043D: "h",
    0x0442: "t",
    0x043A: "k",
    0x043C: "m",
    0x0405: "S",
    0x0408: "J",
    0x0406: "I",
    0x0456: "i",
    0x04CF: "l",
    0x0455: "s",
    0x0458: "j",
}
_CONFUSABLES_RE = re.compile("[" + "".join(re.escape(chr(cp)) for cp in _CONFUSABLES) + "]")


def _map_confusables(text: str) -> tuple[str, bool]:
    """Replace homoglyphs with their Latin look-alike -- never deletes.

    Returns ``(mapped_text, changed)``. Used only to build the *detection*
    copy (`CleanText.detect_text`); the caller's own readable text is never
    passed through this. Fixes finding #21: the previous implementation
    *deleted* minority-script characters from the text a human/report
    actually reads (turning "іgnore" into "gnore"), which both corrupted
    legitimate mixed-script names and, since the leftover "gnore" no longer
    matched the "ignore" heuristic pattern, made evasion easier rather than
    harder.
    """
    changed = False

    def repl(match: re.Match[str]) -> str:
        nonlocal changed
        changed = True
        return _CONFUSABLES[ord(match.group(0))]

    mapped = _CONFUSABLES_RE.sub(repl, text) if text else text
    return mapped, changed


# --------------------------------------------------------------------------
# encoded-blob detection/removal
# --------------------------------------------------------------------------


def _extract_encoded_blobs(text: str, max_chars: int) -> tuple[str, list[str]]:
    blobs: list[str] = []

    def make_repl():
        def repl(match: re.Match[str]) -> str:
            blob = match.group(0)
            if len(blob) > max_chars:
                preview = blob[:80]
                blobs.append(f"{preview}...<{len(blob)} chars total>")
                return ""
            return blob

        return repl

    cleaned = _BASE64_RE.sub(make_repl(), text)
    cleaned = _HEX_BLOB_RE.sub(make_repl(), cleaned)
    return cleaned, blobs


# --------------------------------------------------------------------------
# date parsing
# --------------------------------------------------------------------------


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        from dateutil import parser as date_parser

        dt = date_parser.parse(date_str)
    except (ValueError, OverflowError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# --------------------------------------------------------------------------
# extraction backends
# --------------------------------------------------------------------------


def _extract_with_trafilatura(cleaned_html: str, url: str) -> tuple[str | None, str | None, datetime | None]:
    try:
        import trafilatura
    except ImportError:  # pragma: no cover - trafilatura is a hard dependency in pyproject.toml
        return None, None, None

    try:
        result = trafilatura.extract(
            cleaned_html,
            url=url,
            output_format="json",
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
    except Exception as exc:
        log.debug("fetch.trafilatura_failed", url=url, error=repr(exc))
        return None, None, None

    if not result:
        return None, None, None

    try:
        data = json.loads(result)
    except json.JSONDecodeError:
        return None, None, None

    return data.get("text"), data.get("title"), _parse_date(data.get("date"))


def _extract_with_readability(cleaned_html: str) -> tuple[str | None, str | None]:
    try:
        from readability import Document
    except ImportError:
        return None, None

    try:
        doc = Document(cleaned_html)
        title = doc.title()
        content_tree = lxml_html.fromstring(doc.summary())
        text = content_tree.text_content()
    except Exception as exc:
        log.debug("fetch.readability_failed", error=repr(exc))
        return None, None
    return text, title


def _extract_with_lxml(cleaned_html: str) -> tuple[str | None, str | None]:
    try:
        tree = lxml_html.fromstring(cleaned_html)
    except Exception as exc:
        log.debug("fetch.lxml_fallback_failed", error=repr(exc))
        return None, None
    text = tree.text_content()
    title_el = tree.find(".//title")
    title = title_el.text if title_el is not None else None
    return text, title


# --------------------------------------------------------------------------
# public entry point
# --------------------------------------------------------------------------


def _max_base64_blob_chars() -> int:
    try:
        from eoa.config import settings

        return int(settings().security.max_base64_blob_chars)
    except Exception:
        return _DEFAULT_MAX_BASE64_BLOB_CHARS


def extract_clean_text(html: str, url: str) -> CleanText:
    """Turn raw article HTML into sanitized, provenance-safe text.

    Pipeline: strip script/style/iframe/noscript/non-content-container tags,
    comments/on*-attrs, and CSS-hidden subtrees -- returning an empty
    document outright if the root itself is hidden (computing
    `hidden_text_ratio` along the way) -> extract article text+title+date
    via trafilatura, falling back to readability-lxml, falling back to a
    bare lxml `text_content()` -> repair UTF-8-mis-decoded-as-latin-1/cp1252
    mojibake in both title and body (`_repair_mojibake`) -> strip
    invisible/bidi-control Unicode from BOTH title and body (never deleted:
    display text) -> build a separate confusable-mapped `detect_text`
    detection copy (title+body) -> strip oversized base64/hex blobs from the
    body -> normalize whitespace -> detect language.
    """
    suspicious: list[str] = []

    cleaned_html, hidden_ratio, found_hidden = _strip_dom(html)
    if found_hidden:
        suspicious.append(f"hidden_text_ratio={hidden_ratio:.3f}")

    body_text, title, published_at = _extract_with_trafilatura(cleaned_html, url)

    if not body_text or not body_text.strip():
        body_text, fallback_title = _extract_with_readability(cleaned_html)
        title = title or fallback_title

    if not body_text or not body_text.strip():
        body_text, fallback_title = _extract_with_lxml(cleaned_html)
        title = title or fallback_title

    body_text = body_text or ""
    title_text = title.strip() if isinstance(title, str) else (title or "")

    # Repair UTF-8-mis-decoded-as-latin-1/cp1252 mojibake before anything
    # else touches the text: `eoa.fetch.html._decode` already prefers the
    # right codec whenever it has a signal to go on, but a page with no
    # HTTP charset, no declared charset, and content `charset_normalizer`
    # itself gets wrong still reaches here garbled -- and language
    # detection / homoglyph mapping below would otherwise run on the
    # garbled text instead of the real one.
    body_text = _repair_mojibake(body_text)
    title_text = _repair_mojibake(title_text) if title_text else title_text

    # Invisible/bidi-control/tag-character Unicode is never legitimately
    # visible, so stripping it can't corrupt real content -- apply it to
    # BOTH title and body (finding #21: previously this only ran on the
    # body, so the same attack smuggled via the <title> reached the DB
    # `title` column untouched).
    body_text, body_unicode_flags = _strip_invisible_unicode(body_text)
    suspicious.extend(body_unicode_flags)
    if title_text:
        title_text, title_unicode_flags = _strip_invisible_unicode(title_text)
        suspicious.extend(title_unicode_flags)

    # Homoglyph substitution is no longer *deleted* from the readable text
    # (see `_map_confusables`'s docstring for why that was actively
    # counterproductive). Instead build a separate confusable-mapped
    # detection copy that heuristics/guard additionally scan
    # (`eoa.security.guard.screen`'s `detect_text` param) without ever
    # touching what a human or the report ultimately reads.
    detect_body, body_had_confusables = _map_confusables(body_text)
    detect_title, title_had_confusables = _map_confusables(title_text)
    if body_had_confusables or title_had_confusables:
        suspicious.append("mixed_script_homoglyphs")
    detect_text = f"{detect_title}\n{detect_body}" if detect_title else detect_body

    max_blob_chars = _max_base64_blob_chars()
    body_text, blobs = _extract_encoded_blobs(body_text, max_blob_chars)

    body_text = _normalize_whitespace(body_text)
    title_text = _normalize_whitespace(title_text) if title_text else title_text
    detect_text = _normalize_whitespace(detect_text)

    lang = detect_lang(body_text) if body_text else None

    return CleanText(
        text=body_text,
        title=title_text or None,
        detect_text=detect_text,
        lang=lang,
        published_at=published_at,
        hidden_text_ratio=hidden_ratio,
        encoded_blobs=blobs,
        suspicious=suspicious,
    )
