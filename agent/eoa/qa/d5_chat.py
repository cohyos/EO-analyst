"""D5 -- "ask the analyst" chat deterministic checks (docs/QA_CONTINUOUS_LOOP.md table row D5).

Per the task brief: check whether any chat-log table exists in the DB. As of this writing there
is none (the schema has no ``chat_log``/``ask_log``/similar table -- verified against
``information_schema.tables`` on the live DB: chat answers are generated on the fly by
``eoa.api`` and never persisted). D5's deterministic half is therefore marked "manual only" and
skipped -- the eight fixed golden questions (``docs/qa/loop/golden_questions.json``) still get
asked and scored, but only by the judge (rubric) pass, never by this script.
"""

from __future__ import annotations

from typing import Any

from eoa.qa.types import Check, DomainScore

#: Candidate table names that would make a deterministic pass possible, if one is ever added.
_CANDIDATE_TABLES = ("chat_log", "ask_log", "chat_messages", "ask_the_analyst_log")


def _chat_log_table_exists(conn: Any) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ANY(%s)",
            (list(_CANDIDATE_TABLES),),
        )
        row = cur.fetchone()
        return row["table_name"] if row else None


def score_D5(golden_questions: list[str], conn: Any) -> DomainScore:  # noqa: N802 -- score_Dn matches docs/QA_CONTINUOUS_LOOP.md naming
    """D5: deterministic half of the chat domain. Always returns ``score_0_100=None`` ("manual
    only") unless a chat-log persistence table appears in the schema, in which case this should
    be extended to check citation validity / no-raw-markdown / no-per-source-dump per the spec."""
    table = _chat_log_table_exists(conn)
    if table is None:
        return DomainScore(
            domain="D5",
            score_0_100=None,
            checks=[
                Check(
                    "chat_log_table_exists",
                    passed=False,
                    weight=1.0,
                    evidence="no chat-log persistence table found; D5 deterministic scoring is manual-only for now",
                )
            ],
            n=0,
            note="manual only -- judge the 8 golden_questions.json answers by hand each round",
        )
    # A future chat-log table would let this reuse eoa.report.qa_citations-style checks
    # (citation validity, no raw markdown headings, no per-source dump) -- left unimplemented
    # until that table exists, so as not to guess at a schema that doesn't exist yet.
    return DomainScore(
        domain="D5",
        score_0_100=None,
        checks=[Check("chat_log_table_exists", passed=True, weight=1.0, evidence=f"found table {table!r}")],
        n=0,
        note=f"table {table!r} exists but D5 deterministic checks are not yet implemented for it",
    )
