#!/usr/bin/env python
"""R10-cleanup (round-10 QA loop, 2026-09-07): "clean old corrupted records out of the system" --
nine independent, idempotent cleanup categories over stale/corrupted rows left by the stale-worker
ImportError incident, the U9 bot-challenge/cookie-wall finding, dedup-chain drift, orphaned events,
duplicate report-QA attempts, stale tender candidates, a dead watchlist, and two on-disk directories
(search cache, superseded report files). Every category is dry-run by default (pass ``--apply`` to
actually write) -- same convention as ``scripts/repair_round9.py``/``scripts/purge_stale_tenders.py``:
print either way, so a dry run's report is directly comparable to the applied run's own counts.

Scope discipline (explicit, per the cleanup brief): this script only ever touches
``jobs``, ``investigation_log``, ``items`` (a narrow slice: ``security_status``/``clean_text``/
``summary_he`` on already-fetched rows, never new content), ``events``, ``reports`` (+ its
CASCADE-linked ``feedback_surveys``), ``tenders`` (+ its CASCADE-linked ``tender_feedback``),
``indicator_watchlist``, and two filesystem directories (``runtime/cache/search``,
``output/reports``). It never touches ``entities``/``graph_edges`` (owned by a separate in-flight
package this round) or any file under ``agent/``/``web/`` (other engineers are editing those live).

Categories (see each function's own docstring for the exact rule + any judgment call made where
the brief's literal wording didn't match this DB's actual schema/data):

  1. :func:`cleanup_stale_jobs` -- the stale-worker ``FallbackSynthesisOut`` ImportError incident:
     failed ``deep_search`` jobs get a "superseded by rerun" note where a rerun exists; their
     ``investigation_log`` rows get deleted when the job logged zero page reads and produced no
     answer (true for every job in this incident -- the crash happened before any real fetch).
  2. :func:`cleanup_interstitial_items` -- items whose stored ``clean_text`` is actually a bot-
     challenge/cookie-wall/consent interstitial, not the real article (U9,
     docs/REVIEW_2026-09-05.md, docs/qa/findings_Q4_r1.md finding Q4-1).
  3. :func:`cleanup_dedup_chains` -- ``items.dedup_of`` chains longer than one hop collapsed to
     point directly at the true root; a ``dedup_of`` pointing at a missing item cleared to NULL.
  4. :func:`cleanup_events` -- orphaned/out-of-scope/exact-duplicate ``events`` rows (round-6 rule
     re-applied; a new instance appeared today).
  5. :func:`cleanup_reports` -- ``reports`` rows whose file is gone from disk, and superseded
     failed-QA attempts once a later run of the *same* report passed QA.
  6. :func:`cleanup_tenders` -- stale ``status='unknown'`` candidates that now fail the live
     relevance gate, archived (not deleted); exact URL duplicates collapsed to the lowest id.
  7. :func:`cleanup_watchlist` -- dead ``indicator_watchlist`` rows (old + dropped, or textless).
  8. :func:`purge_search_cache` -- ``runtime/cache/search/*`` files older than 14 days, deleted.
  9. :func:`archive_orphan_report_files` -- ``output/reports/*`` files no ``reports`` row
     references, moved (not deleted) to ``output/reports/_archive/``.

Judgment calls made against the brief (documented here since the brief's own wording assumed
values this DB's schema/data didn't actually have -- see ``docs/qa/loop/round_10_fixes.md``'s
"### R10-cleanup status" section for the live dry-run numbers that confirmed each one):

  * ``jobs_state_check`` does not allow ``'superseded'`` (only queued/running/done/failed/
    deferred/partial) -- category 1 uses the brief's own documented fallback: leave ``state``
    alone, set ``error = '[superseded by rerun <id>]'`` only where a rerun's
    ``payload->>'rerun_of_job_id'`` actually points back at the failed job.
  * ``items_security_status_check`` does not allow ``'fetch_failed'`` (only clean/flagged/
    quarantined/blocked) -- category 2 uses ``'blocked'``, the exact value
    ``eoa.fetch.sanitize.detect_block_page``/``eoa.fetch.service`` already stamp on a freshly-
    fetched interstitial page at ingest time; every pipeline stage already treats it as "skip,
    don't retrigger" (``eoa.pipeline.classify``/``dedup``/``triage``), matching the brief's own
    "drop out of retrieval and reports, leave for the fetcher to retry" intent exactly.
  * The brief lists "< 400 chars body" as one signal of an interstitial row. A live check found
    that signal ALONE (no phrase match) hits 57 ``security_status='clean'`` rows -- almost all of
    them genuinely short but real content (TED/RFI notice teasers, encyclopedia/NASA snippet
    extracts, one already-promoted ``level='red'`` item). Using it as a standalone trigger would
    have nulled out real reported content. Category 2 instead requires an actual known-boilerplate
    phrase match (Cloudflare/WAF markers, cookie-consent phrasing, or the empirically-confirmed
    ``israelhayom.co.il`` nav-shell fingerprint -- 19/20 of that source's stored items turned out
    to be nothing but its cookie-gated site chrome, not the article) with a generous length cap
    (a phrase match past 2000 chars is far more likely a real article that happens to mention
    cookies -- confirmed live: ``en.globes.co.il``'s own privacy-policy article, 16.7k chars,
    would otherwise have been wrongly nulled).
  * Category 5's brief groups report retention by ``(kind, period_end, territory)``, but a live
    check found ``patent_survey`` reports share that exact tuple across genuinely different
    survey topics (``patent_surveys.topic``, joined via ``report_id`` -- e.g. 17 passed rows for
    one ``(patent_survey, 2026-09-06, NULL)`` tuple spanning 3 distinct topics). The grouping key
    adds ``patent_surveys.topic`` for that kind to avoid pairing a failed run of topic A against a
    passed run of topic B. Separately, a live check found every candidate-for-deletion row in this
    DB shares its exact ``path_md``/``path_html``/``path_docx`` with the group's surviving
    (kept) row -- same-day reruns overwrite one date-named file in place rather than writing a new
    one -- so file deletion is additionally guarded: a resolved path is only unlinked if no
    surviving ``reports`` row (in ANY group, not just this one) still references it.

Usage (native Windows, same convention as every other scripts/repair_*.py):

    set -a; . runtime/eoa.env; set +a
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/cleanup_round10.py <category> [--apply]

``<category>`` is one of: jobs, interstitial, dedup, events, reports, tenders, watchlist, cache,
orphan-files, all. Omit ``--apply`` for a dry run (the default) -- always dry-run first, review the
printed inventory, then re-run the exact same command with ``--apply``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path
from typing import Any

# Windows consoles default stdout to cp1252, which cannot encode the Hebrew title/text this script
# prints -- reconfigure to UTF-8 (same fix as scripts/repair_tenders.py, scripts/purge_stale_tenders.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Allow running as `python scripts/cleanup_round10.py` without having to set PYTHONPATH=agent first
# (mirrors scripts/purge_stale_tenders.py's own convenience fallback). The repo root is also added
# so `scripts.purge_stale_tenders` (category 6 reuses its `fails_gate`) resolves as a package.
_REPO_ROOT_DIR = Path(__file__).resolve().parent.parent
_AGENT_DIR = _REPO_ROOT_DIR / "agent"
for _extra_path in (_AGENT_DIR, _REPO_ROOT_DIR):
    if str(_extra_path) not in sys.path:
        sys.path.insert(0, str(_extra_path))

# purge_stale_tenders' own `fails_gate` IS the "current relevance pre-filter" the brief's category
# 6 asks for ("eoa.tenders.scan gate; import it read-only") -- it already wraps
# eoa.tenders.scan's own gate constants/helpers into one pure, testable function. Reused here
# rather than re-implemented, same "import it read-only" spirit purge_stale_tenders itself follows
# toward eoa.tenders.scan.
from scripts.purge_stale_tenders import fails_gate  # noqa: E402

from eoa import db  # noqa: E402
from eoa.api.services import _FETCH_FAILURE_MARKERS, _resolve_repo_path  # noqa: E402
from eoa.config import REPO_ROOT  # noqa: E402
from eoa.fetch.sanitize import _BLOCK_PAGE_PHRASES  # noqa: E402
from eoa.tenders.scan import load_deny_domains, load_procurement_signals, load_tender_sources  # noqa: E402

# ---------------------------------------------------------------------------------------------
# Category 1: stale-worker ImportError incident (jobs + investigation_log)
# ---------------------------------------------------------------------------------------------

#: The exact stale-worker incident error text (docs brief). Matched broadly (ImportError/
#: TypeError substrings) so a future recurrence of the same class of crash is caught too, not just
#: this literal message.
_IMPORT_ERROR_MARKERS = ("cannot import name", "ImportError", "TypeError")


def superseded_error_note(rerun_job_id: int) -> str:
    """The literal fallback text the brief specifies for a jobs row whose ``state`` CHECK
    constraint has no ``'superseded'`` value (verified live: only queued/running/done/failed/
    deferred/partial are allowed)."""
    return f"[superseded by rerun {rerun_job_id}]"


def investigation_log_row_is_disposable(pages_read_sum: int | None, has_answer: bool) -> bool:
    """A job's ``investigation_log`` rows are disposable (safe to delete) iff the job logged zero
    real page reads AND produced no answer -- pure log noise from a run that got nothing, not a
    genuine (if unsuccessful) investigation trail worth keeping for audit."""
    return not has_answer and (pages_read_sum or 0) == 0


def cleanup_stale_jobs(*, apply: bool = False) -> dict[str, Any]:
    """Category 1. Finds every ``deep_search`` job that failed today with an ImportError/TypeError
    class error (the stale-worker ``FallbackSynthesisOut`` incident, jobs 131-140 in the live DB --
    matched by error text + kind + state, not hardcoded ids, so a future recurrence is caught the
    same way). For each: if a rerun exists (``payload->>'rerun_of_job_id'`` on some other job
    points back at it), its ``error`` is overwritten with :func:`superseded_error_note` -- ``state``
    is deliberately left as ``'failed'`` (the CHECK constraint has no ``'superseded'`` value).
    Independently, every one of these jobs' ``investigation_log`` rows are deleted if
    :func:`investigation_log_row_is_disposable` says so (checked per-job, not assumed)."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, error, result IS NOT NULL AS has_result
            FROM jobs
            WHERE kind = 'deep_search' AND state = 'failed'
              AND created_at::date = CURRENT_DATE
              AND (error ILIKE '%%cannot import name%%' OR error ILIKE '%%ImportError%%'
                   OR error ILIKE '%%TypeError%%')
            ORDER BY id
            """
        )
        failed_jobs = cur.fetchall()
        failed_ids = [r["id"] for r in failed_jobs]

        reruns_by_origin: dict[int, int] = {}
        if failed_ids:
            cur.execute(
                """
                SELECT id, (payload->>'rerun_of_job_id')::bigint AS origin_id
                FROM jobs
                WHERE payload ? 'rerun_of_job_id'
                  AND (payload->>'rerun_of_job_id')::bigint = ANY(%(ids)s)
                ORDER BY id
                """,
                {"ids": failed_ids},
            )
            for row in cur.fetchall():
                # First (lowest-id) rerun wins if more than one exists for the same origin.
                reruns_by_origin.setdefault(row["origin_id"], row["id"])

        superseded: list[dict[str, Any]] = []
        for job in failed_jobs:
            rerun_id = reruns_by_origin.get(job["id"])
            if rerun_id is None:
                continue
            note = superseded_error_note(rerun_id)
            superseded.append({"job_id": job["id"], "rerun_job_id": rerun_id, "new_error": note})
            if apply:
                cur.execute("UPDATE jobs SET error = %(err)s WHERE id = %(id)s", {"err": note, "id": job["id"]})

        log_rows_deleted = 0
        disposable_job_ids: list[int] = []
        if failed_ids:
            cur.execute(
                "SELECT job_id, sum(COALESCE(pages_read, 0)) AS reads, count(*) AS n "
                "FROM investigation_log WHERE job_id = ANY(%(ids)s) GROUP BY job_id",
                {"ids": failed_ids},
            )
            reads_by_job = {r["job_id"]: r["reads"] for r in cur.fetchall()}
            has_answer_by_job = {j["id"]: bool(j["has_result"]) for j in failed_jobs}
            for job_id in failed_ids:
                if job_id not in reads_by_job:
                    continue  # no investigation_log rows to begin with -- nothing to delete
                if investigation_log_row_is_disposable(reads_by_job[job_id], has_answer_by_job[job_id]):
                    disposable_job_ids.append(job_id)
            if disposable_job_ids and apply:
                cur.execute(
                    "DELETE FROM investigation_log WHERE job_id = ANY(%(ids)s)", {"ids": disposable_job_ids}
                )
                log_rows_deleted = cur.rowcount
            elif disposable_job_ids:
                cur.execute(
                    "SELECT count(*) AS n FROM investigation_log WHERE job_id = ANY(%(ids)s)",
                    {"ids": disposable_job_ids},
                )
                log_rows_deleted = cur.fetchone()["n"]

    return {
        "failed_job_ids": failed_ids,
        "superseded": superseded,
        "disposable_log_job_ids": disposable_job_ids,
        "investigation_log_rows_deleted": log_rows_deleted,
        "apply": apply,
    }


# ---------------------------------------------------------------------------------------------
# Category 2: bot-challenge / consent-interstitial items
# ---------------------------------------------------------------------------------------------

#: Cookie-consent-wall phrasing (English + Hebrew) -- not covered by _FETCH_FAILURE_MARKERS or
#: _BLOCK_PAGE_PHRASES (those are bot/WAF-challenge specific), but explicitly named in the brief
#: ("cookie walls").
COOKIE_CONSENT_PHRASES = (
    "we use cookies",
    "this site uses cookies",
    "this website uses cookies",
    "accept all cookies",
    "cookie policy",
    "manage your cookie preferences",
    "use of cookies",
    "אנו משתמשים בעוגיות",
    "מדיניות עוגיות",
    "שימוש בעוגיות",
)

#: Empirically-confirmed (live DB, 2026-09-07) site-chrome fingerprint: 19 of 20 stored
#: israelhayom.co.il items turned out to be nothing but this nav bar/menu text -- the real article
#: never made it past that site's cookie/consent gate. A stable, low-false-positive-risk string
#: (the site's own "we're hiring" nav link) rather than a generic length heuristic.
NAV_SHELL_SIGNATURES = ("אנחנו מגייסים",)

#: A phrase match past this many characters is far more likely a real article that happens to
#: mention cookies/security than an actual interstitial (confirmed live: en.globes.co.il's own
#: 16.7k-char privacy-policy article would otherwise match "use of cookies").
_INTERSTITIAL_MAX_CHARS = 2000

_INTERSTITIAL_PHRASES = tuple(
    sorted(set(_BLOCK_PAGE_PHRASES) | set(_FETCH_FAILURE_MARKERS) | set(COOKIE_CONSENT_PHRASES) | set(NAV_SHELL_SIGNATURES))
)


def interstitial_match(title: str | None, clean_text: str | None) -> str | None:
    """Returns the matched boilerplate phrase if ``title``/``clean_text`` looks like a bot-
    challenge/cookie-wall/consent interstitial rather than real fetched content, else ``None``.
    Pure function of the text (no DB), so directly unit-testable."""
    text = clean_text or ""
    if len(text.strip()) > _INTERSTITIAL_MAX_CHARS:
        return None
    haystack = f"{title or ''} {text}".lower()
    for phrase in _INTERSTITIAL_PHRASES:
        if phrase in haystack:
            return phrase
    return None


def cleanup_interstitial_items(*, apply: bool = False) -> dict[str, Any]:
    """Category 2, two independent sub-actions:

    (a) Every ``security_status='clean'`` item whose title/clean_text matches
        :func:`interstitial_match` gets ``security_status='blocked'``, ``clean_text=NULL``,
        ``summary_he=NULL`` -- drops it out of every retrieval/report query (all of which already
        filter ``security_status='clean'``) without destroying the URL record itself, and leaves
        it eligible for the fetcher to retry on its next pass.
    (b) Items with NULL/empty ``title`` AND NULL/empty ``clean_text``, older than 2 days, are
        deleted outright IF nothing still references them -- checked against ``events``,
        ``investigation_log`` (via ``trigger_item``), ``item_corroboration``, and any other item's
        ``dedup_of`` pointer (reports' own references are file-path based, not item-id based, so
        not applicable here)."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, url, title, clean_text, level FROM items WHERE security_status = 'clean' ORDER BY id"
        )
        rows = cur.fetchall()

    interstitial_rows: list[dict[str, Any]] = []
    for row in rows:
        match = interstitial_match(row.get("title"), row.get("clean_text"))
        if match:
            interstitial_rows.append(
                {
                    "id": row["id"],
                    "url_host": _url_host(row.get("url")),
                    "level": row.get("level"),
                    "matched_phrase": match,
                }
            )

    if interstitial_rows and apply:
        ids = [r["id"] for r in interstitial_rows]
        with db.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE items SET security_status = 'blocked', clean_text = NULL, summary_he = NULL, "
                "updated_at = now() WHERE id = ANY(%(ids)s)",
                {"ids": ids},
            )

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM items
            WHERE (title IS NULL OR title = '') AND (clean_text IS NULL OR clean_text = '')
              AND created_at < now() - interval '2 days'
            ORDER BY id
            """
        )
        empty_old_ids = [r["id"] for r in cur.fetchall()]

        deletable_empty: list[int] = []
        skipped_referenced: list[dict[str, Any]] = []
        for item_id in empty_old_ids:
            refs = _item_reference_counts(cur, item_id)
            if any(refs.values()):
                skipped_referenced.append({"id": item_id, **refs})
            else:
                deletable_empty.append(item_id)

        deleted_empty = 0
        if deletable_empty and apply:
            cur.execute("DELETE FROM items WHERE id = ANY(%(ids)s)", {"ids": deletable_empty})
            deleted_empty = cur.rowcount
        elif deletable_empty:
            deleted_empty = len(deletable_empty)

    return {
        "interstitial_rows": interstitial_rows,
        "interstitial_marked": len(interstitial_rows) if apply else 0,
        "empty_old_candidates": empty_old_ids,
        "empty_old_deletable": deletable_empty,
        "empty_old_skipped_referenced": skipped_referenced,
        "empty_old_deleted": deleted_empty,
        "apply": apply,
    }


def _item_reference_counts(cur: Any, item_id: int) -> dict[str, int]:
    """"Nothing references it" check for a candidate-for-deletion item: events, investigation_log
    (trigger_item), item_corroboration, and any other item's dedup_of pointer."""
    cur.execute("SELECT count(*) AS n FROM events WHERE item_id = %(id)s", {"id": item_id})
    events_n = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM investigation_log WHERE trigger_item = %(id)s", {"id": item_id})
    investigations_n = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM item_corroboration WHERE item_id = %(id)s", {"id": item_id})
    corroboration_n = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM items WHERE dedup_of = %(id)s", {"id": item_id})
    dedup_referrers_n = cur.fetchone()["n"]
    return {
        "events": events_n,
        "investigation_log": investigations_n,
        "item_corroboration": corroboration_n,
        "dedup_referrers": dedup_referrers_n,
    }


