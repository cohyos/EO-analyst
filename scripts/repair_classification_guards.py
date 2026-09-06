#!/usr/bin/env python
"""Q3-2/Q3-3/Q3-4 repair (docs/qa/findings_Q3_r1.md): apply the classify/triage deterministic
guards to rows already in the DB (the guards themselves -- ``ClassifyOut``'s subdomain validator,
``eoa.pipeline.classify.apply_no_eoir_gate``, ``eoa.pipeline.triage._reconcile_score`` -- only run
on *new* LLM output going forward; this is the one-off backfill over existing rows).

Three independent passes, each with its own report section:

1. **Q3-3 -- invalid subdomain**: any classified item whose ``subdomain`` is not one of
   ``config/taxonomy.yaml``'s sub-keys for its ``domain`` gets it cleared to ``""`` (6 such rows
   in the QA sample).
2. **Q3-2 -- no-EO/IR-vocabulary gate**: any non-``out_of_scope`` item with no extracted entities
   and no EO/IR/CV vocabulary hit anywhere in its title/text, and no watchlist alias either, gets
   forced to ``domain=out_of_scope``/``level=archive``/``score=1`` (item 117: an AI/deepfake story
   classified ``c_uas``/``"c_ua_0"`` with zero entities is exactly this).
3. **Q3-4 -- triage score/reason inconsistency**: any triaged item whose ``triage_reason`` either
   (a) states an explicit ``score=N`` that disagrees with the persisted ``score`` column, or (b)
   concludes with a level word ("רמה .../level ...") that disagrees with what the persisted
   ``score`` actually maps to via config thresholds, gets the ``triage`` stage re-run in full
   (``eoa.pipeline.triage.triage_item``, which now goes through the same ``_reconcile_score``
   guard) -- ids 10/67 in the QA sample are exactly this pattern.

Use ``--dry-run`` to see what would change (and how many rows in each category) without writing
anything. Passes 1-2 are pure SQL/Python, no LLM call; pass 3 calls Ollama once per flagged item.

Run with the same ``DATABASE_URL`` as the app (Ollama reachable for pass 3), e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/repair_classification_guards.py [--dry-run] [--role resident]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa import db  # noqa: E402
from eoa.config import settings  # noqa: E402
from eoa.memory.relational import update_item_fields  # noqa: E402
from eoa.pipeline.classify import (  # noqa: E402
    _has_ai_market_labor_signal,
    _has_eoir_vocabulary,
    _has_generic_ai_tech_market_vocabulary,
    _watchlist_alias_hit,
)
from eoa.pipeline.triage import _reason_conflicting_level, level_for, triage_item  # noqa: E402

_SCORE_MENTION_RE = re.compile(r"score[`'\"]*\s*[=:]?\s*(\d{1,2})", re.IGNORECASE)

_CLASSIFY_COLS = "id, title, url, clean_text, domain, subdomain, entities_mentioned"
_TRIAGE_COLS = (
    "id, title, url, clean_text, domain, subdomain, report_kind, trl, entities_mentioned, "
    "summary_he, score, level, triage_reason"
)


def _fetch(cols: str, where: str) -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT {cols} FROM items WHERE {where}")
        return cur.fetchall()


def default_subdomain(domain: str | None) -> str | None:
    """The ``domain``'s first ("default") taxonomy sub-key, or ``None`` when the domain has no
    sub-keys at all (e.g. ``out_of_scope``) -- matches ``ClassifyOut``'s own
    ``_validate_subdomain_against_taxonomy`` fallback (schemas/analysis.py, D1 round-1 fix)."""
    domains = settings().taxonomy.get("domains", {}) or {}
    valid_subs = (domains.get(domain) or {}).get("sub", {}) or {}
    return next(iter(valid_subs), None)


def subdomain_missing(item: dict[str, Any]) -> bool:
    """D1 round-1 fix (``subdomain_valid_vs_taxonomy``, item 24): a NULL/empty subdomain on an
    in-scope item whose domain *does* have taxonomy sub-keys is just as invalid as a garbage
    string value -- the original Q3-3 pass (:func:`invalid_subdomain` below) only ever caught the
    latter (it explicitly returns ``False`` for an empty/``None`` value). A no-op for a domain
    with no sub-keys (``out_of_scope`` and the like), where an empty subdomain is correct."""
    if item.get("subdomain"):
        return False
    return default_subdomain(item.get("domain")) is not None


def invalid_subdomain(item: dict[str, Any]) -> bool:
    """Q3-3: ``item['subdomain']`` is non-empty but not a valid sub-key of its ``domain``."""
    subdomain = item.get("subdomain")
    if not subdomain:
        return False
    domains = settings().taxonomy.get("domains", {}) or {}
    valid_subs = (domains.get(item.get("domain")) or {}).get("sub", {}) or {}
    return subdomain not in valid_subs


def should_gate_no_eoir(item: dict[str, Any]) -> bool:
    """Q3-2: same three-signal check as ``eoa.pipeline.classify.apply_no_eoir_gate``, evaluated
    against an already-persisted item row instead of a fresh ``ClassifyOut``."""
    if item.get("domain") == "out_of_scope":
        return False
    if item.get("entities_mentioned"):
        return False
    text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
    return not (_has_eoir_vocabulary(text) or _watchlist_alias_hit(text))


def should_gate_generic_ai_market(item: dict[str, Any]) -> bool:
    """Goal 3 (2026-09-06, docs/qa/STATUS.md r3): same check as
    ``eoa.pipeline.classify.apply_generic_ai_market_gate``, evaluated against an already-persisted
    item row -- unlike :func:`should_gate_no_eoir` above, this one does NOT exempt items with
    extracted entities or a watchlist hit, since a defense company can legitimately appear in a
    generic AI/hi-tech labor-market article with zero real EO/IR/CV content. Requires BOTH generic
    AI/tech vocabulary AND an explicit labor-market/industry-trend frame (see the calibration note
    on ``apply_generic_ai_market_gate``) -- tested against the live DB, requiring either signal
    alone over-triggered on genuine defense-tech company/product news."""
    if item.get("domain") == "out_of_scope":
        return False
    text = " ".join(filter(None, [item.get("title"), item.get("clean_text")]))
    if _has_eoir_vocabulary(text):
        return False
    return _has_generic_ai_tech_market_vocabulary(text) and _has_ai_market_labor_signal(text)


def parse_stated_score(reason_he: str) -> int | None:
    """The last explicit ``score=N`` (or ``` `score` N ```-style) mention in ``reason_he``, if
    any -- used to catch a persisted ``score`` column that disagrees with what the model's own
    free-text reasoning actually concluded (Q3-4, ids 10/67)."""
    matches = _SCORE_MENTION_RE.findall(reason_he or "")
    return int(matches[-1]) if matches else None


def triage_inconsistent(item: dict[str, Any]) -> bool:
    score = item.get("score")
    if score is None:
        return False
    reason = item.get("triage_reason") or ""
    stated_score = parse_stated_score(reason)
    if stated_score is not None and stated_score != score:
        return True
    return _reason_conflicting_level(reason, level_for(score)) is not None


def repair(*, dry_run: bool = False, role: str = "resident") -> dict[str, Any]:
    report: dict[str, Any] = {
        "subdomain_repaired": [],
        "gated_out_of_scope": [],
        "gated_generic_ai_market": [],
        "triage_repaired": [],
        "triage_failed": [],
    }

    classify_items = _fetch(_CLASSIFY_COLS, "domain IS NOT NULL")
    for item in classify_items:
        if invalid_subdomain(item) or subdomain_missing(item):
            target = default_subdomain(item["domain"])
            report["subdomain_repaired"].append(
                {
                    "id": item["id"],
                    "domain": item["domain"],
                    "before_subdomain": item.get("subdomain"),
                    "after_subdomain": target,
                }
            )
            if not dry_run:
                update_item_fields(item["id"], subdomain=target)

        if should_gate_no_eoir(item):
            report["gated_out_of_scope"].append(
                {"id": item["id"], "before_domain": item["domain"], "before_subdomain": item.get("subdomain")}
            )
            if not dry_run:
                update_item_fields(
                    item["id"],
                    domain="out_of_scope",
                    subdomain=None,
                    level="archive",
                    score=1,
                    triage_reason="gate:no_eoir_vocabulary",
                )
            continue  # already gated by the broader Q3-2 rule -- don't double-count below

        # Goal 3: a narrower gate that fires even when entities/watchlist hits are present, so it
        # is checked independently of (and after) the Q3-2 no-vocabulary gate above.
        if should_gate_generic_ai_market(item):
            report["gated_generic_ai_market"].append(
                {"id": item["id"], "before_domain": item["domain"], "before_subdomain": item.get("subdomain")}
            )
            if not dry_run:
                update_item_fields(
                    item["id"],
                    domain="out_of_scope",
                    subdomain=None,
                    level="archive",
                    score=1,
                    triage_reason="gate:generic_ai_market_vocabulary",
                )

    triage_items = _fetch(_TRIAGE_COLS, "score IS NOT NULL AND triage_reason IS NOT NULL")
    for item in triage_items:
        if not triage_inconsistent(item):
            continue
        before = {"score": item["score"], "level": item["level"], "reason": item["triage_reason"]}
        if dry_run:
            report["triage_repaired"].append({"id": item["id"], "before": before})
            continue
        try:
            out = triage_item(item, role=role)
            update_item_fields(
                item["id"], score=out.score, level=out.level, triage_reason=out.reason_he[:600]
            )
            report["triage_repaired"].append(
                {"id": item["id"], "before": before, "after": {"score": out.score, "level": out.level}}
            )
        except Exception as exc:  # a repair run must not die on one bad row
            report["triage_failed"].append({"id": item["id"], "error": str(exc)[:200]})

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report what would change without writing it")
    parser.add_argument("--role", default="resident", help="LLM role for re-triage (default: resident)")
    args = parser.parse_args()

    report = repair(dry_run=args.dry_run, role=args.role)

    print(
        f"{'=' * 70}\nQ3-2/Q3-3/Q3-4/goal-3 classification/triage guard repair "
        f"{'(DRY RUN)' if args.dry_run else '(APPLIED)'}\n{'=' * 70}"
    )
    for key, rows in report.items():
        print(f"\n{key}: {len(rows)}")
        for r in rows[:20]:
            print(f"  {r}")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")

    total = (
        len(report["subdomain_repaired"])
        + len(report["gated_out_of_scope"])
        + len(report["gated_generic_ai_market"])
        + len(report["triage_repaired"])
    )
    print(
        f"\n{'=' * 70}\nTotal repaired: {total}  |  triage failed: {len(report['triage_failed'])}\n{'=' * 70}"
    )
    if args.dry_run:
        print("(dry run -- nothing written; re-run without --dry-run to apply)")


if __name__ == "__main__":
    main()
