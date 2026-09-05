"""One-off repair pass for mojibake already stored in `items.clean_text` / `items.title`.

Scans every row in `items` and re-applies `eoa.fetch.sanitize._repair_mojibake` to
`clean_text` and `title`, updating only the rows where the repair actually changed the
text, then prints how many rows (per column) were fixed. Written to clean up rows
ingested before `eoa.fetch.html._decode`'s charset-priority fix and this repair pass
existed (see `docs/MODULES.md`'s "Mojibake / encoding fix" section for the root cause).

Does not touch `raw_text` (kept as the original fetched artifact for provenance) and
does not re-run the extraction pipeline -- it only repairs the already-extracted text.

Run with the same `DATABASE_URL` as the app, e.g.:

    PYTHONPATH=agent DATABASE_URL=postgresql://eoa:change-me-local-only@127.0.0.1:5432/eoanalyst \\
        python scripts/repair_mojibake.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/repair_mojibake.py` without having to set
# PYTHONPATH=agent first (the documented invocation still works too -- this
# is just a convenience fallback).
_AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
if str(_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(_AGENT_DIR))

from eoa import db  # noqa: E402
from eoa.fetch.sanitize import _repair_mojibake  # noqa: E402


def _repair_all() -> tuple[int, int, int]:
    """Repair `clean_text`/`title` in place. Returns `(rows_scanned, clean_text_fixed, title_fixed)`."""
    scanned = 0
    clean_text_fixed = 0
    title_fixed = 0

    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, clean_text, title FROM items ORDER BY id")
            rows = cur.fetchall()

        for row in rows:
            scanned += 1
            clean_text = row["clean_text"]
            title = row["title"]

            new_clean_text = _repair_mojibake(clean_text) if clean_text else clean_text
            new_title = _repair_mojibake(title) if title else title

            changed_clean_text = new_clean_text != clean_text
            changed_title = new_title != title
            if not (changed_clean_text or changed_title):
                continue

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE items SET clean_text = %s, title = %s WHERE id = %s",
                    (new_clean_text, new_title, row["id"]),
                )

            if changed_clean_text:
                clean_text_fixed += 1
            if changed_title:
                title_fixed += 1

    return scanned, clean_text_fixed, title_fixed


def main() -> None:
    scanned, clean_text_fixed, title_fixed = _repair_all()
    print(f"scanned {scanned} items")
    print(f"clean_text repaired: {clean_text_fixed}")
    print(f"title repaired: {title_fixed}")


if __name__ == "__main__":
    main()