def _url_host(url: str | None) -> str:
    if not url:
        return ""
    from urllib.parse import urlparse

    try:
        return urlparse(url).netloc or url
    except ValueError:
        return url


# ---------------------------------------------------------------------------------------------
# Category 3: dedup_of chains
# ---------------------------------------------------------------------------------------------


def resolve_dedup_root(
    item_id: int, dedup_map: dict[int, int | None], valid_ids: set[int] | None = None
) -> int | None:
    """Walks ``item_id``'s ``dedup_of`` chain to its true root (a node whose own ``dedup_of`` is
    NULL, i.e. no longer a key in ``dedup_map``). Returns ``None`` (unresolvable this pass) for two
    defensive cases the caller must handle separately rather than this function guessing: a cycle
    (A->B->A -- should never happen given the app never lets an item point at itself, but a cleanup
    script must not trust that), and -- when ``valid_ids`` is given -- a chain that walks into an
    item id that no longer exists (that row's own direct ``dedup_of`` is the caller's separate
    missing-target check to fix; a second run of this idempotent script then converges the rest of
    the chain onto the now-shorter one). Pure function (no DB), directly unit-testable."""
    seen: set[int] = set()
    current = item_id
    while current in dedup_map:
        target = dedup_map[current]
        if target is None:
            return current
        if valid_ids is not None and target not in valid_ids:
            return None
        if target in seen or target == item_id:
            return None  # cycle -- refuse to guess
        seen.add(current)
        current = target
    return current


