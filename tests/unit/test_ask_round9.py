"""Round 9 (package R9-chat, docs/qa/loop/round_8_judge_b.md, D5 score 48).

The J8b re-judge asked all 8 golden questions live and found the round-8 hang fix genuinely holds
(8/8 completed, no hangs), but a different, still-live defect brought the domain score down from
round 7's 80: retrieval is structurally blind to most of this domain's vocabulary, the one grounded
answer had a ~34x numeric fabrication no guard caught, and the entailment check still never removes
anything.

1. **Retrieval blind to proper-noun-only questions:** `_rare_tokens` (`eoa.api.services`, line
   ~1613) required a digit inside every candidate token, so pure-letter proper nouns/acronyms --
   "SPECTRO", "Skyranger", "LORA", "DROIC", "AUSA" -- never matched; only "XM30" (digit-mixed) ever
   got a lexical hit, out of 8 golden questions. Closed by extending the extractor with ALL-CAPS
   acronyms (>= 3 letters), Capitalized words (>= 4 letters), hyphenated designations with no digit
   ("C-UAS", "E-HEL"), and non-stopword Hebrew words (>= 4 letters, one leading conjunction/
   preposition prefix stripped) -- capped at 8 tokens, ranked by actual corpus rarity (a `COUNT(*)`
   of ILIKE matches) once there are more candidates than the cap. A short (<= 4 char), pure-letter
   token now matches via a Postgres word-boundary regex (`~* \\yTOKEN\\y`) instead of a bare `ILIKE
   %TOKEN%` substring, closing the same "ATR inside a Dutch word" false-positive class the tenders
   package hit.
2. **Lexical pass must stand alone under RAM pressure:** the vector-embedding fallback fails almost
   every call under memory pressure (`ask.retrieve_embedding_failed`, ~9/9 that day) -- the lexical
   pass is now scored per item by how many distinct rare tokens it matches (title 2x, summary 1x,
   body-only 0.5x), recency tie-broken, instead of first-token-wins insertion order; a new
   `ask.retrieve_lexical_only` log line fires whenever the embedding step didn't succeed, so a judge
   can tell "lexical alone found N items" apart from "retrieval came back empty".
3. **Numeric grounding hole:** `_digits_grounded`/`_money_figure_grounded` (`eoa.api.ask_grounding`)
   let a claimed "53" (from "53 מיליארד דולר") pass as grounded against a real cited source's "$1.53
   bn" -- the digit run "53" is genuinely bounded by non-digit characters ('.' before, 'b' after)
   even though it is really the fractional part of an unrelated, ~34x smaller number. Closed two
   ways: (a) the boundary check also rejects a digit run immediately adjacent to a decimal point
   that continues into another digit (so "53" is never grounded by a substring of "1.53" or "2534"),
   and (b) any money figure carrying a recognisable scale word (billion/million/thousand, any
   spelling) now always compares as a magnitude, unit-aware, regardless of digit count -- not just
   the single-digit case round 5 already covered.
4. **Entailment check still never removes anything:** live-verified again 2026-09-07,
   `ask.entailment_check_skipped reason=timeout_or_error` on every attempt despite round 8's own
   `interactive=True` fix. Root cause one level up: `llm_providers.interactive_default` is `"chain"`
   (Claude -> Gemini -> local) as of round 7, and the `_call` closure passed no explicit `provider`,
   so this optional probe left the local, resource-gated path entirely and raced an unbounded cloud
   CLI subprocess against its own 30s wall-clock instead. Fixed by pinning `provider="ollama"`
   explicitly. `_run_with_timeout` also now reports the failing exception's class name instead of a
   single undifferentiated `None`, so a genuine timeout is distinguishable from a hard failure in
   the `ask.entailment_check_skipped` log line going forward.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv\\Scripts\\python -m pytest tests/unit/test_ask_round9.py -q``
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from eoa.api import ask_grounding, services


def _item(
    id: int,
    title: str = "",
    clean_text: str = "",
    summary_he: str = "",
    security_status: str | None = "clean",
    domain: str = "c_uas",
    key_facts: list[str] | None = None,
    url: str = "https://example.com",
    level: str | None = "yellow",
    source_name: str | None = "Example Source",
    sort_ts: Any = None,
) -> dict[str, Any]:
    return {
        "id": id,
        "title": title,
        "url": url,
        "clean_text": clean_text,
        "summary_he": summary_he,
        "key_facts": key_facts or [],
        "security_status": security_status,
        "domain": domain,
        "level": level,
        "source_name": source_name,
        "_sort_ts": sort_ts,
    }


def _src(id: int, title: str, text: str, **kw: Any) -> dict[str, Any]:
    base = {
        "id": id,
        "title": title,
        "url": f"https://example.test/{id}",
        "clean_text": text,
        "summary_he": "",
        "level": "yellow",
        "source_name": "מקור",
        "report_kind": None,
        "_is_context": False,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------------------------
# 1. Rare-token extraction -- finding 1
# ---------------------------------------------------------------------------------------------


class TestRareTokenExtractionPureLetterTerms:
    def test_all_caps_acronym_is_a_rare_token(self) -> None:
        assert "LORA" in services._rare_tokens("עסקת ה-LORA היוונית -- מה המשמעות?")
        assert "AUSA" in services._rare_tokens("מהי הרלוונטיות של כנס AUSA 2026?")
        assert "DROIC" in services._rare_tokens("מהי המגמה האחרונה ב-DROIC?")
        assert "SPECTRO" in services._rare_tokens("מה ידוע על מערכת ה-SPECTRO ISR?")
        assert "RFI" in services._rare_tokens("מהו ה-RFI העדכני ביותר בתחום EO/IR?")

    def test_capitalized_word_is_a_rare_token(self) -> None:
        assert "Skyranger" in services._rare_tokens("כיצד משתווה ה-Skyranger של Rheinmetall?")

    def test_hyphenated_designation_with_no_digit_is_a_rare_token(self) -> None:
        assert "C-UAS" in services._rare_tokens('מערכת נגד כטב"ם (C-UAS) ישראלית')
        assert "E-HEL" in services._rare_tokens("מערכת E-HEL חדשה")

    def test_digit_mixed_tokens_still_work_unchanged(self) -> None:
        """Regression guard: the pre-existing digit-requiring path (U9) must be untouched."""
        assert "XM30" in services._rare_tokens("מה זה XM30? ומה לגבי F-35?")
        assert "F-35" in services._rare_tokens("מה זה XM30? ומה לגבי F-35?")

    def test_generic_hebrew_question_still_yields_nothing(self) -> None:
        """Regression guard for the shared-suite assertion in test_ask_retrieval.py."""
        assert services._rare_tokens("שאלה כללית בלי שום מספר דגם") == []

    def test_hebrew_word_with_prefix_stripped_is_a_rare_token(self) -> None:
        # "בסקיריינג'ר" is not a real word; use a plausible >=4-letter proper-noun-like token with
        # a stripped conjunction prefix, distinct from every stoplist entry.
        tokens = services._rare_tokens("ומה לגבי ולורנטיום החדש?")
        assert any(t in ("ולורנטיום", "לורנטיום") for t in tokens)

    def test_common_hebrew_pronouns_and_boilerplate_are_never_flagged(self) -> None:
        tokens = services._rare_tokens(
            "מהי המגמה הטכנולוגית האחרונה ב-DROIC (Digital Read-Out Integrated Circuit), ומי מוביל אותה?"
        )
        assert "אותה" not in tokens
        assert "מוביל" not in tokens
        assert "המגמה" not in tokens


class TestShortTokenWordBoundaryQuery:
    def test_short_pure_letter_token_uses_word_boundary_regex(self) -> None:
        where_sql, params = services._keyword_where_clause("LORA")
        assert "~*" in where_sql
        assert params["t"] == r"\yLORA\y"

    def test_digit_mixed_token_keeps_plain_ilike(self) -> None:
        where_sql, params = services._keyword_where_clause("XM30")
        assert "ILIKE" in where_sql
        assert params["t"] == "%XM30%"

    def test_hyphenated_token_keeps_plain_ilike(self) -> None:
        where_sql, _params = services._keyword_where_clause("C-UAS")
        assert "ILIKE" in where_sql

    def test_long_pure_letter_token_keeps_plain_ilike(self) -> None:
        where_sql, _ = services._keyword_where_clause("SPECTRO")
        assert "ILIKE" in where_sql

    def test_short_token_query_is_actually_used_by_ask_retrieve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End-to-end: a short all-caps token ("LORA") must reach the DB via the word-boundary
        path, not a naive substring ILIKE, when ask_retrieve runs its lexical pass."""
        lora_item = _item(
            37,
            title="Israeli LORA missiles fired from a German frigate",
            summary_he="תקציר",
            level="yellow",
        )
        seen_queries: list[str] = []

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            seen_queries.append(query)
            if "~*" in query and params and params.get("t") == r"\yLORA\y":
                return [lora_item]
            return []

        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: None)
        monkeypatch.setattr(services, "_fetchall", fake_fetchall)
        monkeypatch.setattr(
            services.ollama_client, "embed", lambda texts, **kw: (_ for _ in ()).throw(RuntimeError("x"))
        )

        out = services.ask_retrieve("עסקת ה-LORA היוונית", [], [])
        assert [r["id"] for r in out] == [37]
        assert any("~*" in q for q in seen_queries)


