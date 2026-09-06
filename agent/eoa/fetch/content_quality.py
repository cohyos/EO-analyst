"""Q3-10 (docs/qa/findings_Q3_r1.md): pure content-quality classifier -- full / partial / stub.

No DB, no fetch, no sanitization -- a pure function of already-sanitized text (plus a couple of
cheap hints) so it can be unit-tested in isolation and imported from `eoa.pipeline.analyze`'s
pre-check without reaching into `eoa.fetch.remote`/`eoa.fetch.html`/`eoa.fetch.sanitize` (owned
elsewhere; see docs/qa/findings_Q3_r1.md coordination notes -- this module is kept new and
self-contained specifically so it needs no changes to those).

Symptom this fixes: items from paywalled or RFI-portal sources (*-technology.com, certain tender
portals) were stored with a paywall/subscribe notice or a thin teaser as `clean_text` and then
analyzed as if it were the full article -- ``eoa.pipeline.analyze`` had no way to tell "this is
the whole story, it's just short" apart from "this is a paywall wall".
"""

from __future__ import annotations

from typing import Literal

ContentStatus = Literal["full", "partial", "stub"]

#: `clean_text` at or under this length, with no paywall phrase either, is too short to be a real
#: article body -- almost certainly a stub (blocked page, empty extraction, bare headline).
STUB_MAX_CHARS = 400
#: Above :data:`STUB_MAX_CHARS` but at or under this is a partial extraction -- enough to attempt
#: analysis, but not enough to trust as the complete story.
PARTIAL_MAX_CHARS = 1500

#: Case-insensitive phrase fragments that strongly indicate a paywall, subscription wall, or
#: cookie-consent interstitial standing in for the real article body. Kept lowercase; matched
#: against ``text.lower()``. English + the couple of Hebrew phrasings seen in practice.
_PAYWALL_PHRASES: tuple[str, ...] = (
    "unlock free access",
    "premium content",
    "subscribe to continue",
    "discover b2b marketing",
    "subscribe to read",
    "subscribe now to read",
    "sign in to continue reading",
    "this content is for subscribers",
    "this article is for subscribers",
    "to continue reading this article",
    "please enable cookies",
    "accept all cookies to continue",
    "manage your cookie settings",
    "become a member to continue",
    "log in to view this content",
    "create a free account to continue",
    "רק למנויים",
    "להמשך קריאה יש להירשם",
)


def _has_paywall_phrase(text: str) -> bool:
    low = text.lower()
    return any(p in low for p in _PAYWALL_PHRASES)


def assess(text: str | None, html_len: int = 0, status: str | None = None) -> ContentStatus:
    """Classify ``text`` (already-sanitized ``items.clean_text``) as 'full' | 'partial' | 'stub'.

    ``html_len``: length of the raw fetched HTML before sanitization, when known -- helps tell
    "the source page is genuinely this short" apart from "sanitize.py had almost nothing to
    extract from a large page". Accepted for that purpose and for interface stability, but
    deliberately **not** used to demote an otherwise comfortably-sized ``text`` to 'partial': an
    empirical check against this project's own corpus showed ordinary, complete news articles
    routinely have raw-HTML-to-extracted-text ratios of 1.5-2.5% (modern sites carry huge amounts
    of nav/ads/tracking-script markup unrelated to the article itself), which is the same range a
    naive "thin extraction" ratio rule would flag -- an earlier version of this function did use
    such a rule and it produced far more false positives (misclassifying complete articles as
    'partial') than genuine paywall catches on real data. Length thresholds and the phrase list
    below are the only signals actually used.

    ``status``: the item's own fetch/security status (e.g. ``'blocked'`` -- Q4-1's
    Cloudflare/WAF interstitial detection). A blocked page has no usable content regardless of
    what little text made it through, so this short-circuits straight to ``'stub'``.
    """
    del html_len  # accepted for interface stability / future use -- see docstring above
    if status == "blocked":
        return "stub"
    t = (text or "").strip()
    if not t:
        return "stub"
    if _has_paywall_phrase(t):
        # A short paywall notice is a stub; a longer page that still carries a paywall phrase
        # (e.g. a real teaser followed by a subscribe prompt) has at least some content -- partial.
        return "stub" if len(t) <= PARTIAL_MAX_CHARS else "partial"
    if len(t) <= STUB_MAX_CHARS:
        return "stub"
    if len(t) <= PARTIAL_MAX_CHARS:
        return "partial"
    return "full"