def cleanup_dedup_chains(*, apply: bool = False) -> dict[str, Any]:
    """Category 3. Any ``items.dedup_of`` row whose target itself has a non-NULL ``dedup_of``
    (a chain longer than one hop) is repointed directly at the chain's true root
    (:func:`resolve_dedup_root`). Any ``dedup_of`` pointing at an item id that no longer exists is
    cleared to NULL. Both repairs are independent and idempotent."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, dedup_of FROM items WHERE dedup_of IS NOT NULL")
        rows = cur.fetchall()
        dedup_map = {r["id"]: r["dedup_of"] for r in rows}
        cur.execute("SELECT id FROM items")
        all_ids = {r["id"] for r in cur.fetchall()}

        missing_target_fixes = [
            {"id": iid, "old_dedup_of": tgt} for iid, tgt in dedup_map.items() if tgt not in all_ids
        ]
        chain_fixes: list[dict[str, Any]] = []
        for iid, tgt in dedup_map.items():
            if tgt not in all_ids:
                continue  # handled above
            root = resolve_dedup_root(iid, dedup_map, all_ids)
            if root is not None and root != tgt:
                chain_fixes.append({"id": iid, "old_dedup_of": tgt, "new_dedup_of": root})

        if apply:
            for fix in missing_target_fixes:
                cur.execute(
                    "UPDATE items SET dedup_of = NULL, updated_at = now() WHERE id = %(id)s", {"id": fix["id"]}
                )
            for fix in chain_fixes:
                cur.execute(
                    "UPDATE items SET dedup_of = %(root)s, updated_at = now() WHERE id = %(id)s",
                    {"root": fix["new_dedup_of"], "id": fix["id"]},
                )

    return {
        "missing_target_fixes": missing_target_fixes,
        "chain_fixes": chain_fixes,
        "apply": apply,
    }


# ---------------------------------------------------------------------------------------------
# Category 4: events
# ---------------------------------------------------------------------------------------------


def cleanup_events(*, apply: bool = False) -> dict[str, Any]:
    """Category 4, three independent sub-cleanups: (a) events whose ``item_id`` no longer exists;
    (b) events belonging to an out-of-scope (``COALESCE(domain,'out_of_scope')='out_of_scope'``,
    same NULL-safe rule ``scripts/repair_round6.py``'s ``find_out_of_scope_events`` uses) or
    archived (``level='archive'``) item -- the round-6 rule, re-applied because a new instance
    appeared today (event on item 22); (c) exact duplicates (same item_id/kind/title), keeping the
    lowest id."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT e.id FROM events e LEFT JOIN items i ON i.id = e.item_id "
            "WHERE e.item_id IS NOT NULL AND i.id IS NULL ORDER BY e.id"
        )
        orphan_ids = [r["id"] for r in cur.fetchall()]

        cur.execute(
            "SELECT e.id, e.item_id FROM events e JOIN items i ON i.id = e.item_id "
            "WHERE COALESCE(i.domain, 'out_of_scope') = 'out_of_scope' OR i.level = 'archive' ORDER BY e.id"
        )
        out_of_scope_rows = cur.fetchall()
        out_of_scope_ids = [r["id"] for r in out_of_scope_rows]

        cur.execute(
            "SELECT item_id, kind, title, array_agg(id ORDER BY id) AS ids "
            "FROM events GROUP BY item_id, kind, title HAVING count(*) > 1"
        )
        dup_groups = cur.fetchall()
        dup_ids: list[int] = []
        for grp in dup_groups:
            dup_ids.extend(grp["ids"][1:])  # keep the lowest id, drop the rest

        all_delete_ids = sorted(set(orphan_ids) | set(out_of_scope_ids) | set(dup_ids))
        deleted = 0
        if all_delete_ids and apply:
            cur.execute("DELETE FROM events WHERE id = ANY(%(ids)s)", {"ids": all_delete_ids})
            deleted = cur.rowcount
        elif all_delete_ids:
            deleted = len(all_delete_ids)

    return {
        "orphan_item_id_ids": orphan_ids,
        "out_of_scope_ids": out_of_scope_ids,
        "duplicate_ids": dup_ids,
        "total_delete_ids": all_delete_ids,
        "deleted": deleted,
        "apply": apply,
    }


