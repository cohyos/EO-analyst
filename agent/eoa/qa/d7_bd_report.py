"""D7 -- business-development territory report deterministic checks (QA_CONTINUOUS_LOOP.md D7).

Operates on rendered ``output/reports/bd_<territory>_<date>.md`` files. Reuses
``eoa.report.bd_territory._COMPETITOR_PROMOTION_VERBS`` (the exact verb list the report builder
itself uses to reject a perspective violation before ever rendering) so this QA check can never
drift from what the live "BD-1" perspective gate already enforces.

PL-backend (2026-09-07): ``score_D7`` also scores ``output/reports/pl_<line_id>_<date>.md`` files
(``eoa.report.product_line``, the product-line status & business-development report) -- the same
Hebrew section-heading conventions (שורה תחתונה / תקציר מנהלים / פעולות מומלצות / מפת קונים /
הנחות והפרכות) apply to both report kinds, so every check here already generalizes; the only checks
genuinely scoped to a *territory* (``conference_dates_match_db``, which looks for a "כנסים" heading
the product-line report never renders, and ``acquisition_watch_scoped_to_territory``, gated on
extracting an ISO-2 territory code from the filename via ``_BD_FILENAME_RE``) already no-op cleanly
for a ``pl_*`` file -- no code branch needed for those two. The only two markers that DO need to
recognize either report kind explicitly are the "no activity" actions-section marker
(``NO_ACTIVITY_MARKER_HE``/``PL_NO_ACTIVITY_MARKER_HE``) and the genuinely-empty-report marker
(``_EMPTY_TERRITORY_MARKER_HE``/``PL_EMPTY_LINE_MARKER_HE``) -- see both usages below.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from eoa.config import settings
from eoa.qa.types import Check, DomainScore, weighted_score
from eoa.report.bd_territory import _COMPETITOR_PROMOTION_VERBS, NO_ACTIVITY_MARKER_HE
from eoa.report.geography import normalize_country
from eoa.report.product_line import PL_EMPTY_LINE_MARKER_HE, PL_NO_ACTIVITY_MARKER_HE

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)
_CONFERENCES_HEADING = "כנסים"
_ACTIONS_HEADING_KEYWORDS = ("פעולות", "המלצ")
_DATE_RANGE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})")

# ---------------------------------------------------------------------------------------------
# Round 5 (2026-09-06, docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 items 4/10/11): BLUF, the buyer
# pipeline table, and the assumptions/falsifiers list are landing in other engineers' file scopes
# (schemas/prompts/``bd_territory.py``) this same evening -- these are the deterministic,
# read-only-of-the-rendered-markdown QA gates for that work. Every check is tolerant of the
# feature not existing yet (a normal ``passed=False``, never an exception).
# ---------------------------------------------------------------------------------------------

_EXEC_SUMMARY_HEADING = "תקציר מנהלים"
_BLUF_HEADING_HE = "שורה תחתונה"
_MAX_BLUF_SENTENCES = 2
_MAX_BLUF_WORDS = 40
_CITATION_RE = re.compile(r"\[(\d+)\]")
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.?!])(?=\s|$)")
_BUYER_PIPELINE_HEADING_KEYWORDS = ("מפת קונים", "צינור הזדמנויות")
_ASSUMPTIONS_HEADING_KEYWORDS = ("הנחות והפרכות", "הנחות ואלטרנטיבות", "הנחות")
_FALSIFIER_KEYWORDS_HE = ("פריך", "הפרכ", "יופרך", "falsif")  # "יופרך אם" is the other valid renderer wording
_ACQUISITION_HEADING_HE = "מעקב רכישות ושותפויות"
_GLOBAL_MARKER_KEYWORD_HE = "גלובלי"
_EMPTY_TERRITORY_MARKER_HE = "לא זוהו בטריטוריה זו פריטים חדשים"  # eoa.report.bd_territory system_note_he
_BD_FILENAME_RE = re.compile(r"bd_([a-z]+)_\d{4}-\d{2}-\d{2}\.md$")


def _split_sentences_simple(text: str) -> list[str]:
    """Small local sentence splitter (mirrors ``eoa.report.qa_citations.split_sentences``'s basic
    shape without the abbreviation table) -- kept local per this module's own "no cross-domain
    private-name imports" convention."""
    if not text or not text.strip():
        return []
    return [p.strip() for p in _SENTENCE_BOUNDARY_RE.split(text.strip()) if p.strip()]


