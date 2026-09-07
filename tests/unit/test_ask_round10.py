"""Round 10 (package R10-chat, docs/qa/loop/round_9_judge.md, D5 score 78; worst-list #3, #4, #5).

The J9 judge found round 9's chat fixes genuinely holding (8/8 answers completed, fast, richly
cited) but three real defects still live:

1. **Truncated/dangling opening sentence in 5/8 sampled answers** (worst #3): reproduced offline
   directly against `_iter_units` -- a plain "." between two digits (a decimal point, e.g. the "."
   in "1.53") was treated as a full sentence terminator exactly like a real sentence-ending ".",
   silently chopping a money-figure claim like "...מוערך ב-1.53 מיליארד דולר [1]." into two bogus
   half-sentence units. Money figures with a decimal point are extremely common in this domain's
   retrieved sources, so any downstream guard removing *either* half left the other half standing
   on its own as exactly the garbled, non-sentence fragment the judge found live. Fixed at the
   source (`_iter_units`/`_is_real_sentence_terminator` now recognise a decimal point and a short
   list of common Latin abbreviations as non-boundaries) plus a final, content-blind coherence pass
   (`enforce_answer_coherence`) that catches any other removal shape.
2. **Entailment guard active on only 1/8 answers** (worst #4): the round-9 fix pinned this optional
   probe to the local, resource-gated `provider="ollama"` path, which is itself starved under the
   same RAM pressure the rest of the host is under. A cloud leg bypasses the local resource gate
   entirely, so `entailment_filter` now supports an opt-in `chain_fallback=True` second attempt
   through the configured cloud chain (`provider="chain"`, 40s budget) when the primary local
   attempt fails -- wired on at the one real call site (`routes.ask`) via a new keyword, so every
   existing shared-suite test's tested single-attempt contract (`test_ask_round9.py`'s
   `TestEntailmentPinnedToOllama`, `test_ask_round7.py`'s `test_timeout_is_a_graceful_no_op`) stays
   byte-for-byte unchanged. When both attempts fail, a new `ask.entailment_unavailable` log line
   fires once per process (not per request).
3. **Q5 (Skyranger) item 1353's spec not evidenced** (worst #5): the item is genuinely retrieved by
   the lexical pass but ranked below the fixed top-8 cut. `ask_retrieve`'s cap now widens 8 -> 10
   whenever the question yields >= 3 rare tokens (a specific-enough query where a real, relevant
   item can legitimately rank 9th/10th without being any less relevant).

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv\\Scripts\\python -m pytest tests/unit/test_ask_round10.py -q``
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eoa.api import ask_grounding, services
from eoa.api.routes import ask as ask_route


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


def _item(id: int, title: str = "", summary_he: str = "", sort_ts: Any = None) -> dict[str, Any]:
    return {
        "id": id,
        "title": title,
        "url": "https://example.com",
        "clean_text": "",
        "summary_he": summary_he,
        "key_facts": [],
        "security_status": "clean",
        "domain": "c_uas",
        "level": "yellow",
        "source_name": "Example Source",
        "_sort_ts": sort_ts,
    }


# ---------------------------------------------------------------------------------------------
# 1a. `_iter_units` decimal-point / abbreviation sentence-boundary fix -- finding 1 root cause
# ---------------------------------------------------------------------------------------------


class TestSentenceBoundaryDecimalPoint:
    def test_decimal_money_figure_is_not_split(self) -> None:
        text = "התוכנית מוערכת ב-1.53 מיליארד דולר [1][2]. זהו מקור נוסף."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert units == ["התוכנית מוערכת ב-1.53 מיליארד דולר [1][2].", " זהו מקור נוסף."]

    def test_decimal_measurement_is_not_split(self) -> None:
        text = "החברה F-35 נמכרה ל-3.5 יחידות בשנת 2026 [1]. משפט שני."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert units[0] == "החברה F-35 נמכרה ל-3.5 יחידות בשנת 2026 [1]."

    def test_multiple_decimal_points_in_one_sentence_are_not_split(self) -> None:
        text = 'לפי הדו"ח, המחיר עומד על 12.7 מיליון ש"ח [1]. עוד משפט.'
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert units[0] == 'לפי הדו"ח, המחיר עומד על 12.7 מיליון ש"ח [1].'
        assert len(units) == 2

    def test_genuine_sentence_end_after_a_digit_still_splits(self) -> None:
        """A real sentence boundary right after a plain (non-decimal) number must still split --
        the fix must not over-suppress genuine terminators."""
        text = "יש 5 יחידות. עוד משפט."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert units == ["יש 5 יחידות.", " עוד משפט."]


class TestSentenceBoundaryAbbreviation:
    def test_common_latin_abbreviation_is_not_split(self) -> None:
        text = "החברה נמכרה לחברת Aerojet Rocketdyne Inc. במסגרת עסקה גדולה [1]. משפט שני."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert units[0] == "החברה נמכרה לחברת Aerojet Rocketdyne Inc. במסגרת עסקה גדולה [1]."
        assert len(units) == 2

    def test_ordinary_sentence_end_is_unaffected(self) -> None:
        text = "משפט רגיל שמסתיים כרגיל. עוד משפט."
        units = [text[s:e] for s, e in ask_grounding._iter_units(text)]
        assert units == ["משפט רגיל שמסתיים כרגיל.", " עוד משפט."]

    def test_exclamation_and_question_marks_are_always_real_terminators(self) -> None:
        """Only "." is decimal/abbreviation-ambiguous -- "!"/"?"/gershayim never are."""
        assert ask_grounding._is_real_sentence_terminator("מה זה?", 5) is True
        assert ask_grounding._is_real_sentence_terminator("וואו!", 4) is True


class TestGroundAndFilterAnswerNoLongerLeavesDanglingFragment:
    def test_removed_money_claim_takes_the_whole_sentence_not_half_of_it(self) -> None:
        """Integration-level regression for the round-9 judge's own live repro: a fabricated
        '53 billion' claim against a real '$1.53bn' source used to be silently pre-split by
        `_iter_units` into two half-sentence units before `ground_and_filter_answer` ever saw it --
        now the whole sentence is removed as one unit, no dangling '...ב-1.' left behind."""
        rows = [_src(257, "XM30 program funding", "The program is valued at $1.53bn per item 257.")]
        text = "### עובדות מרכזיות\n- שלב התוכנית הנוכחי מוערך ב-53 מיליארד דולר [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "כמה שווה תוכנית XM30?", rows)
        assert removed == 1
        assert "53 מיליארד" not in new_text
        assert "-1." not in new_text  # the old bug's own dangling-fragment shape


# ---------------------------------------------------------------------------------------------
# 1b. `enforce_answer_coherence` -- final content-blind safety net
# ---------------------------------------------------------------------------------------------


class TestEnforceAnswerCoherence:
    def test_short_dangling_leading_fragment_before_heading_is_dropped(self) -> None:
        text = "53.\n\n### עובדות מרכזיות\n- טענה תקינה עם מספיק מילים בפנים [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert "53." not in new_text
        assert "### עובדות מרכזיות" in new_text
        assert "טענה תקינה" in new_text

    def test_dangling_fragment_with_no_terminal_punctuation_as_only_unit_is_dropped(self) -> None:
        text = "53 מיליארד דולר בערך משהו כזה\n\n### עובדות מרכזיות\n- טענה שנייה שלמה לגמרי [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert "53 מיליארד דולר בערך משהו כזה" not in new_text

    def test_complete_short_lead_with_enough_words_and_terminal_punctuation_is_kept(self) -> None:
        text = "התוכנית אושרה השבוע במלואה [1].\n\n### עובדות מרכזיות\n- טענה שנייה [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_never_leaves_an_empty_section_heading(self) -> None:
        """When a heading's only unit is dropped as a dangling fragment, the now-empty heading
        itself is dropped too, not left standing over nothing."""
        text = "תשובה תקינה ומלאה עם מספיק מילים [1].\n\n### פערים / מה לא ידוע\n53."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 1
        assert "פערים" not in new_text
        assert "תשובה תקינה" in new_text

    def test_coherent_full_answer_is_a_no_op(self) -> None:
        text = (
            "תשובה ישירה ומלאה עם ציטוט תקין [1].\n\n"
            "### עובדות מרכזיות\n- עובדה ראשונה עם ציטוט [1].\n- עובדה שנייה עם ציטוט [1].\n\n"
            "### פערים / מה לא ידוע\n- אין פערים ידועים כרגע."
        )
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text

    def test_blank_answer_is_a_no_op(self) -> None:
        assert ask_grounding.enforce_answer_coherence("") == ("", 0)
        assert ask_grounding.enforce_answer_coherence("   ") == ("   ", 0)

    def test_bullet_list_leading_item_is_never_flagged_as_a_fragment(self) -> None:
        """A short-but-complete bulleted first item (its own bullet marker makes it a whole unit
        regardless of word count) must not be mistaken for a dangling sentence fragment."""
        text = "### עובדות מרכזיות\n- לא ידוע.\n- עובדה שנייה מלאה לגמרי עם ציטוט תקין [1]."
        new_text, removed = ask_grounding.enforce_answer_coherence(text)
        assert removed == 0
        assert new_text == text


# ---------------------------------------------------------------------------------------------
# 2. Entailment chain-fallback -- finding 2
# ---------------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_entailment_unavailable_flag() -> Iterator[None]:
    """`_ENTAILMENT_UNAVAILABLE_LOGGED` is process-lifetime, module-level state by design (round
    10's own once-per-process contract) -- reset it around every test in this class so one test's
    trigger cannot silently suppress another's expected log line."""
    ask_grounding._ENTAILMENT_UNAVAILABLE_LOGGED = False
    yield
    ask_grounding._ENTAILMENT_UNAVAILABLE_LOGGED = False


class TestEntailmentChainFallback:
    def test_default_chain_fallback_is_off(self) -> None:
        import inspect

        sig = inspect.signature(ask_grounding.entailment_filter)
        assert sig.parameters["chain_fallback"].default is False

    def test_chain_timeout_default_is_40_seconds(self) -> None:
        import inspect

        sig = inspect.signature(ask_grounding.entailment_filter)
        assert sig.parameters["chain_timeout_s"].default == 40.0

    def test_local_success_never_attempts_the_chain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה תקינה [1]."
        calls: list[str | None] = []

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            calls.append(kw.get("provider"))
            return ask_grounding._EntailmentResponse(verdicts=[])

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        ask_grounding.entailment_filter(text, rows, max_claims=4, timeout_s=5.0, chain_fallback=True)
        assert calls == ["ollama"]

    def test_local_failure_falls_back_to_chain_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה שגויה [1]."
        calls: list[str | None] = []

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            provider = kw.get("provider")
            calls.append(provider)
            if provider == "ollama":
                raise ConnectionError("local starved under RAM pressure")
            return ask_grounding._EntailmentResponse(
                verdicts=[ask_grounding._ClaimVerdict(index=1, verdict="no")]
            )

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        new_text, removed = ask_grounding.entailment_filter(
            text, rows, max_claims=4, timeout_s=5.0, chain_fallback=True, chain_timeout_s=5.0
        )
        assert calls == ["ollama", "chain"]
        assert removed == 1
        assert "טענה שגויה" not in new_text

    def test_local_failure_without_chain_fallback_never_tries_a_second_attempt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default (`chain_fallback=False`, unchanged from round 9): exactly one attempt, ever --
        the exact contract `test_ask_round7.py`'s `test_timeout_is_a_graceful_no_op` and
        `test_ask_round9.py`'s `TestEntailmentPinnedToOllama` depend on."""
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה כלשהי [1]."
        calls: list[str | None] = []

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            calls.append(kw.get("provider"))
            raise ConnectionError("local starved")

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        new_text, removed = ask_grounding.entailment_filter(text, rows, max_claims=4, timeout_s=5.0)
        assert calls == ["ollama"]
        assert removed == 0
        assert new_text == text

    def test_both_attempts_failing_logs_entailment_unavailable(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה כלשהי [1]."

        def _raise(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            raise ConnectionError("nothing works tonight")

        monkeypatch.setattr(ollama_client, "chat_structured", _raise)
        new_text, removed = ask_grounding.entailment_filter(
            text, rows, max_claims=4, timeout_s=5.0, chain_fallback=True, chain_timeout_s=5.0
        )
        out = capsys.readouterr().out
        assert removed == 0
        assert new_text == text
        assert "ask.entailment_unavailable" in out
        assert "ask.entailment_check_skipped" in out  # per-request log still fires, unchanged

    def test_entailment_unavailable_logs_only_once_per_process(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה כלשהי [1]."

        def _raise(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            raise ConnectionError("nothing works tonight")

        monkeypatch.setattr(ollama_client, "chat_structured", _raise)
        ask_grounding.entailment_filter(
            text, rows, max_claims=4, timeout_s=5.0, chain_fallback=True, chain_timeout_s=5.0
        )
        capsys.readouterr()  # drain the first call's output
        ask_grounding.entailment_filter(
            text, rows, max_claims=4, timeout_s=5.0, chain_fallback=True, chain_timeout_s=5.0
        )
        out = capsys.readouterr().out
        assert "ask.entailment_unavailable" not in out  # second call: already logged once
        assert "ask.entailment_check_skipped" in out  # per-request log keeps firing every time


class TestEntailmentRouteWiring:
    def test_route_call_site_passes_chain_fallback_true(self) -> None:
        """`ask_grounding.entailment_filter`'s default is deliberately `chain_fallback=False` (see
        `TestEntailmentChainFallback.test_default_chain_fallback_is_off`) -- the one real call site
        (`routes.ask`) must opt in explicitly or the round-10 fix never actually reaches production."""
        import inspect

        source = inspect.getsource(ask_route.ask)
        assert "chain_fallback=True" in source


# ---------------------------------------------------------------------------------------------
# 2b. End-to-end: the chain fallback actually fires through the live `/api/ask` route
# ---------------------------------------------------------------------------------------------


class _FakeChatResult:
    def __init__(self, content: str = "") -> None:
        self.content = content


class _FakeAskCfg:
    entailment_check = True
    entailment_max_claims = 6


class _FakeSettings:
    ask = _FakeAskCfg()


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _sse_events(body: str) -> list[dict]:
    events = []
    for chunk in body.split("\n\n"):
        line = next((ln for ln in chunk.split("\n") if ln.startswith("data:")), None)
        if not line:
            continue
        events.append(json.loads(line[len("data:") :].strip()))
    return events


def _row(id: int, **kw: Any) -> dict[str, Any]:
    base = {
        "id": id,
        "title": "כותרת",
        "url": "https://example.com",
        "clean_text": "טקסט",
        "summary_he": "תקציר",
        "key_facts": [],
        "level": "yellow",
        "source_name": "מקור",
        "report_kind": None,
        "entities_mentioned": [],
        "_is_context": False,
    }
    base.update(kw)
    return base


class TestEndToEndEntailmentChainFallback:
    def test_local_starved_chain_succeeds_removes_the_flagged_claim(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.llm import ollama_client

        rows = [
            _row(
                1,
                title="XM30 program funding",
                clean_text="The XM30 program is valued at $1.53bn, involving Rheinmetall.",
                entities_mentioned=["Rheinmetall"],
            )
        ]
        chunk = "התוכנית של XM30 מוערכת בכ-1.53 מיליארד דולר [1]."

        monkeypatch.setattr(ask_route, "settings", lambda: _FakeSettings())
        monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: rows)
        monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
        monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter([chunk]))
        monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: _FakeChatResult(""))

        calls: list[str | None] = []

        def _fake_structured(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            provider = kw.get("provider")
            calls.append(provider)
            if provider == "ollama":
                raise ConnectionError("local starved under RAM pressure")
            return ask_grounding._EntailmentResponse(
                verdicts=[ask_grounding._ClaimVerdict(index=1, verdict="no")]
            )

        monkeypatch.setattr(ollama_client, "chat_structured", _fake_structured)

        r = client.post("/api/ask", json={"question": "כמה שווה תוכנית XM30?"})
        events = _sse_events(r.text)
        final = [e for e in events if e["type"] == "answer_final"][-1]
        assert calls == ["ollama", "chain"]  # proves `chain_fallback=True` actually reached the route
        assert final["removed_by_guard"].get("entailment_check") == 1


# ---------------------------------------------------------------------------------------------
# 3. Retrieval cap widens 8 -> 10 for a rich (>= 3 rare-token) question -- finding 3
# ---------------------------------------------------------------------------------------------


class TestRetrievalCapWidensForRichQuestions:
    def test_cap_stays_at_8_for_a_question_with_fewer_than_3_rare_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        items = [_item(i, title=f"Skyranger item {i}", summary_he="תקציר") for i in range(1, 13)]

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            return list(items)

        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: None)
        monkeypatch.setattr(services, "_fetchall", fake_fetchall)
        monkeypatch.setattr(
            services.ollama_client, "embed", lambda texts, **kw: (_ for _ in ()).throw(RuntimeError("x"))
        )

        # "Skyranger" alone -- a single rare token, well under the rich-question threshold.
        out = services.ask_retrieve("מהו ה-Skyranger?", [], [])
        assert len(out) == services._RETRIEVAL_CAP == 8

    def test_cap_widens_to_10_for_a_question_with_3_or_more_rare_tokens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        items = [
            _item(i, title=f"Skyranger Rheinmetall C-UAS item {i}", summary_he="תקציר") for i in range(1, 13)
        ]

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            return list(items)

        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: None)
        monkeypatch.setattr(services, "_fetchall", fake_fetchall)
        monkeypatch.setattr(
            services.ollama_client, "embed", lambda texts, **kw: (_ for _ in ()).throw(RuntimeError("x"))
        )

        # Q5's own live shape: "C-UAS", "UAS", "Skyranger", "Rheinmetall" -- 4 distinct rare tokens.
        question = 'מערכת נגד כטב"ם (C-UAS) -- כיצד משתווה ה-Skyranger של Rheinmetall?'
        assert len(services._rare_tokens(question)) >= services._RETRIEVAL_CAP_RICH_MIN_TOKENS
        out = services.ask_retrieve(question, [], [])
        assert len(out) == services._RETRIEVAL_CAP_RICH == 10

    def test_lower_ranked_item_now_survives_the_wider_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The live finding this closes: item 1353 (a genuine lexical hit, low score) used to fall
        outside the fixed top-8 cut behind several higher-scoring items -- the round-10 cap widens
        just enough to keep it in, without changing anything about how items are *ranked*."""
        # 9 higher-scoring items (2 rare-token title hits each) plus item 1353 (a single, weaker
        # summary-only hit) -- 1353 would rank 10th, inside the widened cap but outside the old one.
        higher = [
            _item(100 + i, title="Skyranger Rheinmetall demo", summary_he="Skyranger Rheinmetall תקציר")
            for i in range(9)
        ]
        weak_hit = _item(
            1353, title="U.S. Air Force seeks anti-aircraft guns", summary_he="Skyranger-35 spec"
        )

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            token = params.get("t", "") if params else ""
            if "1353" in str(token) or "Skyranger" in str(token) or "skyranger" in str(token).lower():
                return [*higher, weak_hit]
            return list(higher)

        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: None)
        monkeypatch.setattr(services, "_fetchall", fake_fetchall)
        monkeypatch.setattr(
            services.ollama_client, "embed", lambda texts, **kw: (_ for _ in ()).throw(RuntimeError("x"))
        )

        question = 'מערכת נגד כטב"ם (C-UAS) -- כיצד משתווה ה-Skyranger של Rheinmetall?'
        out = services.ask_retrieve(question, [], [])
        assert 1353 in [r["id"] for r in out]
        assert len(out) == 10

    def test_rich_query_min_tokens_boundary_is_exactly_3(self) -> None:
        assert services._RETRIEVAL_CAP_RICH_MIN_TOKENS == 3