# ---------------------------------------------------------------------------------------------
# Category 5: reports
# ---------------------------------------------------------------------------------------------


def report_group_key(kind: str, period_end: Any, territory: str | None, topic: str | None) -> tuple:
    """Grouping key for report retention. ``topic`` (``patent_surveys.topic``, joined by
    ``report_id``) is only meaningful for ``kind='patent_survey'`` -- other kinds pass ``None`` and
    fall back to plain ``(kind, period_end, territory)`` grouping (``product_line`` reports already
    carry their distinguishing dimension directly in ``territory``, e.g. 'targeting_pods' vs
    'mws_eo', so no join is needed there)."""
    return (kind, period_end, territory, topic if kind == "patent_survey" else None)


def reports_to_retire(rows: list[dict[str, Any]]) -> list[int]:
    """Pure retention logic (no DB): given every row of ONE group (each a dict with ``id``,
    ``qa_passed``, ``created_at``), returns the ids that should be deleted -- a ``qa_passed=false``
    row that is NOT the group's single newest row (by ``created_at``) AND has at least one
    ``qa_passed=true`` sibling created later. The group's newest row is never returned, regardless
    of its own ``qa_passed`` value (it may be the only attempt so far, not yet QA'd)."""
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: r["created_at"])
    newest_id = ordered[-1]["id"]
    newest_passed_at = max((r["created_at"] for r in rows if r["qa_passed"]), default=None)
    if newest_passed_at is None:
        return []
    return [
        r["id"]
        for r in ordered
        if r["id"] != newest_id and not r["qa_passed"] and r["created_at"] < newest_passed_at
    ]