_TRAILING_CITES_AFTER_STOP_RE = re.compile(r"([.!?])((?:\s*\[\d+(?:\s*,\s*\d+)*\])+)")


def _normalize_bluf_body(body: str) -> str:
    """Round 5 follow-up (P3/P6 finding): the renderer emits the BLUF as ``**sentence. [3]**`` -- a
    citation group *after* the full stop -- and the plain sentence splitter then yields an uncited
    ``sentence.`` plus a bare ``[3]`` fragment, failing a correctly cited BLUF. Strip Markdown bold
    and move each trailing citation group back before its terminal punctuation before splitting."""
    text = (body or "").replace("**", "").strip()
    return _TRAILING_CITES_AFTER_STOP_RE.sub(lambda m: f" {m.group(2).strip()}{m.group(1)}", text)


def _bluf_check(sections: list[tuple[str, str]]) -> Check:
    """Item 4: a ``שורה תחתונה`` heading, before the exec summary, 1-2 short cited sentences --
    same shape as ``eoa.qa.d6_daily_report``'s own BLUF check, duplicated locally per this
    codebase's QA-module convention (see e.g. ``d8_patent_survey.py``'s duplicated disclosure
    strings) rather than importing across domain-scorer modules."""
    bluf_idx = exec_idx = None
    bluf_body = ""
    for i, (h, body) in enumerate(sections):
        if _BLUF_HEADING_HE in h and bluf_idx is None:
            bluf_idx, bluf_body = i, body
        if _EXEC_SUMMARY_HEADING in h and exec_idx is None:
            exec_idx = i
    if bluf_idx is None:
        return Check(
            "bluf_present_and_short", False, weight=1.5, evidence=f"no '{_BLUF_HEADING_HE}' heading found"
        )
    bluf_body = _normalize_bluf_body(bluf_body)
    sentences = _split_sentences_simple(bluf_body)
    word_count = len(bluf_body.split())
    cited = bool(sentences) and all(_CITATION_RE.search(s) for s in sentences)
    before_summary = exec_idx is None or bluf_idx < exec_idx
    ok = (
        0 < len(sentences) <= _MAX_BLUF_SENTENCES
        and word_count <= _MAX_BLUF_WORDS
        and cited
        and before_summary
    )
    return Check(
        "bluf_present_and_short",
        ok,
        weight=1.5,
        evidence=(
            f"{len(sentences)} sentence(s), {word_count} words, cited={cited}, "
            f"before_exec_summary={before_summary}"
        ),
    )


def _has_table_data_row(body: str) -> bool:
    """A markdown table (header + ``|---|`` separator) with at least one non-empty data row --
    local, simplified copy of ``d8_patent_survey._section_has_data_row``'s shape."""
    table_lines = [
        ln.strip() for ln in body.splitlines() if ln.strip().startswith("|") and ln.strip().endswith("|")
    ]
    if len(table_lines) < 3:
        return False
    return any(any(c.strip() for c in line.strip("|").split("|")) for line in table_lines[2:])


_PIPELINE_NO_OPPORTUNITIES_MARKER_HE = (
    "לא זוהו הזדמנויות"  # eoa.report.product_line / bd_territory empty-pipeline note
)


def _buyer_pipeline_check(sections: list[tuple[str, str]]) -> Check:
    """Item 10: a "מפת קונים / צינור הזדמנויות" table (opportunity -> stage -> buyer -> date)."""
    for h, body in sections:
        if (
            any(kw in h for kw in ("מפת קונים", "צינור הזדמנויות"))
            and _PIPELINE_NO_OPPORTUNITIES_MARKER_HE in body
        ):
            return Check(
                "buyer_pipeline_table_present",
                True,
                weight=2.0,
                evidence=f"heading '{h}': explicit no-opportunities note",
            )
    for h, body in sections:
        if any(kw in h for kw in _BUYER_PIPELINE_HEADING_KEYWORDS):
            return Check(
                "buyer_pipeline_table_present",
                _has_table_data_row(body),
                weight=2.0,
                evidence=f"heading '{h}' found; has populated table: {_has_table_data_row(body)}",
            )
    return Check(
        "buyer_pipeline_table_present",
        False,
        weight=2.0,
        evidence=f"no heading matching {_BUYER_PIPELINE_HEADING_KEYWORDS} found",
    )