class TestRareTokenRarityRanking:
    def test_within_cap_never_calls_the_db(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*a: Any, **kw: Any) -> Any:
            raise AssertionError("must not query the DB when candidate count is within the cap")

        monkeypatch.setattr(services, "_fetchone", _boom)
        out = services._rare_tokens("מה זה XM30? ומה לגבי F-35?")
        assert out == ["XM30", "F-35"]

    def test_over_cap_ranks_by_corpus_rarity_ascending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # 9 distinct ALL-CAPS acronym candidates -- one more than the cap of 8.
        question = " ".join(f"TOK{i}A" for i in range(9))  # e.g. "TOK0A TOK1A ... TOK8A"
        counts = {f"TOK{i}A": (i + 1) for i in range(9)}  # TOK0A rarest (1 hit) .. TOK8A commonest

        def fake_fetchone(query: str, params: Any = None) -> dict[str, Any]:
            token = params["t"].strip("%")
            return {"c": counts[token]}

        monkeypatch.setattr(services, "_fetchone", fake_fetchone)
        out = services._rare_tokens(question)
        assert len(out) == services._RARE_TOKEN_CAP
        # rarest-first: TOK0A (1 hit) must be kept, TOK8A (9 hits, commonest) must be dropped.
        assert "TOK0A" in out
        assert "TOK8A" not in out

    def test_zero_hit_candidates_are_deprioritised_below_real_ones(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A token that matches nothing in the corpus contributes nothing to retrieval -- it must
        never crowd out a token that matches at least once, even though "0" sorts lowest."""
        question = " ".join(f"TOK{i}A" for i in range(9))
        # TOK0A has zero corpus hits; every other token has >= 1.
        counts = {f"TOK{i}A": (0 if i == 0 else i) for i in range(9)}

        def fake_fetchone(query: str, params: Any = None) -> dict[str, Any]:
            token = params["t"].strip("%")
            return {"c": counts[token]}

        monkeypatch.setattr(services, "_fetchone", fake_fetchone)
        out = services._rare_tokens(question)
        assert len(out) == services._RARE_TOKEN_CAP
        assert "TOK0A" not in out  # zero-hit candidate correctly dropped in favour of real ones

    def test_db_failure_during_ranking_falls_back_to_original_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        question = " ".join(f"TOK{i}A" for i in range(9))

        def _boom(*a: Any, **kw: Any) -> Any:
            raise RuntimeError("db down")

        monkeypatch.setattr(services, "_fetchone", _boom)
        out = services._rare_tokens(question)  # must not raise
        assert len(out) == services._RARE_TOKEN_CAP
        assert out[0] == "TOK0A"  # falls back to original extraction order, truncated


# ---------------------------------------------------------------------------------------------
# 2. Lexical scoring + recency tie-break + ask.retrieve_lexical_only logging -- finding 2
# ---------------------------------------------------------------------------------------------


class TestLexicalScoring:
    def test_title_match_outranks_summary_only_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        title_hit = _item(1, title="SPECTRO ISR contract announced", summary_he="תקציר כלשהו")
        summary_hit = _item(2, title="Unrelated headline", summary_he="דיווח על SPECTRO ISR")

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            return [summary_hit, title_hit]  # deliberately summary-first in raw DB order

        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: None)
        monkeypatch.setattr(services, "_fetchall", fake_fetchall)
        monkeypatch.setattr(
            services.ollama_client, "embed", lambda texts, **kw: (_ for _ in ()).throw(RuntimeError("x"))
        )

        out = services.ask_retrieve("מה ידוע על מערכת ה-SPECTRO ISR?", [], [])
        assert [r["id"] for r in out] == [1, 2]  # title match (weight 2) ranked above summary (1)

    def test_multi_token_match_outranks_single_token_match(self, monkeypatch: pytest.MonkeyPatch) -> None:
        double_hit = _item(1, title="Rheinmetall Skyranger demo", summary_he="תקציר")
        single_hit = _item(2, title="Skyranger overview only", summary_he="תקציר")

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            token = params["t"].strip("%") if "%" in params.get("t", "") else params["t"].strip("\\y")
            if token.casefold() == "rheinmetall":
                return [double_hit]
            return [double_hit, single_hit]

        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: None)
        monkeypatch.setattr(services, "_fetchall", fake_fetchall)
        monkeypatch.setattr(
            services.ollama_client, "embed", lambda texts, **kw: (_ for _ in ()).throw(RuntimeError("x"))
        )

        out = services.ask_retrieve("Skyranger של Rheinmetall", [], [])
        ids = [r["id"] for r in out]
        assert ids[0] == 1  # matched by both "Skyranger" and "Rheinmetall" -> higher score
        assert 2 in ids

    def test_recency_tie_break_when_scores_are_equal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        older = _item(1, title="AUSA coverage older", summary_he="תקציר", sort_ts=dt.date(2026, 1, 1))
        newer = _item(2, title="AUSA coverage newer", summary_he="תקציר", sort_ts=dt.date(2026, 6, 1))

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            return [older, newer]

        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: None)
        monkeypatch.setattr(services, "_fetchall", fake_fetchall)
        monkeypatch.setattr(
            services.ollama_client, "embed", lambda texts, **kw: (_ for _ in ()).throw(RuntimeError("x"))
        )

        out = services.ask_retrieve("AUSA 2026", [], [])
        assert [r["id"] for r in out] == [2, 1]  # equal score -> newer item ranked first


class TestLexicalOnlyLogging:
    def test_logs_lexical_only_when_embedding_fails(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        item = _item(321, title="Elbit Systems wins $270m contract for SPECTRO ISR")
        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: None)
        monkeypatch.setattr(services, "_fetchall", lambda q, p=None: [item] if "SPECTRO" in str(p) else [])
        monkeypatch.setattr(
            services.ollama_client, "embed", lambda texts, **kw: (_ for _ in ()).throw(RuntimeError("no ram"))
        )

        services.ask_retrieve("מה ידוע על מערכת ה-SPECTRO ISR?", [], [])
        out = capsys.readouterr().out
        assert "ask.retrieve_lexical_only" in out
        assert "ask.retrieve_embedding_failed" in out

    def test_does_not_log_lexical_only_when_embedding_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: None)
        monkeypatch.setattr(services, "_fetchall", lambda q, p=None: [])
        monkeypatch.setattr(services.ollama_client, "embed", lambda texts, **kw: [[0.1, 0.2]])
        monkeypatch.setattr(services.vector, "nearest", lambda *a, **kw: [])

        services.ask_retrieve("שאלה כלשהי", [], [])
        out = capsys.readouterr().out
        assert "ask.retrieve_lexical_only" not in out


# ---------------------------------------------------------------------------------------------
# 3. Numeric grounding: decimal-boundary + unit-aware magnitude comparison -- finding 3
# ---------------------------------------------------------------------------------------------


class TestDecimalBoundaryDigitsGrounded:
    def test_53_is_not_grounded_by_1_53(self) -> None:
        assert ask_grounding._digits_grounded("53", "the program is worth $1.53bn total") is False

    def test_53_is_not_grounded_by_2534(self) -> None:
        assert ask_grounding._digits_grounded("53", "reference number 2534 was cited") is False

    def test_53_is_grounded_by_a_genuine_standalone_53(self) -> None:
        assert ask_grounding._digits_grounded("53", "the contract covers 53 units total") is True

    def test_153_is_not_grounded_by_a_forward_decimal_continuation(self) -> None:
        # "15" must not be considered grounded by the integer part of "15.3" -- a different number.
        assert ask_grounding._digits_grounded("15", "measured at 15.3 degrees") is False


class TestUnitAwareMoneyMagnitude:
    def test_53_billion_not_grounded_by_real_1_53_billion_source(self) -> None:
        """The exact live finding (docs/qa/loop/round_8_judge_b.md): a fabricated '53 billion'
        claim cited against a source whose real figure is $1.53bn -- ~34x off, must be flagged."""
        rows = [_src(257, "XM30 program funding", "The program is valued at $1.53bn per item 257.")]
        text = "### עובדות מרכזיות\n- שלב התוכנית הנוכחי מוערך ב-53 מיליארד דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "כמה שווה תוכנית XM30?", rows)
        assert removed == 1
        assert "53 מיליארד" not in new_text

    def test_1_53bn_grounded_by_1_530_million_different_phrasing(self) -> None:
        rows = [_src(1, "Contract value", "The deal is worth 1,530 million dollars total.")]
        text = "### עובדות מרכזיות\n- החוזה מוערך בכ-1.53 מיליארד דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_correctly_cited_1_53bn_stays_grounded(self) -> None:
        rows = [_src(257, "XM30 program funding", "The program is valued at $1.53bn per item 257.")]
        text = "### עובדות מרכזיות\n- שלב התוכנית הנוכחי מוערך ב-1.53 מיליארד דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "כמה שווה תוכנית XM30?", rows)
        assert removed == 0
        assert new_text == text

    def test_bare_unscaled_money_figure_still_uses_literal_check(self) -> None:
        """No scale word at all ('$1,234' with nothing else) -- falls back to the pre-existing
        literal digit-substring behaviour, unchanged."""
        rows = [_src(1, "Invoice", "Total due: $1,234 per the attached invoice.")]
        text = "### עובדות מרכזיות\n- הסכום הכולל עומד על 1,234$ [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text


# ---------------------------------------------------------------------------------------------
# 4. Entailment check: pinned to local Ollama, exception-class logging -- finding 4
# ---------------------------------------------------------------------------------------------


class TestEntailmentPinnedToOllama:
    def test_call_pins_provider_to_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Round 9 root cause: `llm_providers.interactive_default` is "chain" as of round 7, so an
        unset `provider` sent this call out over an unbounded cloud CLI subprocess instead of the
        resource-gated local path the round-8 `interactive=True` fix assumed it reached."""
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה תקינה [1]."
        captured: dict[str, Any] = {}

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            captured.update(kw)
            return ask_grounding._EntailmentResponse(verdicts=[])

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        ask_grounding.entailment_filter(text, rows, max_claims=4, timeout_s=5.0)
        assert captured.get("provider") == "ollama"
        assert captured.get("interactive") is True

    def test_run_with_timeout_reports_timeout_error_class(self) -> None:
        import time

        result, error = ask_grounding._run_with_timeout(lambda: time.sleep(0.3), 0.05)
        assert result is None
        assert error == "TimeoutError"

    def test_run_with_timeout_reports_the_real_exception_class(self) -> None:
        def _boom() -> None:
            raise ValueError("bad schema")

        result, error = ask_grounding._run_with_timeout(_boom, 5.0)
        assert result is None
        assert error == "ValueError"

    def test_run_with_timeout_returns_result_and_no_error_on_success(self) -> None:
        result, error = ask_grounding._run_with_timeout(lambda: 42, 5.0)
        assert result == 42
        assert error is None

    def test_skip_log_carries_the_specific_exception_class_not_a_generic_reason(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Source text.")]
        text = "### עובדות מרכזיות\n- טענה כלשהי [1]."

        def _raise(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            raise ConnectionError("cloud leg unreachable")

        monkeypatch.setattr(ollama_client, "chat_structured", _raise)
        ask_grounding.entailment_filter(text, rows, max_claims=4, timeout_s=5.0)
        out = capsys.readouterr().out
        assert "ask.entailment_check_skipped" in out
        assert "ConnectionError" in out


class TestEntailmentCoversTheDirectAnswerParagraph:
    def test_lead_paragraph_with_citations_is_a_candidate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The unheaded lead ("תשובה ישירה") IS in scope whenever it carries [n] citations, even
        though ask_answer_format.md does not require citations there -- confirms existing coverage
        (docs/qa/loop/round_8_judge_b.md finding 4's "run it on the direct-answer paragraph too")."""
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text about the program.")]
        text = "תוכנית ה-XM30 מוערכת בכ-1.53 מיליארד דולר [1].\n\n### פערים / מה לא ידוע\n- אין."
        captured: dict[str, Any] = {}

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            captured["messages"] = messages
            return ask_grounding._EntailmentResponse(verdicts=[])

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        ask_grounding.entailment_filter(text, rows, max_claims=4, timeout_s=5.0)
        assert "messages" in captured, "the unheaded lead paragraph's own citation must reach the LLM call"
        assert "טענה 1:" in captured["messages"][1]["content"]