def cleanup_reports(*, apply: bool = False) -> dict[str, Any]:
    """Category 5, two sub-actions: (a) any ``reports`` row whose ``path_md`` no longer resolves to
    a file on disk (via ``_resolve_repo_path``, which also remaps the pre-native-migration
    ``/app/...`` prefix -- ADR-004) is deleted outright -- a dangling DB pointer with nothing left
    to protect; (b) per :func:`report_group_key` group, :func:`reports_to_retire` picks the
    superseded failed-QA rows to delete. Their files (md/html/docx) are deleted too, but ONLY when
    no *surviving* ``reports`` row (in any group) still points at that same resolved path -- a
    live check found every current candidate shares its path with the group's kept row (same-day
    reruns overwrite one date-named file in place), so deleting "the file" there would have
    destroyed the live, passing report."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT r.id, r.kind, r.period_end, r.territory, r.qa_passed, r.created_at, "
            "r.path_md, r.path_html, r.path_docx, ps.topic "
            "FROM reports r LEFT JOIN patent_surveys ps ON ps.report_id = r.id "
            "ORDER BY r.kind, r.period_end, r.territory, r.created_at"
        )
        rows = cur.fetchall()

    missing_file_ids: list[int] = []
    for row in rows:
        path = row.get("path_md")
        resolved = _resolve_repo_path(path) if path else None
        if resolved is None or not resolved.exists():
            missing_file_ids.append(row["id"])

    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        if row["id"] in missing_file_ids:
            continue  # already scheduled for deletion via the missing-file check above
        key = report_group_key(row["kind"], row["period_end"], row["territory"], row.get("topic"))
        groups.setdefault(key, []).append(row)

    retire_ids: list[int] = []
    for grp_rows in groups.values():
        retire_ids.extend(reports_to_retire(grp_rows))

    rows_by_id = {r["id"]: r for r in rows}
    surviving_paths: set[str] = set()
    all_retire = set(missing_file_ids) | set(retire_ids)
    for row in rows:
        if row["id"] in all_retire:
            continue
        for key in ("path_md", "path_html", "path_docx"):
            v = row.get(key)
            if v:
                surviving_paths.add(str(_resolve_repo_path(v)).lower())

    files_to_delete: list[Path] = []
    files_skipped_shared: list[str] = []
    for report_id in retire_ids:  # missing-file rows have no file to delete by definition
        row = rows_by_id[report_id]
        for key in ("path_md", "path_html", "path_docx"):
            v = row.get(key)
            if not v:
                continue
            resolved = _resolve_repo_path(v)
            resolved_str = str(resolved).lower()
            if resolved_str in surviving_paths:
                files_skipped_shared.append(resolved_str)
                continue
            if resolved.exists():
                files_to_delete.append(resolved)

    deleted_rows = 0
    deleted_files = 0
    if apply:
        all_ids = list(all_retire)
        if all_ids:
            with db.connection() as conn, conn.cursor() as cur:
                cur.execute("DELETE FROM reports WHERE id = ANY(%(ids)s)", {"ids": all_ids})
                deleted_rows = cur.rowcount
        for path in files_to_delete:
            try:
                path.unlink()
                deleted_files += 1
            except OSError:
                pass
    else:
        deleted_rows = len(all_retire)
        deleted_files = len(files_to_delete)

    return {
        "missing_file_ids": missing_file_ids,
        "retire_ids": retire_ids,
        "total_delete_ids": sorted(all_retire),
        "deleted_rows": deleted_rows,
        "files_to_delete": [str(p) for p in files_to_delete],
        "files_skipped_shared_with_survivor": sorted(set(files_skipped_shared)),
        "deleted_files": deleted_files,
        "apply": apply,
    }


# ---------------------------------------------------------------------------------------------
# Category 6: tenders
# ---------------------------------------------------------------------------------------------


def cleanup_tenders(*, apply: bool = False) -> dict[str, Any]:
    """Category 6, two sub-actions: (a) ``status='unknown'`` candidates older than 7 days that now
    fail the live relevance gate (:func:`scripts.purge_stale_tenders.fails_gate`, reused read-only
    exactly as the brief asks) are archived (``status='archived'``) -- never deleted, unlike
    ``purge_stale_tenders``'s own harder purge. ``tenders`` has no ``reason`` column (verified live
    against the schema), so the brief's "with a reason column if present" is a no-op here. (b)
    exact URL duplicates collapsed to the lowest id (CASCADE-deletes any ``tender_feedback`` on the
    dropped rows -- verified via ``pg_constraint``)."""
    sources_by_id = {s.id: s for s in load_tender_sources()}
    procurement_signals = load_procurement_signals()
    deny_domains = load_deny_domains()

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM tenders WHERE status = 'unknown' AND created_at < now() - interval '7 days' "
            "ORDER BY id"
        )
        stale_unknown = cur.fetchall()

        to_archive: list[dict[str, Any]] = []
        for row in stale_unknown:
            reason = fails_gate(row, sources_by_id, procurement_signals, deny_domains)
            if reason is not None:
                to_archive.append({"id": row["id"], "title": row.get("title"), "gate_reason": reason})
        if to_archive and apply:
            ids = [r["id"] for r in to_archive]
            cur.execute(
                "UPDATE tenders SET status = 'archived', updated_at = now() WHERE id = ANY(%(ids)s)",
                {"ids": ids},
            )

        cur.execute(
            "SELECT url, array_agg(id ORDER BY id) AS ids FROM tenders "
            "WHERE url IS NOT NULL AND url <> '' GROUP BY url HAVING count(*) > 1"
        )
        dup_groups = cur.fetchall()
        dup_ids: list[int] = []
        for grp in dup_groups:
            dup_ids.extend(grp["ids"][1:])
        deleted_dups = 0
        if dup_ids and apply:
            cur.execute("DELETE FROM tenders WHERE id = ANY(%(ids)s)", {"ids": dup_ids})
            deleted_dups = cur.rowcount
        elif dup_ids:
            deleted_dups = len(dup_ids)

    return {
        "stale_unknown_checked": len(stale_unknown),
        "archived": to_archive,
        "duplicate_url_ids": dup_ids,
        "deleted_duplicates": deleted_dups,
        "apply": apply,
    }


# ---------------------------------------------------------------------------------------------
# Category 7: indicator_watchlist
# ---------------------------------------------------------------------------------------------


def cleanup_watchlist(*, apply: bool = False) -> dict[str, Any]:
    """Category 7: ``status='dropped'`` rows older than 30 days, and any row whose ``text_he`` is
    empty, are deleted. ``indicator_watchlist`` has no incoming foreign keys (verified via
    ``pg_constraint``), so a plain delete is safe."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM indicator_watchlist WHERE status = 'dropped' "
            "AND last_seen < now() - interval '30 days'"
        )
        dropped_ids = [r["id"] for r in cur.fetchall()]
        cur.execute(
            "SELECT id FROM indicator_watchlist WHERE text_he IS NULL OR text_he = ''"
        )
        empty_ids = [r["id"] for r in cur.fetchall()]

        all_ids = sorted(set(dropped_ids) | set(empty_ids))
        deleted = 0
        if all_ids and apply:
            cur.execute("DELETE FROM indicator_watchlist WHERE id = ANY(%(ids)s)", {"ids": all_ids})
            deleted = cur.rowcount
        elif all_ids:
            deleted = len(all_ids)

    return {
        "dropped_stale_ids": dropped_ids,
        "empty_text_ids": empty_ids,
        "total_delete_ids": all_ids,
        "deleted": deleted,
        "apply": apply,
    }


