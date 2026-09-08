"""TENDERS-SAM repair (2026-09-08, docs/qa/content_review/TENDERS-SAM.md item 2): re-score every
existing ``tenders`` row against the negative-keyword/defence-context relevance-demotion signal
(``eoa.tenders.scan._negative_keyword_penalty`` -- an off-topic-domain term such as "spectroscopy"/
"laboratory reagent"/"office supplies" present with no countervailing defence-context term such as
"military"/"NATO"/"DoD"). This is a pure demotion: a row's ``relevance_score`` is only ever lowered
(never raised), and its ``intake`` only ever moves toward ``'candidate'`` (never toward
``'accepted'``) -- consistent with the tenders module's own W2b "open intake, never reject
outright on a relevance signal" design (see ``eoa.tenders.scan`` module docstring).

Reuses ``eoa.tenders.scan.find_relevance_demotions``/``repair_relevance_scores`` directly so this
script and the live ``scan_tenders`` insertion path can never drift apart. Rows already carrying
``intake='rejected-by-user'`` (an explicit operator decision) are never touched.

Defaults to a dry run (report only, no writes, no backup file). Pass ``--apply`` to actually write
the demoted ``relevance_score``/``intake`` -- a JSON backup of every affected row's *before* state
is written first (``--backup-dir``, default ``runtime/backups``), so a mistaken repair is always
recoverable.

Run with the same DATABASE_URL as the app, e.g.:

    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/rescore_tenders_relevance.py              # dry run
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe scripts/rescore_tenders_relevance.py --apply       # write
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

# Windows consoles default stdout to the cp1252 codepage, which cannot encode non-ASCII title text
# this script may print -- reconfigure to UTF-8 (same fix as scripts/repair_tenders.py).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Allow running as `python scripts/rescore_tenders_relevance.py` without pre-setting
# PYTHONPATH=agent (mirrors scripts/repair_round7_tenders.py's own convenience fallback).
_AGENT_DIR = Path(__file__).resolve().parents[1] / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa.tenders.scan import find_relevance_demotions, repair_relevance_scores  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the demoted rows (default: dry run)")
    ap.add_argument(
        "--backup-dir",
        default="runtime/backups",
        help="directory for the pre-apply JSON backup (default: runtime/backups; only written with --apply)",
    )
    args = ap.parse_args()

    if args.apply:
        # Snapshot the affected rows' *before* state (id/source/title/relevance_score/intake) --
        # a dry-run listing already carries every field needed to reconstruct/undo the change, so
        # this backup is deliberately the same shape as find_relevance_demotions()'s own report,
        # just written to disk before any UPDATE runs.
        pre = find_relevance_demotions()
        backup_dir = Path(args.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_path = backup_dir / f"tenders_relevance_rescore_backup_{stamp}.json"
        backup_path.write_text(
            json.dumps(
                [
                    {
                        "id": r.id,
                        "source": r.source,
                        "title": r.title,
                        "negative_term": r.negative_term,
                        "before_score": r.before_score,
                        "before_intake": r.before_intake,
                    }
                    for r in pre
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"(backup written: {backup_path} -- {len(pre)} row(s))")

        results = repair_relevance_scores(apply=True)
        mode = "apply"
    else:
        results = find_relevance_demotions()
        mode = "dry_run"

    report = {
        "mode": mode,
        "demotions_found": len(results),
        "demotions": [
            {
                "id": r.id,
                "source": r.source,
                "title": r.title,
                "negative_term": r.negative_term,
                "before_score": r.before_score,
                "after_score": r.after_score,
                "before_intake": r.before_intake,
                "after_intake": r.after_intake,
            }
            for r in results
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