def _assumptions_falsifiers_check(sections: list[tuple[str, str]]) -> Check:
    """Item 11: an "הנחות והפרכות" list -- each entry an "assumption <-> what would falsify it"
    pair, not free unstructured prose."""
    for h, body in sections:
        if any(kw in h for kw in _ASSUMPTIONS_HEADING_KEYWORDS):
            has_falsifier_language = any(kw in body for kw in _FALSIFIER_KEYWORDS_HE)
            has_list_items = any(ln.strip().startswith(("-", "*")) for ln in body.splitlines())
            ok = has_falsifier_language and has_list_items
            return Check(
                "assumptions_falsifiers_list_present",
                ok,
                weight=1.5,
                evidence=f"heading '{h}': falsifier_language={has_falsifier_language}, list_items={has_list_items}",
            )
    return Check(
        "assumptions_falsifiers_list_present",
        False,
        weight=1.5,
        evidence=f"no heading matching {_ASSUMPTIONS_HEADING_KEYWORDS} found",
    )


def _acquisition_primary_rows(body: str) -> list[list[str]]:
    """Table data rows of the acquisition-watch section that appear *before* any "גלובלי" marker
    line (``eoa.report.acquisition_watch.acquisition_watch_section_md``'s own W18 out-of-territory
    disclosure) -- these are the rows the report claims are territory-local."""
    rows: list[list[str]] = []
    for line in body.splitlines():
        if _GLOBAL_MARKER_KEYWORD_HE in line:
            break
        s = line.strip()
        if not s.startswith("|") or not s.endswith("|"):
            continue
        if all(c in "|-: " for c in s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if cells and cells[0] not in ("תאריך",):
            rows.append(cells)
    return rows


def _acquisition_watch_scope_violations(
    sections: list[tuple[str, str]], conn: Any, code: str | None
) -> list[str]:
    if conn is None or not code:
        return []
    body = ""
    for h, b in sections:
        if _ACQUISITION_HEADING_HE in h:
            body = b
            break
    if not body:
        return []
    rows = _acquisition_primary_rows(body)
    if not rows:
        return []
    names = {cells[1] for cells in rows if len(cells) >= 2 and cells[1] not in ("—", "-", "")}
    names |= {cells[3] for cells in rows if len(cells) >= 4 and cells[3] not in ("—", "-", "")}
    if not names:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name, country FROM entities WHERE name = ANY(%s) AND country IS NOT NULL",
                (sorted(names),),
            )
            country_by_name = {r["name"]: normalize_country(r["country"]) for r in cur.fetchall()}
    except Exception:
        return []
    violations: list[str] = []
    for cells in rows:
        company = cells[1] if len(cells) >= 2 else ""
        counterparty = cells[3] if len(cells) >= 4 else ""
        # Round 5 (2026-09-07, live bd_il/bd_us): an Elbit x Anduril deal is Israel-local even
        # though Anduril is a US company -- a row is in-territory when ANY named party with a known
        # country sits in the territory; flag it only when every known party is elsewhere.
        known = {
            name: country_by_name.get(name) for name in (company, counterparty) if country_by_name.get(name)
        }
        if known and all(country != code for country in known.values()):
            violations.append(
                ", ".join(f"{name}: {country}" for name, country in known.items())
                + f" (expected territory {code})"
            )
    return violations