# ---------------------------------------------------------------------------------------------
# Category 8: search cache (filesystem only, no DB)
# ---------------------------------------------------------------------------------------------

_CACHE_MAX_AGE_DAYS = 14


def purge_search_cache(cache_dir: Path | None = None, *, apply: bool = False) -> dict[str, Any]:
    """Category 8: deletes every file under ``runtime/cache/search`` whose mtime is older than
    :data:`_CACHE_MAX_AGE_DAYS`. Pure filesystem, no DB -- safe to call independently of the other
    categories."""
    cache_dir = cache_dir or (REPO_ROOT / "runtime" / "cache" / "search")
    if not cache_dir.exists():
        return {"checked": 0, "stale_files": [], "deleted": 0, "bytes_freed": 0, "apply": apply}

    cutoff = time.time() - _CACHE_MAX_AGE_DAYS * 86400
    all_files = [p for p in cache_dir.iterdir() if p.is_file()]
    stale = [p for p in all_files if p.stat().st_mtime < cutoff]
    bytes_freed = sum(p.stat().st_size for p in stale)

    deleted = 0
    if apply:
        for p in stale:
            try:
                p.unlink()
                deleted += 1
            except OSError:
                pass
    else:
        deleted = len(stale)

    return {
        "checked": len(all_files),
        "stale_files": [str(p) for p in stale],
        "deleted": deleted,
        "bytes_freed": bytes_freed,
        "mb_freed": round(bytes_freed / (1024 * 1024), 2),
        "apply": apply,
    }


