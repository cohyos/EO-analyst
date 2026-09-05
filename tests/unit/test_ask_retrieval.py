"""Tests for `eoa.api.services.ask_retrieve` / `ask_build_messages` (U9, docs/REVIEW_2026-09-05.md).

Repro: the user pressed "A" on a feed item about Rheinmetall/GDLS XM30 (attaching it to the chat
context), then asked "מה זה XM30?" -- the answer said no info was found and cited blocked (403)
Safran pages instead. Root causes fixed here:
  1. explicitly-attached context items were fetched with only `clean_text`/`summary_he` and were
     never distinguished from retrieval, so the prompt gave them no priority;
  2. vector-only retrieval could surface quarantined/out-of-scope/empty items (e.g. a 403 fetch);
  3. there was no keyword fallback for rare tokens like "XM30" that a small embedding model blurs.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_ask_retrieval.py -q``
"""

from __future__ import annotations

from typing import Any

import pytest

from eoa.api import services


def _item(
    id: int,
    title: str = "",
    clean_text: str = "",
    summary_he: str = "",
    security_status: str | None = "clean",
    domain: str = "c_uas",
    key_facts: list[str] | None = None,
    url: str = "https://example.com",
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
    }


class TestAskRetrieveContextAlwaysIncluded:
    def test_explicit_context_item_included_even_if_quarantined(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The user attached this item on purpose -- it must never be dropped by a security/scope filter."""
        quarantined = _item(1, title="XM30 item", security_status="quarantined", domain="out_of_scope")

        def fake_fetchone(query: str, params: Any = None) -> dict[str, Any] | None:
            if "FROM items WHERE id" in query:
                return quarantined
            return None

        monkeypatch.setattr(services, "_fetchone", fake_fetchone)
        monkeypatch.setattr(services, "_fetchall", lambda *a, **kw: [])
        monkeypatch.setattr(services.ollama_client, "embed", lambda texts: [[0.1, 0.2]])
        monkeypatch.setattr(services.vector, "nearest", lambda *a, **kw: [])

        out = services.ask_retrieve("מה זה XM30?", [1], [])
        assert len(out) == 1
        assert out[0]["id"] == 1
        assert out[0]["_is_context"] is True

    def test_context_item_survives_even_with_no_matching_entity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        item = _item(7, title="Rheinmetall XM30")
        monkeypatch.setattr(
            services,
            "_fetchone",
            lambda q, p=None: item if "FROM items WHERE id" in q else None,
        )
        monkeypatch.setattr(services, "_fetchall", lambda *a, **kw: [])
        monkeypatch.setattr(services.ollama_client, "embed", lambda texts: (_ for _ in ()).throw(RuntimeError("no embed")))

        out = services.ask_retrieve("מה זה XM30?", [7], [])
        assert [r["id"] for r in out] == [7]


class TestAskRetrieveExcludesUnsafeRetrieval:
    def test_vector_retrieval_excludes_quarantined_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        quarantined = _item(2, title="Safran blocked page", security_status="quarantined")

        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: quarantined if "id = %s" in q else None)
        monkeypatch.setattr(services, "_fetchall", lambda *a, **kw: [])
        monkeypatch.setattr(services.ollama_client, "embed", lambda texts: [[0.1, 0.2]])
        monkeypatch.setattr(services.vector, "nearest", lambda vec, limit=16: [(2, 0.9)])

        out = services.ask_retrieve("מה זה XM30?", [], [])
        assert out == []

    def test_vector_retrieval_excludes_out_of_scope_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        oos = _item(3, title="Unrelated", domain="out_of_scope")
        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: oos if "id = %s" in q else None)
        monkeypatch.setattr(services, "_fetchall", lambda *a, **kw: [])
        monkeypatch.setattr(services.ollama_client, "embed", lambda texts: [[0.1, 0.2]])
        monkeypatch.setattr(services.vector, "nearest", lambda vec, limit=16: [(3, 0.9)])

        out = services.ask_retrieve("מה זה XM30?", [], [])
        assert out == []

    def test_vector_retrieval_excludes_item_with_no_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        empty = _item(4, title="Fetch failed", clean_text="", summary_he="")
        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: empty if "id = %s" in q else None)
        monkeypatch.setattr(services, "_fetchall", lambda *a, **kw: [])
        monkeypatch.setattr(services.ollama_client, "embed", lambda texts: [[0.1, 0.2]])
        monkeypatch.setattr(services.vector, "nearest", lambda vec, limit=16: [(4, 0.9)])

        out = services.ask_retrieve("מה זה XM30?", [], [])
        assert out == []

    def test_vector_retrieval_excludes_unsummarized_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Live-verified 2026-09-05: a Cloudflare challenge page fetched as `clean_text` with
        `security_status='clean'` still has `summary_he=None` because it never reached analyze --
        that gap, not text presence, is what actually distinguishes a fetch failure."""
        unsummarized = _item(6, title="Some item", clean_text="raw text but never analyzed yet", summary_he="")
        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: unsummarized if "id = %s" in q else None)
        monkeypatch.setattr(services, "_fetchall", lambda *a, **kw: [])
        monkeypatch.setattr(services.ollama_client, "embed", lambda texts: [[0.1, 0.2]])
        monkeypatch.setattr(services.vector, "nearest", lambda vec, limit=16: [(6, 0.9)])

        out = services.ask_retrieve("מה קורה?", [], [])
        assert out == []

    def test_vector_retrieval_excludes_cloudflare_challenge_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The exact live U9 finding: Safran press-room fetches that were actually bot-block pages,
        with a summary_he a hypothetical future pipeline change might still populate -- the content
        heuristic is defense in depth on top of the summary_he gate."""
        blocked = _item(
            8,
            title="This website is using a security service to protect itself from online attacks.",
            clean_text="This website is using a security service to protect itself from online attacks...",
            summary_he="תקציר שגוי שהופק בטעות מעמוד חסימה",
        )
        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: blocked if "id = %s" in q else None)
        monkeypatch.setattr(services, "_fetchall", lambda *a, **kw: [])
        monkeypatch.setattr(services.ollama_client, "embed", lambda texts: [[0.1, 0.2]])
        monkeypatch.setattr(services.vector, "nearest", lambda vec, limit=16: [(8, 0.9)])

        out = services.ask_retrieve("מה קורה?", [], [])
        assert out == []

    def test_vector_retrieval_keeps_clean_in_scope_item(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clean = _item(5, title="Elbit item", clean_text="some text about the program", summary_he="תקציר על אלביט")
        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: clean if "id = %s" in q else None)
        monkeypatch.setattr(services, "_fetchall", lambda *a, **kw: [])
        monkeypatch.setattr(services.ollama_client, "embed", lambda texts: [[0.1, 0.2]])
        monkeypatch.setattr(services.vector, "nearest", lambda vec, limit=16: [(5, 0.9)])

        out = services.ask_retrieve("מה קורה?", [], [])
        assert [r["id"] for r in out] == [5]
        assert out[0]["_is_context"] is False


class TestAskRetrieveHybridKeyword:
    def test_rare_token_extraction(self) -> None:
        assert "XM30" in services._rare_tokens("מה זה XM30? ומה לגבי F-35?")
        assert "F-35" in services._rare_tokens("מה זה XM30? ומה לגבי F-35?")
        assert services._rare_tokens("שאלה כללית בלי שום מספר דגם") == []

    def test_keyword_match_surfaces_item_vector_search_missed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """This is the exact U9 mechanism: a small embedding model can rank the right item below
        the top-N neighbours for a rare token like a program name; the ILIKE fallback must still
        find it by an exact keyword hit on the title/body."""
        xm30_item = _item(
            9,
            title="Rheinmetall and GDLS deliver first XM30 prototypes",
            clean_text="XM30 details",
            summary_he="תקציר על XM30",
        )

        def fake_fetchall(query: str, params: Any = None) -> list[dict[str, Any]]:
            if "ILIKE" in query:
                return [xm30_item]
            return []

        monkeypatch.setattr(services, "_fetchone", lambda q, p=None: None)
        monkeypatch.setattr(services, "_fetchall", fake_fetchall)
        # vector search deliberately returns nothing relevant (simulates it missing the item)
        monkeypatch.setattr(services.ollama_client, "embed", lambda texts: [[0.1, 0.2]])
        monkeypatch.setattr(services.vector, "nearest", lambda vec, limit=16: [])

        out = services.ask_retrieve("מה זה XM30?", [], [])
        assert [r["id"] for r in out] == [9]


class TestAskBuildMessages:
    def test_context_items_cited_first_with_full_detail(self) -> None:
        context_item = _item(
            1,
            title="XM30 item",
            summary_he="תקציר על XM30",
            clean_text="X" * 5000,
            key_facts=["עובדה אחת", "עובדה שתיים"],
        )
        context_item["_is_context"] = True
        retrieved_item = _item(2, title="פריט אחר", clean_text="טקסט אחר")
        retrieved_item["_is_context"] = False

        # deliberately pass retrieved-before-context to prove build_messages reorders them
        messages, citations = services.ask_build_messages("מה זה XM30?", [], [retrieved_item, context_item])

        assert citations[0]["item_id"] == 1  # context item cited [1]
        assert citations[1]["item_id"] == 2
        user_msg = messages[-1]["content"]
        assert "הקשר מצורף" in user_msg
        assert "עובדה אחת" in user_msg  # key_facts rendered for the context item
        assert "תקציר על XM30" in user_msg

    def test_system_prompt_instructs_general_knowledge_disclosure(self) -> None:
        messages, _ = services.ask_build_messages("שאלה", [], [])
        system = messages[0]["content"]
        assert "ידע כללי" in system
        assert "הקשר מצורף" in system

    def test_no_retrieved_items_still_produces_valid_messages(self) -> None:
        messages, citations = services.ask_build_messages("שאלה", [], [])
        assert citations == []
        assert "לא נמצאו פריטים רלוונטיים" in messages[-1]["content"]

    def test_history_is_preserved_for_clarification_followups(self) -> None:
        """A clarification follow-up must still see the prior turn in `history`."""
        history = [{"role": "user", "content": "מה זה XM30?"}, {"role": "assistant", "content": "תשובה"}]
        messages, _ = services.ask_build_messages("תסביר יותר", history, [])
        contents = [m["content"] for m in messages]
        assert "מה זה XM30?" in contents
        assert "תשובה" in contents