def _sections(md_text: str) -> list[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(md_text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        out.append((m.group(2).strip(), md_text[start:end].strip()))
    return out


def _non_israeli_watchlist_names() -> list[str]:
    wl = settings().watchlist.get("companies", []) or []
    names = []
    for c in wl:
        if (c.get("country") or "").upper() != "IL":
            names.append(c.get("name", ""))
            names.extend(c.get("aliases") or [])
    return [n for n in names if n]


def _empty_headings(sections: list[tuple[str, str]]) -> list[str]:
    return [h for h, body in sections if not body.strip() or body.strip() in ("—", "-", "")]


def _actions_section_text(sections: list[tuple[str, str]]) -> str:
    return "\n".join(body for h, body in sections if any(kw in h for kw in _ACTIONS_HEADING_KEYWORDS))


def _is_no_activity_actions_text(actions_text: str) -> bool:
    """Round-3 (D7 finding 3): the report's actions/recommendations section carries the shared,
    machine-detectable "no activity in this window" marker (``eoa.report.bd_territory.
    NO_ACTIVITY_MARKER_HE`` for a BD-territory report, ``eoa.report.product_line.
    PL_NO_ACTIVITY_MARKER_HE`` for a product-line report -- PL-backend, 2026-09-07) -- an honest,
    deterministic statement that the market/table collection all came back empty, listing every
    watchlist competitor checked. Imported from the report builder itself (same convention as
    ``_COMPETITOR_PROMOTION_VERBS`` above) so this check can never drift from what the renderer
    actually emits."""
    return NO_ACTIVITY_MARKER_HE in actions_text or PL_NO_ACTIVITY_MARKER_HE in actions_text


def _actions_text_has_populated_rows(actions_text: str) -> bool:
    """A markdown table data row (a ``|``-delimited line, not the header/separator row) inside the
    actions section -- signals the report is *also* listing concrete recommended actions. Used to
    catch a self-contradictory report that claims "no activity" while still fabricating a
    populated actions table (D7 finding 3: stay strict about fabricated actions)."""
    for line in actions_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if stripped.startswith("|---") or stripped.strip("|").strip() in ("", "---"):
            continue
        if all(c in "|- " for c in stripped):
            continue
        return True
    return False


def _competitor_promotion_hits(actions_text: str, competitor_names: list[str]) -> list[str]:
    """Promotion-verb + watchlist-competitor-name hits, scoped to the recommended-actions
    section only -- mirrors ``eoa.report.bd_territory._action_promoted_competitor``, which checks
    only ``action_he``/``rationale_he`` on each ``BdAction``, never the report's market-overview
    prose (which legitimately names competitors using the same verbs in a purely descriptive
    sense, e.g. "השוק מציג התעצמות טכנולוגית ... Leonardo DRS" -- not a recommendation at all)."""
    hits = []
    for line in actions_text.splitlines():
        if "המתחרה" in line or "מעקב" in line or "מתחרים" in line:
            # round 7 (live bd_de): an action that tracks/monitors a competitor's presence
            # ('למעקב אחר נוכחות המתחרה') names it without promoting it
            continue
        if any(verb in line for verb in _COMPETITOR_PROMOTION_VERBS):
            for name in competitor_names:
                if name and name in line:
                    hits.append(f"{name}: {line.strip()[:100]}")
    return hits


def _conference_dates_match_db(sections: list[tuple[str, str]], conn: Any) -> tuple[int, int]:
    """(mismatches, total_checked) comparing every conference date-range mentioned in the report's
    conferences section against the ``conferences`` table by name."""
    conf_body = ""
    for heading, body in sections:
        if _CONFERENCES_HEADING in heading:
            conf_body = body
            break
    if not conf_body or conn is None:
        return 0, 0
    mismatches = 0
    total = 0
    with conn.cursor() as cur:
        for line in conf_body.splitlines():
            if "|" not in line or line.strip().startswith("|---"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            name, date_range = cells[0], cells[1]
            m = _DATE_RANGE_RE.search(date_range)
            if not m or name in ("שם",):
                continue
            total += 1
            cur.execute("SELECT start_date FROM conferences WHERE name = %s", (name,))
            row = cur.fetchone()
            if row is None:
                mismatches += 1
                continue
            reported_start = dt.date.fromisoformat(m.group(1))
            if row["start_date"] != reported_start:
                mismatches += 1
    return mismatches, total


def score_D7(md_paths: list[Path], conn: Any = None) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D7: deterministic checks over the latest BD report per territory."""
    md_paths = [p for p in md_paths if p and p.exists()]
    if not md_paths:
        return DomainScore(domain="D7", score_0_100=None, checks=[], n=0, note="no bd report files found")

    competitor_names = _non_israeli_watchlist_names()
    total_mismatch = 0
    total_checked = 0
    empty_heading_hits: list[str] = []
    promotion_hits: list[str] = []
    no_actions: list[str] = []
    bluf_checks: list[Check] = []
    buyer_pipeline_checks: list[Check] = []
    assumptions_checks: list[Check] = []
    scope_violations: list[str] = []

    for path in md_paths:
        text = path.read_text(encoding="utf-8")
        sections = _sections(text)
        empty_heading_hits.extend(f"{path.name}:{h}" for h in _empty_headings(sections))
        actions_text = _actions_section_text(sections)
        promotion_hits.extend(
            f"{path.name}:{hit}" for hit in _competitor_promotion_hits(actions_text, competitor_names)
        )
        mismatches, checked = _conference_dates_match_db(sections, conn)
        total_mismatch += mismatches
        total_checked += checked
        if (
            _EMPTY_TERRITORY_MARKER_HE not in text
            and PL_EMPTY_LINE_MARKER_HE not in text
            and PL_NO_ACTIVITY_MARKER_HE not in text  # tables-only product-line report (no market items)
        ):
            # Round 5 (2026-09-07, live bd_kr): the deliberate empty-territory report (zero items
            # in the window, expansion search queued) has no BLUF, buyer pipeline or assumptions
            # by design -- the benchmark structure checks apply to populated reports only. PL-
            # backend (2026-09-07): a genuinely empty product-line report (eoa.report.product_line
            # ._no_items_draft) is the same honest case, just a different marker string.
            bluf_checks.append(_bluf_check(sections))
            buyer_pipeline_checks.append(_buyer_pipeline_check(sections))
            assumptions_checks.append(_assumptions_falsifiers_check(sections))
        else:
            na = "empty-territory report -- not applicable"
            bluf_checks.append(Check("bluf_present_and_short", True, weight=1.5, evidence=na))
            buyer_pipeline_checks.append(Check("buyer_pipeline_table_present", True, weight=2.0, evidence=na))
            assumptions_checks.append(
                Check("assumptions_falsifiers_list_present", True, weight=1.5, evidence=na)
            )
        territory_match = _BD_FILENAME_RE.search(path.name)
        code = normalize_country(territory_match.group(1)) if territory_match else None
        scope_violations.extend(
            f"{path.name}:{hit}" for hit in _acquisition_watch_scope_violations(sections, conn, code)
        )
        # Round-3 (D7 finding 3): an honest "no activity this window" report (the shared marker
        # from eoa.report.bd_territory) counts as a populated actions section -- it is a
        # deterministic, machine-detectable statement that the section was checked and correctly
        # found nothing, not a silently empty/omitted section. Still fails outright if the marker
        # and an actual populated actions table both appear (self-contradictory / fabricated).
        is_no_activity = _is_no_activity_actions_text(actions_text)
        contradictory = is_no_activity and _actions_text_has_populated_rows(actions_text)
        has_actions = is_no_activity or any(
            any(kw in h for kw in _ACTIONS_HEADING_KEYWORDS) and body.strip() for h, body in sections
        )
        if not has_actions or contradictory:
            tag = "contradictory (no-activity marker + populated table)" if contradictory else "empty"
            no_actions.append(f"{path.name} ({tag})")

    checks = [
        Check(
            "conference_dates_match_db",
            passed=total_mismatch == 0,
            weight=1.5,
            evidence=f"{total_checked - total_mismatch}/{total_checked} conference dates matched the DB"
            if total_checked
            else "no conference rows to check",
        ),
        Check(
            "no_empty_headings",
            passed=len(empty_heading_hits) == 0,
            weight=1.0,
            evidence=f"empty headings: {empty_heading_hits[:10]}",
        ),
        Check(
            "no_competitor_promotion_language",
            passed=len(promotion_hits) == 0,
            weight=2.5,
            evidence=f"promotion-verb hits on watchlist competitors: {promotion_hits[:10]}",
        ),
        Check(
            "actions_table_nonempty",
            passed=len(no_actions) == 0,
            weight=2.0,
            evidence=f"reports with no populated actions/recommendations section: {no_actions}",
        ),
        # Round 5 (docs/REPORT_TEMPLATE_BENCHMARK.md sec 4 items 4/10/11): one aggregate Check per
        # new criterion across every territory report in scope this round (mirrors how the checks
        # above already aggregate across ``md_paths``).
        Check(
            "bluf_present_and_short",
            passed=all(c.passed for c in bluf_checks),
            weight=1.5,
            evidence="; ".join(f"{p.name}: {c.evidence}" for p, c in zip(md_paths, bluf_checks, strict=True))[
                :500
            ],
        ),
        Check(
            "buyer_pipeline_table_present",
            passed=all(c.passed for c in buyer_pipeline_checks),
            weight=2.0,
            evidence="; ".join(
                f"{p.name}: {c.evidence}" for p, c in zip(md_paths, buyer_pipeline_checks, strict=True)
            )[:500],
        ),
        Check(
            "assumptions_falsifiers_list_present",
            passed=all(c.passed for c in assumptions_checks),
            weight=1.5,
            evidence="; ".join(
                f"{p.name}: {c.evidence}" for p, c in zip(md_paths, assumptions_checks, strict=True)
            )[:500],
        ),
        Check(
            "acquisition_watch_scoped_to_territory",
            passed=len(scope_violations) == 0,
            weight=1.5,
            evidence=f"out-of-territory rows without a 'גלובלי' disclosure: {scope_violations[:10]}",
        ),
    ]
    return DomainScore(domain="D7", score_0_100=weighted_score(checks), checks=checks, n=len(md_paths))