# ---------------------------------------------------------------------------------------------
# Category 9: superseded report files with no DB row (filesystem, but needs the DB to know what's
# referenced)
# ---------------------------------------------------------------------------------------------

_ORPHAN_FILE_MIN_AGE_DAYS = 1
_REPORT_FILE_SUFFIXES = (".md", ".html", ".docx")


def find_orphan_report_files(
    reports_dir: Path, referenced_paths: set[str], *, min_age_days: int = _ORPHAN_FILE_MIN_AGE_DAYS
) -> list[Path]:
    """Pure function (no DB): every file directly under ``reports_dir`` (not its ``_archive``
    subdirectory) whose lowercased resolved path is not in ``referenced_paths`` and is older than
    ``min_age_days``."""
    if not reports_dir.exists():
        return []
    cutoff = time.time() - min_age_days * 86400
    orphans = []
    for p in reports_dir.iterdir():
        if not p.is_file() or p.suffix.lower() not in _REPORT_FILE_SUFFIXES:
            continue
        if str(p.resolve()).lower() in referenced_paths:
            continue
        if p.stat().st_mtime < cutoff:
            orphans.append(p)
    return orphans


def archive_orphan_report_files(*, apply: bool = False) -> dict[str, Any]:
    """Category 9. Files in ``output/reports/`` no ``reports.path_md``/``path_html``/``path_docx``
    row references, older than 1 day, are MOVED (never deleted -- the brief is explicit) into
    ``output/reports/_archive/`` (created if missing)."""
    reports_dir = REPO_ROOT / "output" / "reports"
    archive_dir = reports_dir / "_archive"

    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT path_md, path_html, path_docx FROM reports")
        rows = cur.fetchall()
    referenced: set[str] = set()
    for row in rows:
        for key in ("path_md", "path_html", "path_docx"):
            v = row.get(key)
            if v:
                referenced.add(str(_resolve_repo_path(v)).lower())

    orphans = find_orphan_report_files(reports_dir, referenced)

    moved = []
    if apply and orphans:
        archive_dir.mkdir(parents=True, exist_ok=True)
        for p in orphans:
            dest = archive_dir / p.name
            shutil.move(str(p), str(dest))
            moved.append(str(dest))

    return {
        "orphan_files": [str(p) for p in orphans],
        "moved": moved if apply else [str(p) for p in orphans],
        "moved_count": len(moved) if apply else len(orphans),
        "apply": apply,
    }


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------

