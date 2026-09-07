"""Round 8 (package R8-chat-b) chat fixes (docs/qa/loop/round_7_judge_b.md D5, score 80).

The J7b re-judge sampled 8/8 successful `answer_final` events (delivery itself is fixed) and found
two new, real content defects plus asked for a live-log audit of the round-7 entailment check:

1. **Leaked internal delimiter (2 of 8 sampled answers):** both Q4 (DROIC) and Q6 (AUSA) ended with
   the literal `===SOURCES_JSON===\n"}]}` glued onto otherwise-clean content. Root-caused to
   `_run_citation_repair` (`eoa.api.routes.ask`): its one-shot, non-streamed corrective rewrite
   reuses the exact same system+sources messages the original streamed answer saw -- which carry
   the same `ask_answer_format.md` instructions telling the model to always append the sentinel+
   JSON tail -- but that rewritten text never goes through the streaming loop's own sentinel-split
   logic at all, so a leaked tail sailed straight into `answer_text`/`answer_final` unfiltered. Two
   new pure functions in `eoa.api.routes.ask`, `_split_sources_json` (a single robust, tolerant
   split applied to the complete text -- whitespace/`=`-count/`#`-prefix/code-fence tolerant) and
   `_strip_residual_sources_block` (a last-resort sanitizer), are now applied: once to the fully
   streamed `answer_text`, once to `_run_citation_repair`'s own `corrected` text (the actual live
   root cause), and once more, unconditionally, immediately before `answer_final` is yielded. The
   streaming loop's own exact-literal `_SOURCES_SENTINEL` match is left in place unchanged, as a
   low-latency optimisation only -- it is no longer the *only* line of defence.
2. **Numeric slip:** item 257's own text says the company plans to deliver "seven" additional
   prototypes; the answer said "eight". `eoa.api.ask_grounding.filter_claim_count_mismatch` (new)
   plus `_normalize_spelled_numbers` (English "one".."twenty", Hebrew "אחד".."עשרים", both genders,
   two-word teens) close this: a claimed digit count is compared, noun-phrase by noun-phrase,
   against its own `[n]` citation with any spelled-out source number normalised to digits first --
   an exact noun-phrase match gets the digit corrected in place, a fuzzy-only match (e.g. a plural/
   construct-state variant) gets the whole unit dropped instead, per this round's own brief. Wired
   into both the main guard chain and `_run_removal_guards` (the repair/demotion re-apply helper),
   right after `filter_claim_grounding`.
3. **Entailment check audit:** `runtime/logs/api.2026-09-07.log` shows 5 of 5 sampled real
   `ask.entailment_check` attempts that day all logged `ask.entailment_check_skipped
   reason=timeout_or_error` -- zero successes, zero removals ever observed (100% skip rate).
   Root-caused by reading `entailment_filter`'s own `_call` closure: `chat_structured("light", ...)`
   never passed `interactive=True`, so every attempt queued behind the resource gate's patient
   *batch* budget while the function's own 20s outer wall-clock was always going to expire first --
   fixed in place. Per this round's own brief, the outer timeout default is also raised 20s -> 30s,
   and the claims-per-call cap is lowered 6 -> 4 -- enforced in `eoa.api.routes.ask` via
   `_ENTAILMENT_MAX_CLAIMS_CAP` rather than by lowering `config/config.yaml`'s own
   `ask.entailment_max_claims` value, because that value is pinned `== 6` by
   `tests/unit/test_ask_round7.py`'s `TestAskConfig.test_default_config_values` -- a shared-suite
   test this package does not own and must not edit.

Run with:
``PYTHONPATH=agent PYTHONUTF8=1 .venv\\Scripts\\python -m pytest tests/unit/test_ask_round8.py -q``
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from eoa.api import ask_grounding, services
from eoa.api.routes import ask as ask_route
from eoa.config import settings


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
# 1. _split_sources_json / _strip_residual_sources_block -- unit level
# ---------------------------------------------------------------------------------------------


class TestSplitSourcesJson:
    def test_whole_text_single_chunk_with_valid_json(self) -> None:
        text = (
            'תשובה עם תוכן אנליטי תקין.\n\n===SOURCES_JSON===\n{"source_notes": '
            '[{"n": 1, "note": "רלוונטי"}]}'
        )
        answer, tail = ask_route._split_sources_json(text)
        assert answer == "תשובה עם תוכן אנליטי תקין."
        assert "SOURCES_JSON" not in answer
        notes = ask_route._parse_source_notes(tail)
        assert notes == {1: "רלוונטי"}

    def test_exact_live_found_leak_shape_has_no_valid_json_at_all(self) -> None:
        """The literal live-found artefact (docs/qa/loop/round_7_judge_b.md D5 finding #2):
        `===SOURCES_JSON===\\n"}]}` -- not parseable JSON at all, so the notes dict comes back
        empty, but the sentinel and its garbled tail must still be fully removed from the answer."""
        text = 'תשובה תקינה לגמרי בנושא ה-AUSA.\n===SOURCES_JSON===\n"}]}'
        answer, tail = ask_route._split_sources_json(text)
        assert answer == "תשובה תקינה לגמרי בנושא ה-AUSA."
        assert "SOURCES_JSON" not in answer
        assert ask_route._parse_source_notes(tail) == {}

    def test_delimiter_wrapped_in_a_markdown_code_fence(self) -> None:
        text = 'תוכן תקין.\n```json\n===SOURCES_JSON===\n{"source_notes": [{"n": 1, "note": "הערה"}]}\n```'
        answer, tail = ask_route._split_sources_json(text)
        assert answer == "תוכן תקין."
        assert ask_route._parse_source_notes(tail) == {1: "הערה"}

    def test_delimiter_variant_extra_equals_and_stray_heading_prefix(self) -> None:
        text = 'תוכן.\n### =====SOURCES_JSON=====\n{"source_notes": []}'
        answer, _tail = ask_route._split_sources_json(text)
        assert answer == "תוכן."
        assert "=" not in answer

    def test_no_delimiter_at_all_is_unchanged(self) -> None:
        text = "תשובה רגילה לגמרי בלי שום מחרוזת פנימית."
        answer, tail = ask_route._split_sources_json(text)
        assert answer == text
        assert tail == ""

    def test_offline_replay_of_the_two_live_leaked_answers(self) -> None:
        """Offline replay (docs/qa/loop/round_7_judge_b.md D5 finding #2): Q4 (DROIC) and Q6
        (AUSA) both ended with the literal captured leak `===SOURCES_JSON===\\n"}]}`. Replays both
        raw captured tails against the actual fix function -- no network, no live model call."""
        q4_raw = (
            "### עובדות מרכזיות\n"
            "- ה-DROIC (Digital Read-Out Integrated Circuit) של Leonardo DRS משמש לעיבוד אות "
            "תת-אדום בזמן אמת [1].\n\n"
            '### הערכת האנליסט\nמדובר ברכיב מפתח בשרשרת החיישן.\n===SOURCES_JSON===\n"}]}'
        )
        q6_raw = (
            "תערוכת AUSA 2026 תתקיים באוקטובר בוושינגטון די.סי., ומתמקדת במערכות קרקעיות "
            "מאוישות ובלתי מאוישות [1].\n\n"
            '### פערים / מה לא ידוע\n- לא נמצא פירוט תוכנית הכנס.\n===SOURCES_JSON===\n"}]}'
        )
        for raw in (q4_raw, q6_raw):
            answer, tail = ask_route._split_sources_json(raw)
            assert "SOURCES_JSON" not in answer
            assert '"}]}' not in answer
            assert ask_route._parse_source_notes(tail) == {}
            # last-resort sanitizer must also be a clean no-op on the already-clean result
            assert ask_route._strip_residual_sources_block(answer) == answer


class TestStripResidualSourcesBlock:
    def test_strips_sentinel_and_everything_after_it(self) -> None:
        text = "תוכן נקי.\n===SOURCES_JSON===שאריות מוזרות כלשהן"
        assert ask_route._strip_residual_sources_block(text) == "תוכן נקי."

    def test_no_op_when_no_sentinel_present(self) -> None:
        text = "תוכן תקין לגמרי."
        assert ask_route._strip_residual_sources_block(text) == text


# ---------------------------------------------------------------------------------------------
# 2. filter_claim_count_mismatch / _normalize_spelled_numbers -- unit level
# ---------------------------------------------------------------------------------------------


class TestNormalizeSpelledNumbers:
    def test_english_seven_to_digit(self) -> None:
        text = "The company plans to deliver seven additional prototypes this year."
        assert "7" in ask_grounding._normalize_spelled_numbers(text)
        assert "seven" not in ask_grounding._normalize_spelled_numbers(text).lower()

    def test_hebrew_shiva_to_digit(self) -> None:
        text = "החברה תספק שבעה אב-טיפוסים נוספים השנה."
        normalized = ask_grounding._normalize_spelled_numbers(text)
        assert "7 אב-טיפוסים" in normalized
        assert "שבעה" not in normalized

    def test_english_one_to_twenty_all_normalize(self) -> None:
        text = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty"
        normalized = ask_grounding._normalize_spelled_numbers(text)
        assert normalized == " ".join(str(n) for n in range(1, 21))

    def test_hebrew_bare_units_normalize(self) -> None:
        text = "אחד שתיים שלוש ארבע חמש שש שבע שמונה תשע עשר"
        normalized = ask_grounding._normalize_spelled_numbers(text)
        assert normalized == "1 2 3 4 5 6 7 8 9 10"

    def test_non_number_text_is_unchanged(self) -> None:
        text = "החברה חתמה חוזה עם Rheinmetall בנושא לא קשור למספרים."
        assert ask_grounding._normalize_spelled_numbers(text) == text

    def test_blank_text_is_a_no_op(self) -> None:
        assert ask_grounding._normalize_spelled_numbers("") == ""


class TestFilterClaimCountMismatch:
    def test_live_repro_seven_to_eight_english_source_is_corrected(self) -> None:
        """Live-verified shape (docs/qa/loop/round_7_judge_b.md D5 finding #1): item 257's own
        text says "seven", the claimed answer says "8" -- exact noun-phrase match ("prototypes"
        both sides) so the digit is corrected in place, not the whole sentence dropped."""
        rows = [
            _src(257, "XM30 program", "The company plans to deliver seven additional prototypes this year.")
        ]
        text = "### עובדות מרכזיות\n- החברה מתכננת לספק 8 prototypes נוספים השנה [1]."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 1
        assert "8 prototypes" not in new_text
        assert "7 prototypes" in new_text

    def test_hebrew_shiva_to_eight_is_corrected(self) -> None:
        rows = [_src(1, "Item", "החברה תספק שבעה אב-טיפוסים נוספים השנה.")]
        text = "### עובדות מרכזיות\n- החברה מתכננת לספק 8 אב-טיפוסים נוספים השנה [1]."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 1
        assert "8 אב-טיפוסים" not in new_text
        assert "7 אב-טיפוסים" in new_text

    def test_fuzzy_noun_match_drops_the_unit_instead_of_correcting(self) -> None:
        """A plural/singular mismatch ("prototype" vs "prototypes") is only a fuzzy match --
        per this round's own brief, prefer removal over an unconfident in-place correction."""
        rows = [_src(1, "Item", "The report lists seven prototypes in total.")]
        text = "### עובדות מרכזיות\n- יש 8 prototype חדש בתוכנית [1]."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 1
        assert "8 prototype" not in new_text
        assert "prototype" not in new_text

    def test_matching_count_is_left_alone(self) -> None:
        rows = [_src(1, "Item", "The company plans to deliver eight additional prototypes this year.")]
        text = "### עובדות מרכזיות\n- החברה מתכננת לספק 8 prototypes נוספים השנה [1]."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 0
        assert new_text == text

    def test_no_comparable_count_in_citation_is_unverifiable_and_left_alone(self) -> None:
        rows = [_src(1, "Item", "Unrelated text about something else entirely, no counts here.")]
        text = "### עובדות מרכזיות\n- יש 8 prototypes חדשים בתוכנית [1]."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 0
        assert new_text == text

    def test_uncited_unit_is_left_alone(self) -> None:
        rows = [_src(1, "Item", "The company plans to deliver seven additional prototypes.")]
        text = "### עובדות מרכזיות\n- יש 8 prototypes חדשים ללא כל ציטוט."
        new_text, removed = ask_grounding.filter_claim_count_mismatch(text, rows)
        assert removed == 0
        assert new_text == text

    def test_single_digit_non_money_still_auto_passes_the_older_grounding_guard(self) -> None:
        """Regression guard (task item 2's own "keep the 1-digit non-money auto-pass otherwise"):
        this new guard must not change `_digits_grounded`'s existing behaviour for a lone digit
        with no comparable source count -- `ground_and_filter_answer`'s corpus-wide check still
        auto-passes it, independent of this new, separate, narrower guard."""
        rows = [_src(1, "Item", "Some unrelated text about a different topic entirely.")]
        text = "### עובדות מרכזיות\n- קיימות 3 מערכות דומות בשוק [1]."
        new_text, removed = ask_grounding.ground_and_filter_answer(text, "שאלה", rows)
        assert removed == 0
        assert new_text == text

    def test_no_op_on_empty_retrieved(self) -> None:
        text = "### עובדות מרכזיות\n- יש 8 prototypes [1]."
        assert ask_grounding.filter_claim_count_mismatch(text, []) == (text, 0)

    def test_no_op_on_blank_answer(self) -> None:
        rows = [_src(1, "x", "y")]
        assert ask_grounding.filter_claim_count_mismatch("   ", rows) == ("   ", 0)


# ---------------------------------------------------------------------------------------------
# 3. entailment_filter budget fix -- unit level
# ---------------------------------------------------------------------------------------------


class TestEntailmentBudgetFix:
    def test_default_timeout_raised_to_30_seconds(self) -> None:
        import inspect

        sig = inspect.signature(ask_grounding.entailment_filter)
        assert sig.parameters["timeout_s"].default == 30.0

    def test_call_passes_interactive_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The actual root-cause fix (docs/qa/loop/round_8_fixes.md): without `interactive=True`
        every call queued behind the resource gate's batch budget and skipped on timeout -- 5/5
        sampled live attempts on 2026-09-07. Asserts the wiring directly rather than the resource
        gate's own queueing behaviour (out of this package's scope/file ownership)."""
        from eoa.llm import ollama_client

        rows = [_src(1, "Item", "Supporting source text.")]
        text = "### עובדות מרכזיות\n- טענה תקינה [1]."
        captured: dict[str, Any] = {}

        def _fake(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            captured.update(kw)
            return ask_grounding._EntailmentResponse(verdicts=[])

        monkeypatch.setattr(ollama_client, "chat_structured", _fake)
        ask_grounding.entailment_filter(text, rows, max_claims=4, timeout_s=5.0)
        assert captured.get("interactive") is True


# ---------------------------------------------------------------------------------------------
# 4. AskCfg / config.yaml design-decision guard
# ---------------------------------------------------------------------------------------------


class TestEntailmentClaimsCapDesignDecision:
    def test_effective_cap_constant_is_4(self) -> None:
        assert ask_route._ENTAILMENT_MAX_CLAIMS_CAP == 4

    def test_shipped_config_still_pins_6_for_the_round7_test(self) -> None:
        """docs/qa/loop/round_8_fixes.md's own status note: the raw config value is deliberately
        left at 6 (not lowered to 4) because `tests/unit/test_ask_round7.py`'s
        `TestAskConfig.test_default_config_values` asserts it `== 6` and this package must not
        edit that shared-suite test file -- the effective cap of 4 is enforced at the call site
        in `eoa.api.routes.ask` instead (see the constant test above)."""
        from pathlib import Path

        import yaml

        shipped = yaml.safe_load(Path("config/config.yaml").read_text(encoding="utf-8"))["ask"]
        assert shipped["entailment_max_claims"] == 6
        assert settings().ask.entailment_max_claims == 6


# ---------------------------------------------------------------------------------------------
# 5. End-to-end SSE wiring
# ---------------------------------------------------------------------------------------------


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


class _FakeChatResult:
    def __init__(self, content: str = "") -> None:
        self.content = content


def _mock_ask_full(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    chunks: list[str],
    *,
    question: str,
    repair_content: str = "",
) -> str:
    from eoa.llm import ollama_client

    monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: rows)
    monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
    monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: iter(chunks))
    monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: _FakeChatResult(repair_content))
    return question


class TestEndToEndSentinelLeak:
    def test_whole_response_single_chunk_no_leak_reaches_answer_final(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The round-7b judge's own diagnosis: a cloud-chain provider can return the entire
        completion as a single chunk. Simulated here by handing `chat_stream` exactly one chunk
        already containing the sentinel + JSON tail."""
        rows = [_row(1, title="Item", clean_text="Rheinmetall Group signed a new deal.")]
        chunk = (
            'תוכן תקין עם ציטוט [1].\n\n===SOURCES_JSON===\n{"source_notes": [{"n": 1, "note": "רלוונטי"}]}'
        )
        question = _mock_ask_full(monkeypatch, rows, [chunk], question="מה קורה?")
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        final = [e for e in events if e["type"] == "answer_final"][-1]
        assert "SOURCES_JSON" not in final["text"]
        sources_event = [e for e in events if e["type"] == "sources"][-1]
        assert sources_event["items"][0]["note"] == "רלוונטי"

    def test_delimiter_split_across_two_streamed_chunks(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The delimiter itself split mid-token across two separate `chat_stream` chunks -- the
        existing streaming-time hold-back (kept unchanged as an optimisation, see `ask.py`'s own
        U11 note) must still catch this."""
        rows = [_row(1, title="Item", clean_text="Rheinmetall Group signed a new deal.")]
        part_a = "תוכן תקין [1].\n\n===SOURCES_"
        part_b = 'JSON===\n{"source_notes": [{"n": 1, "note": "טוב"}]}'
        question = _mock_ask_full(monkeypatch, rows, [part_a, part_b], question="מה קורה?")
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        final = [e for e in events if e["type"] == "answer_final"][-1]
        assert "SOURCES_JSON" not in final["text"]
        assert final["text"].rstrip().endswith("תוכן תקין [1].")

    def test_citation_repair_leak_is_the_actual_live_root_cause(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The concrete root cause found while implementing this round's fix: the streamed answer
        has zero `[n]` citations, triggering `_run_citation_repair`'s one-shot rewrite -- and that
        rewrite's own raw output ends with the literal live-captured leak shape
        `===SOURCES_JSON===\\n"}]}` (docs/qa/loop/round_7_judge_b.md D5 finding #2), which never
        passed through the streaming loop's own sentinel split at all before this round's fix."""
        rows = [
            _row(
                1,
                title="Rheinmetall deal",
                clean_text="Rheinmetall Group signed a new deal.",
                entities_mentioned=["Rheinmetall"],
            )
        ]
        # No [n] anywhere -- forces the zero-citation repair branch.
        chunk = "תוכן ראשוני ללא שום ציטוט כלל."
        repaired = 'Rheinmetall חתמה חוזה חדש [1].\n===SOURCES_JSON===\n"}]}'
        question = _mock_ask_full(
            monkeypatch, rows, [chunk], question="מה קורה עם Rheinmetall?", repair_content=repaired
        )
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        final = [e for e in events if e["type"] == "answer_final"][-1]
        assert "SOURCES_JSON" not in final["text"]
        assert '"}]}' not in final["text"]
        assert "Rheinmetall חתמה חוזה חדש" in final["text"]
        assert events[-1]["type"] == "done"

    def test_no_delimiter_at_all_end_to_end_is_unaffected(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [_row(1, title="Item", clean_text="Rheinmetall Group signed a new deal.")]
        chunk = "Rheinmetall חתמה חוזה חדש [1] ובלי שום מחרוזת פנימית."
        question = _mock_ask_full(monkeypatch, rows, [chunk], question="מה קורה עם Rheinmetall?")
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        final = [e for e in events if e["type"] == "answer_final"][-1]
        assert final["text"] == chunk
        sources_event = [e for e in events if e["type"] == "sources"][-1]
        assert sources_event["items"][0]["note"] is None


class TestEndToEndCountMismatch:
    def test_count_mismatch_corrected_end_to_end(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [
            _row(
                257,
                title="XM30 program",
                clean_text="The company plans to deliver seven additional prototypes this year.",
                entities_mentioned=["XM30"],
            )
        ]
        chunk = "### עובדות מרכזיות\n- החברה מתכננת לספק 8 prototypes נוספים השנה [1]."
        question = _mock_ask_full(monkeypatch, rows, [chunk], question="מה מצב תוכנית ה-XM30?")
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        final = [e for e in events if e["type"] == "answer_final"][-1]
        assert final["removed_by_guard"].get("count_mismatch") == 1
        assert "8 prototypes" not in final["text"]
        assert "7 prototypes" in final["text"]
        assert events[-1]["type"] == "done"


class TestEndToEndEntailmentCap:
    def test_entailment_call_capped_at_4_claims_even_with_config_value_6(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.llm import ollama_client

        cfg = settings().ask
        monkeypatch.setattr(cfg, "entailment_check", True, raising=False)
        assert cfg.entailment_max_claims == 6  # the shipped/pinned raw config value, unchanged

        captured: dict[str, Any] = {}

        def _fake_structured(role: str, schema: Any, messages: list[dict[str, Any]], **kw: Any) -> Any:
            captured["messages"] = messages
            captured["kwargs"] = kw
            return ask_grounding._EntailmentResponse(verdicts=[])

        monkeypatch.setattr(ollama_client, "chat_structured", _fake_structured)

        rows = [_row(1, title="Item", clean_text="Rheinmetall Group signed a new deal about many things.")]
        bullets = "\n".join(f"- טענה מספר {i} [1]." for i in range(1, 9))
        chunk = f"### עובדות מרכזיות\n{bullets}"
        question = _mock_ask_full(monkeypatch, rows, [chunk], question="מה קורה?")
        r = client.post("/api/ask", json={"question": question})
        events = _sse_events(r.text)
        assert events[-1]["type"] == "done"

        assert "messages" in captured, "chat_structured must have been called (entailment_check=True)"
        assert captured["kwargs"].get("interactive") is True
        user_content = captured["messages"][1]["content"]
        claim_count = sum(user_content.count(f"טענה {i}:") for i in range(1, 9))
        assert claim_count == 4  # capped, not the raw config value of 6
