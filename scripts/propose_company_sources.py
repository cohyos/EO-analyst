"""Task B item 3 (2026-09-16, read-only): propose companies to add a press/LinkedIn-search source
for, ranked by what the system has already learned about them -- NOT a fixed hand-picked list.

Ranking signal, per company entity (`entities.kind = 'company'`):
  (a) mentions in in-scope items over the last 90 days (`items.entities_mentioned` name/alias hit,
      `domain != 'out_of_scope'`)
  (b) graph-edge count (`graph_edges` rows touching the entity, either direction)
  (c) whether the name appears in `config/product_lines.yaml`'s per-line `competitors`/
      `competitor_products.vendor` lists, or in `config/company_facts.yaml`

... excluding any company that already has a `*_press` or `*_linkedin_search` entry in
`config/sources.yaml` (matched heuristically -- see `_normalize`/`_already_covered`).

Read-only: only SELECTs against the DB (DATABASE_URL loaded silently from the environment/
runtime/eoa.env by the caller -- this script never prints it) and only reads YAML config files.
Writes nothing. Prints a ranked table; `--markdown` additionally emits the
`docs/SOURCES_COMPANY_EXPANSION_HE.md`-shaped proposal table to stdout.

Run with: ``PYTHONPATH=agent .venv/Scripts/python.exe scripts/propose_company_sources.py``
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

_SUFFIX_WORDS = {
    "ltd", "ltd.", "inc", "inc.", "corp", "corp.", "corporation", "group", "systems",
    "advanced", "defense", "defence", "industries", "industry", "technologies",
    "precision", "aerospace", "the", "company", "co",
}


def _normalize(name: str) -> str:
    """Loose company-name normalization for heuristic matching: lowercase, strip punctuation,
    drop common corporate-suffix words (\"Systems\"/\"Group\"/\"Ltd\"/...) -- good enough to match
    e.g. entity name \"Rafael\" against source display name \"Rafael Advanced Defense Systems\",
    not a legal-identity resolver."""
    name = re.sub(r"[^\w\s]", " ", name.lower())
    words = [w for w in name.split() if w not in _SUFFIX_WORDS]
    return " ".join(words).strip()


def _word_set(name: str) -> frozenset[str]:
    return frozenset(_normalize(name).split())


def _covers(entity_name: str, covered_word_sets: list[frozenset[str]]) -> bool:
    """True if `entity_name` (e.g. a bare acronym like "IAI") is already represented by one of the
    configured sources' display names, or vice versa -- word-subset match in EITHER direction so
    both a short acronym-only entity name and a longer full-legal-name entity name each match a
    source display name that spells out the other form (e.g. entity "IAI" <-> source display
    "Israel Aerospace Industries (IAI) - Press Releases", whose normalized word set includes the
    bare "iai" token from its own parenthetical)."""
    entity_ws = _word_set(entity_name)
    if not entity_ws:
        return False
    for covered_ws in covered_word_sets:
        if not covered_ws:
            continue
        if entity_ws <= covered_ws or covered_ws <= entity_ws:
            return True
    return False


def _load_covered_company_names() -> list[frozenset[str]]:
    """Companies already covered by a `*_press` or `*_linkedin_search` `config/sources.yaml`
    entry -- derived from each such source's own `name` field (e.g. "Elbit Systems - News",
    "LinkedIn · Rafael Advanced Defense Systems (search)"), as normalized word sets (see
    `_covers`)."""
    raw = yaml.safe_load((REPO_ROOT / "config" / "sources.yaml").read_text(encoding="utf-8")) or {}
    covered: list[frozenset[str]] = []
    for entry in raw.get("sources", []):
        sid = entry.get("id", "")
        if not (sid.endswith("_press") or sid.endswith("_linkedin_search")):
            continue
        display = entry.get("name", "")
        # Strip the "LinkedIn · " prefix / " (search)" suffix noise; keep the rest (including any
        # " - News"/" - Press Releases" tail and parenthetical acronym) so _word_set still sees
        # both the full name and any bare acronym it spells out (e.g. "(IAI)").
        display = re.sub(r"^LinkedIn\s*[·:-]\s*", "", display)
        display = re.sub(r"\s*\(search\)\s*$", "", display)
        ws = _word_set(display)
        if ws:
            covered.append(ws)
    return covered


def _load_product_line_competitor_names() -> set[str]:
    raw = yaml.safe_load((REPO_ROOT / "config" / "product_lines.yaml").read_text(encoding="utf-8")) or {}
    names: set[str] = set()
    for pl in raw.get("product_lines", []):
        for c in pl.get("competitors") or []:
            names.add(_normalize(c))
        for cp in pl.get("competitor_products") or []:
            vendor = cp.get("vendor")
            if vendor:
                names.add(_normalize(vendor))
    return names


def _load_company_facts_names() -> set[str]:
    path = REPO_ROOT / "config" / "company_facts.yaml"
    if not path.exists():
        return set()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    names: set[str] = set()
    for entry in raw.get("entries", []):
        if entry.get("name"):
            names.add(_normalize(entry["name"]))
        for parent in entry.get("parents") or []:
            names.add(_normalize(parent))
    return names


def _fetch_entity_signals() -> list[dict]:
    """One read-only pass: every `kind='company'` entity, its 90-day in-scope mention count
    (`items.entities_mentioned`, name-or-alias match, case-insensitive), and its graph-edge count."""
    from eoa.db import connection

    query = """
        WITH recent AS (
            SELECT id, unnest(entities_mentioned) AS entity_name, domain
            FROM items
            WHERE entities_mentioned IS NOT NULL
              AND (published_at >= now() - interval '90 days' OR fetched_at >= now() - interval '90 days')
        ),
        mention_counts AS (
            SELECT lower(entity_name) AS name_lc,
                   count(*) FILTER (WHERE domain IS DISTINCT FROM 'out_of_scope') AS in_scope_mentions,
                   count(*) AS total_mentions
            FROM recent
            GROUP BY lower(entity_name)
        ),
        edge_counts AS (
            SELECT entity_id, count(*) AS edges
            FROM (
                SELECT src_entity_id AS entity_id FROM graph_edges
                UNION ALL
                SELECT dst_entity_id AS entity_id FROM graph_edges
            ) t
            WHERE entity_id IS NOT NULL
            GROUP BY entity_id
        )
        SELECT e.id, e.name, e.aliases, e.country,
               COALESCE(mc.in_scope_mentions, 0) AS in_scope_mentions,
               COALESCE(mc.total_mentions, 0) AS total_mentions,
               COALESCE(ec.edges, 0) AS edges
        FROM entities e
        LEFT JOIN mention_counts mc
          ON lower(e.name) = mc.name_lc
          OR mc.name_lc = ANY (SELECT lower(a) FROM unnest(COALESCE(e.aliases, ARRAY[]::text[])) AS a)
        LEFT JOIN edge_counts ec ON ec.entity_id = e.id
        WHERE e.kind = 'company'
        ORDER BY in_scope_mentions DESC, edges DESC, e.name ASC
    """
    with connection() as conn, conn.cursor() as cur:
        cur.execute(query)
        return cur.fetchall()


def _guess_linkedin_slug(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower()).strip()
    slug = re.sub(r"\s+", "-", slug)
    return f"linkedin.com/company/{slug}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--markdown", action="store_true", help="also print the docs-table form")
    args = parser.parse_args()

    covered = _load_covered_company_names()
    competitor_names = _load_product_line_competitor_names()
    company_facts_names = _load_company_facts_names()

    rows = _fetch_entity_signals()
    proposals = []
    for row in rows:
        norm = _normalize(row["name"])
        if not norm or _covers(row["name"], covered):
            continue
        if row["in_scope_mentions"] == 0 and row["edges"] == 0 and norm not in competitor_names:
            continue  # no signal at all -- not worth proposing
        proposals.append(
            {
                "id": row["id"],
                "name": row["name"],
                "country": row["country"],
                "mentions_90d": row["in_scope_mentions"],
                "edges": row["edges"],
                "in_competitor_lists": norm in competitor_names,
                "in_company_facts": norm in company_facts_names,
                "linkedin_slug_guess": _guess_linkedin_slug(row["name"]),
            }
        )

    proposals.sort(
        key=lambda p: (
            p["mentions_90d"],
            p["edges"],
            p["in_competitor_lists"],
        ),
        reverse=True,
    )
    top = proposals[: args.top]

    print(f"{'name':40s} {'country':8s} {'mentions_90d':>13s} {'edges':>6s} {'competitor?':>12s}")
    for p in top:
        print(
            f"{p['name'][:40]:40s} {(p['country'] or ''):8s} {p['mentions_90d']:>13d} "
            f"{p['edges']:>6d} {'yes' if p['in_competitor_lists'] else 'no':>12s}"
        )
    print(f"\ntotal candidates considered: {len(rows)}; proposed (with signal, not yet covered): {len(proposals)}")

    if args.markdown:
        print("\n\n| # | חברה | מדינה | אזכורים (90 יום) | קשתות גרף | ברשימת מתחרים | LinkedIn (ניחוש) |")
        print("|---|---|---|---|---|---|---|")
        for i, p in enumerate(top, start=1):
            print(
                f"| {i} | {p['name']} | {p['country'] or '-'} | {p['mentions_90d']} | {p['edges']} | "
                f"{'כן' if p['in_competitor_lists'] else 'לא'} | {p['linkedin_slug_guess']} |"
            )


if __name__ == "__main__":
    main()