_CATEGORY_FUNCS = {
    "jobs": cleanup_stale_jobs,
    "interstitial": cleanup_interstitial_items,
    "dedup": cleanup_dedup_chains,
    "events": cleanup_events,
    "reports": cleanup_reports,
    "tenders": cleanup_tenders,
    "watchlist": cleanup_watchlist,
    "cache": lambda apply: purge_search_cache(apply=apply),
    "orphan-files": archive_orphan_report_files,
}


def _run_category(name: str, *, apply: bool) -> dict[str, Any]:
    fn = _CATEGORY_FUNCS[name]
    return fn(apply=apply)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "category",
        choices=[*_CATEGORY_FUNCS.keys(), "all"],
        help="which cleanup category to run (or 'all' for every category)",
    )
    parser.add_argument(
        "--apply", action="store_true", help="write the computed cleanup (default: dry run, report only)"
    )
    args = parser.parse_args()
    apply = args.apply

    names = list(_CATEGORY_FUNCS.keys()) if args.category == "all" else [args.category]

    print("APPLIED" if apply else "DRY RUN (pass --apply to write)")
    print("=" * 78)
    for name in names:
        result = _run_category(name, apply=apply)
        print(f"[{name}]")
        for key, value in result.items():
            if key == "apply":
                continue
            if isinstance(value, list) and len(value) > 10:
                print(f"  {key}: {len(value)} item(s) -- first 10: {value[:10]}")
            else:
                print(f"  {key}: {value}")
        print("-" * 78)
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
