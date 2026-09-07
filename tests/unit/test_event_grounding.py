"""Unit tests for round-14 (2026-09-07): the events-stage grounding guard
(``eoa.pipeline.event_grounding``) and the two ``eoa.report.bd_territory`` functions it feeds
(``collect_platform_events``/``platform_events_table``) -- both added/fixed after
docs/qa/content_review/CR-factcheck.md's independent fact-check found events 22/23 (item 81) and a
self-contradictory "buyer=supplier=Israel" platform row (item 90) among other fabrications.

Run with:
    PYTHONPATH=agent PYTHONUTF8=1 .venv/Scripts/python.exe -m pytest tests/unit/test_event_grounding.py -q
"""

from __future__ import annotations

import datetime as dt

import pytest

from eoa.pipeline import event_grounding as eg

# --------------------------------------------------------------------------
# fixtures -- real source excerpts for items 81 (Anduril/Norkin), 90 (Greece air-defense deal),
# 114 (Elbit backlog), trimmed to the sentences the module actually needs (the live items carry a
# lot of unrelated ad-script/boilerplate noise this module already has to be robust against, see
# ITEM_81's own "collaborations, investments, and acquisitions" sentence below).
# --------------------------------------------------------------------------

ITEM_81 = {
    "id": 81,
    "title": "Anduril to appoint Amiram Norkin to head Israel activity",
    "clean_text": (
        "US defense-tech giant Anduril has chosen former Israel Air Force Commander Gen. (res.) "
        "Amikam Norkin as head of the company's operations in Israel, \"Globes\" has learned. "
        "The appointment, which has not yet been officially announced, concludes a process that "
        "has been ongoing for the past few months. At the end of June, Anduril cofounder and CEO "
        "Brian Schimpf visited Israel for meetings with candidates to lead Israel operations. He "
        "also met with senior officials at the Ministry of Defense Ministry and its Directorate "
        "for Defense R&D (MAFAT) as well as senior executives of Israeli defense companies -- "
        "the company's senior officials also met with Elbit, Rafael, and other executives, and "
        "examined possible collaborations, investments, and acquisitions.\n"
        "One of the collaborations that has already been reported is with Elbit Systems. The "
        "companies are working together to offer the US Army the Sigma 155 howitzer system, "
        "which is already operative in the IDF. A partnership with a large Israeli company "
        "provides Anduril with operational experience.\n"
        "The appointment comes as Anduril is conducting another financing round, which could "
        "become one of the largest private equity rounds ever seen in a defense-tech company. "
        "According to information received by \"Globes,\" the size of the round has reached "
        "about $10 billion, based on a company valuation of about $100 billion. However, the "
        "round has not yet closed.\n"
        "For the Israeli defense-tech market, Anduril's entry could be both an opportunity and "
        "a competitive threat."
    ),
    "raw_text": "",
}

ITEM_90 = {
    "id": 90,
    "title": "Updated 13.30",
    "clean_text": (
        "Ministry of Defense director general Gen. (res.) Amir Baram today signed an historic "
        "agreement with Greece in Tel Aviv for the sale of three air defense systems for "
        "€3.1 billion ($3.6 billion). The deal will surpass the $3.5 billion paid by Germany "
        "for a single Arrow 3 long-range air defense system.\n"
        "The air defense systems being supplied by Israel, known in Greece as the \"Achilles "
        "Shield,\" is one of the largest defense deals in the country's history.\n"
        "A pair of Greek Air Force F-16s were scrambled last Friday to repel a Turkish drone "
        "operating in the area of the islands of Samothraki and Lemnos."
    ),
    "raw_text": "",
}

ITEM_114 = {
    "id": 114,
    "title": "Elbit Systems beats analysts, backlog reaches new peak",
    "clean_text": (
        "Defense company Elbit Systems reports a new peak in its orders backlog, which reached "
        "$32 billion at the end of the second quarter. At the end of 2025 it was $28 billion.\n"
        "Elbit Systems' second quarter revenue was $2.29 billion, 16% more than in the "
        "corresponding quarter of 2025."
    ),
    "raw_text": "",
}

THIN_ITEM = {"id": 999, "title": "Rafael sells plant", "clean_text": "", "raw_text": ""}


def _ev(**kw):
    base = {
        "kind": "other", "title": "t", "date": None, "amount_usd": None, "currency": None,
        "parties": [], "customer": None, "program": None, "summary_he": "", "confidence": 0.8,
    }  # fmt: skip
    base.update(kw)
    return base


# --------------------------------------------------------------------------
# rule (a): amount/currency grounding, valuation-context detection
# --------------------------------------------------------------------------


def test_event_22_appointment_amount_and_customer_dropped_kind_reclassified():
    """The headline fabrication this round exists to fix: a personnel-appointment article turned
    into a fabricated $10B m_and_a event against a fictitious IMOD/IDF counterparty."""
    ev = _ev(
        kind="m_and_a",
        title="מינוי אמיתי נורקין לראש פעילות אנדוריל בישראל",
        amount_usd=10_000_000_000,
        currency="USD",
        parties=["Anduril", "Amikam Norkin"],
        customer="Israel Ministry of Defense and IDF",
        summary_he="אנדוריל בחרה בגנרל אמיתי נורקין לתפקיד ראש הפעילות של החברה בישראל.",
        confidence=0.95,
    )
    g = eg.ground_event(ITEM_81, ev)
    assert g.kind == "appointment"
    assert g.amount_usd is None
    assert g.currency is None
    assert g.customer is None
    assert g.parties == ["Anduril", "Amikam Norkin"]  # both literally in the source -- kept
    assert g.confidence == 0.5  # capped: fields were dropped
    assert "amount_usd" in g.dropped_fields
    assert "customer" in g.dropped_fields


def test_event_23_investment_round_amount_kept_kind_reclassified_not_valuation():
    """The *same* $10B figure, this time describing a real (if still-open) financing round --
    grounded and kept, distinguished from the $100B *valuation* mentioned one clause later by the
    before-only valuation-context window (see event_grounding._amount_grounded)."""
    ev = _ev(
        kind="m_and_a",
        title="סבב גיוס הון חדש של אנדוריל",
        amount_usd=10_000_000_000,
        currency="USD",
        parties=["Anduril"],
        summary_he="אנדוריל נמצאת בתהליך גיוס הון חדש בגובה של כ-10 מיליארד דולר.",
        confidence=0.7,
    )
    g = eg.ground_event(ITEM_81, ev)
    assert g.kind == "investment"
    assert g.amount_usd == 10_000_000_000  # real digits in the source -- not dropped
    assert g.confidence == 0.7  # unchanged: a kind reclassification alone never caps confidence
    assert g.dropped_fields == []


def test_valuation_figure_is_dropped_even_though_digits_are_grounded():
    """The $100B *valuation* figure, isolated: grounded digits (they're really in the text) but
    immediately preceded by "based on a company valuation of about" -- must be dropped."""
    ev = _ev(kind="investment", title="שווי אנדוריל", amount_usd=100_000_000_000, currency="USD")
    g = eg.ground_event(ITEM_81, ev)
    assert g.amount_usd is None
    assert "amount_usd" in g.dropped_fields
    assert "valuation" in g.changes[-1].evidence.lower() or "שווי" in g.changes[-1].evidence


def test_amount_ungrounded_when_not_in_source_at_any_magnitude():
    ev = _ev(kind="contract_award", title="x", amount_usd=999_000_000, currency="USD")
    g = eg.ground_event(ITEM_90, ev)
    assert g.amount_usd is None


def test_greece_deal_amount_grounded_and_not_flagged_as_valuation():
    ev = _ev(kind="deployment", title="מכירת שלושה מערכות הגנה אווירית ליוון", amount_usd=3_600_000_000, currency="USD")
    g = eg.ground_event(ITEM_90, ev)
    assert g.amount_usd == 3_600_000_000
    assert g.dropped_fields == []


def test_elbit_backlog_amount_grounded_and_kind_reclassified_to_financial_results():
    ev = _ev(
        kind="regulation",
        title="דיווח על שיא במלאי ההזמנות",
        amount_usd=32_000_000_000,
        currency="USD",
        parties=["Elbit"],
        summary_he="אלביט מערכות דיווחה על שיא חדש במלאי ההזמנות שלה, שהגיע ל-32 מיליארד דולר.",
    )
    g = eg.ground_event(ITEM_114, ev)
    assert g.kind == "financial_results"
    assert g.amount_usd == 32_000_000_000
    assert g.kind_changed_from == "regulation"


# --------------------------------------------------------------------------
# rule (b): party/customer grounding + placeholder normalization
# --------------------------------------------------------------------------


def test_ungrounded_customer_dropped_and_confidence_capped():
    ev = _ev(kind="other", title="כניסת אנדוריל לשוק", customer="Israel Ministry of Defense and IDF", confidence=0.8)
    g = eg.ground_event(ITEM_81, ev)
    assert g.customer is None
    assert g.confidence == 0.5


def test_grounded_customer_via_literal_substring_kept():
    ev = _ev(kind="deployment", title="x", customer="Greece", confidence=0.9)
    g = eg.ground_event(ITEM_90, ev)
    assert g.customer == "Greece"
    assert g.confidence == 0.9


@pytest.mark.parametrize("placeholder", ["לא צוין", "not specified", "N/A", "unknown"])
def test_placeholder_customer_normalized_to_none_without_confidence_penalty(placeholder):
    ev = _ev(kind="contract_award", title="x", customer=placeholder, confidence=0.9)
    g = eg.ground_event(ITEM_90, ev)
    assert g.customer is None
    assert g.confidence == 0.9  # placeholder normalization is not a "drop" -- never penalized
    assert g.dropped_fields == []


def test_ungrounded_party_dropped_grounded_party_kept():
    ev = _ev(kind="deployment", title="x", parties=["Israel", "Fictional Corp"])
    g = eg.ground_event(ITEM_90, ev)
    assert g.parties == ["Israel"]
    assert any("Fictional Corp" in d for d in g.dropped_fields)


# --------------------------------------------------------------------------
# rule (c): kind reclassification, scoped to the event's own text
# --------------------------------------------------------------------------


def test_partnership_reclassified_from_m_and_a():
    ev = _ev(
        kind="m_and_a",
        title="שיתוף פעולה בין אנדוריל לאלביט מערכות",
        parties=["Anduril", "Elbit", "Elbit Systems"],
        summary_he="אנדוריל ואלביט מערכות עובדות יחד על הצעת מערכת התותח Sigma 155 לצבא ארה״ב.",
    )
    g = eg.ground_event(ITEM_81, ev)
    assert g.kind == "partnership"


def test_m_and_a_kind_reclassification_scoped_to_event_own_text_not_full_corpus():
    """ITEM_81's *corpus* contains the word "acquisitions" (an unrelated sentence about Anduril's
    general market exploration) -- a corpus-wide vocabulary check would wrongly let a fabricated
    'm_and_a' event survive just because that word appears somewhere else in the article. The
    event's own text (title/summary_he/program) is what's actually checked."""
    ev = _ev(
        kind="m_and_a",
        title="מינוי אמיתי נורקין לראש פעילות אנדוריל בישראל",
        summary_he="אנדוריל בחרה בגנרל אמיתי נורקין לתפקיד ראש הפעילות של החברה בישראל.",
    )
    g = eg.ground_event(ITEM_81, ev)
    assert g.kind != "m_and_a"  # reclassified despite "acquisitions" existing elsewhere in corpus
    assert g.kind == "appointment"


def test_m_and_a_with_real_acquisition_vocabulary_is_kept():
    ev = _ev(kind="m_and_a", title="x", summary_he="Company X agreed to acquire Company Y in a merger deal.")
    g = eg.ground_event(THIN_ITEM | {"clean_text": "Company X agreed to acquire Company Y."}, ev)
    assert g.kind == "m_and_a"


# --------------------------------------------------------------------------
# rule (d): title-only / empty-body items
# --------------------------------------------------------------------------


def test_thin_source_item_cannot_carry_amount_or_customer():
    ev = _ev(kind="deployment", title="x", amount_usd=500_000_000, currency="USD", customer="Rafael")
    g = eg.ground_event(THIN_ITEM, ev)
    assert g.amount_usd is None
    assert g.customer is None
    assert g.confidence is not None and g.confidence <= 0.5


# --------------------------------------------------------------------------
# rule (e): confidence cap
# --------------------------------------------------------------------------


def test_confidence_uncapped_when_nothing_dropped():
    ev = _ev(kind="deployment", title="x", confidence=0.95)
    g = eg.ground_event(ITEM_90, ev)
    assert g.confidence == 0.95


def test_confidence_capped_at_half_when_any_field_dropped():
    ev = _ev(kind="deployment", title="x", customer="Fictional Buyer", confidence=0.95)
    g = eg.ground_event(ITEM_90, ev)
    assert g.confidence == 0.5


# --------------------------------------------------------------------------
# EventOut-like object input (analyze.py wiring shape)
# --------------------------------------------------------------------------


class _FakeEventOut:
    def __init__(self, **kw):
        self.kind = kw.get("kind", "other")
        self.title = kw.get("title", "x")
        self.date = kw.get("date")
        self.amount_usd = kw.get("amount_usd")
        self.currency = kw.get("currency")
        self.parties = kw.get("parties", [])
        self.customer = kw.get("customer")
        self.program = kw.get("program")
        self.summary_he = kw.get("summary_he", "")
        self.confidence = kw.get("confidence", 0.8)


def test_ground_event_accepts_eventout_shaped_object():
    obj = _FakeEventOut(kind="deployment", title="x", customer="Greece")
    g = eg.ground_event(ITEM_90, obj)
    assert g.customer == "Greece"


# --------------------------------------------------------------------------
# reconcile_events: cross-source merge (task rule 2)
# --------------------------------------------------------------------------


def test_reconcile_events_merges_exact_cross_source_duplicate():
    """Events 54/107: the same $270M Elbit SPECTRO/ISR contract, reported by two outlets one day
    apart -- same amount/currency, so no reconciliation-range note is needed."""
    e1 = {
        "id": 54, "item_id": 321, "kind": "contract_award", "title": "זכייה בחוזה", "date": dt.date(2026, 9, 1),
        "amount_usd": 270_000_000, "currency": "USD", "parties": ["Elbit"], "customer": None,
        "program": None, "summary_he": "s1", "confidence": 0.81,
    }  # fmt: skip
    e2 = {
        "id": 107, "item_id": 93, "kind": "contract_award", "title": "אלביט זכתה בחוזה", "date": dt.date(2026, 9, 2),
        "amount_usd": 270_000_000, "currency": "USD", "parties": ["Elbit"], "customer": None,
        "program": None, "summary_he": "s2", "confidence": 0.9,
    }  # fmt: skip
    clusters = eg.reconcile_events([e1, e2])
    merged = [c for c in clusters if c.member_ids]
    assert len(merged) == 1
    # both rows are equally "rich" (same non-null field count) -- ties break to the lower/earlier id
    assert merged[0].keep["id"] == 54
    assert merged[0].member_ids == [107]
    assert merged[0].merged_item_ids == [93]
    assert merged[0].amounts_reconciled is False


def test_reconcile_events_greece_deal_keeps_amount_range_note_when_amounts_differ():
    e1 = {
        "id": 25, "item_id": 90, "kind": "deployment", "title": "מכירת שלושה מערכות", "date": None,
        "amount_usd": 3_600_000_000, "currency": "USD", "parties": ["Israel", "Greece"], "customer": None,
        "program": None, "summary_he": "s1", "confidence": 0.95,
    }  # fmt: skip
    e2 = {
        "id": 74, "item_id": 150, "kind": "deployment", "title": "עסקת הגנה אווירית", "date": None,
        "amount_usd": 3_500_000_000, "currency": "EUR", "parties": ["Rafael"], "customer": "Greece",
        "program": "Achilles Shield", "summary_he": "s2", "confidence": 0.8,
    }  # fmt: skip
    clusters = eg.reconcile_events([e1, e2])
    merged = [c for c in clusters if c.member_ids]
    assert len(merged) == 1
    assert merged[0].amounts_reconciled is True
    assert "סכומים שונים בין המקורות" in merged[0].keep["summary_he"]
    assert merged[0].keep["confidence"] <= 0.6


def test_reconcile_events_does_not_merge_unrelated_deals_sharing_one_common_vendor():
    """False-merge case found live while testing this module: two unrelated contracts (different
    buyers) that happen to share the single common vendor name "Leonardo" and land two days apart
    by coincidence, with no amount on either side to corroborate -- must not merge."""
    e1 = {
        "id": 61, "item_id": 303, "kind": "contract_award", "title": "Leonardo DRS wins Space Force contract",
        "date": dt.date(2026, 9, 2), "amount_usd": None, "currency": None,
        "parties": ["Leonardo DRS", "US Space Force", "Leonardo"], "customer": "US Space Force",
        "program": None, "summary_he": "s1", "confidence": 0.8,
    }  # fmt: skip
    e2 = {
        "id": 124, "item_id": 815, "kind": "contract_award", "title": "Leonardo Centauro II Brazil",
        "date": dt.date(2026, 9, 4), "amount_usd": None, "currency": None,
        "parties": ["Leonardo", "Brazilian Army"], "customer": "Brazilian Army",
        "program": None, "summary_he": "s2", "confidence": 0.8,
    }  # fmt: skip
    clusters = eg.reconcile_events([e1, e2])
    assert all(not c.member_ids for c in clusters)


def test_reconcile_events_does_not_merge_distinct_same_item_facts():
    """Two genuinely distinct facts on the same item/kind (two different satellites) must not be
    collapsed into one row -- gated on eoa.memory.relational.same_kind_duplicate's own
    distinct-numbers check."""
    e1 = {
        "id": 237, "item_id": 101, "kind": "launch", "title": "שיגור לוויין דור 1", "date": None,
        "amount_usd": None, "currency": None, "parties": ["Israel"], "customer": None,
        "program": None, "summary_he": "s1", "confidence": 0.8,
    }  # fmt: skip
    e2 = {
        "id": 238, "item_id": 101, "kind": "launch", "title": "שיגור לוויין אופק 19", "date": None,
        "amount_usd": None, "currency": None, "parties": ["Israel"], "customer": None,
        "program": None, "summary_he": "s2", "confidence": 0.8,
    }  # fmt: skip
    clusters = eg.reconcile_events([e1, e2])
    assert all(not c.member_ids for c in clusters)


# --------------------------------------------------------------------------
# bd_territory.collect_platform_events / platform_events_table (task rule 3)
# --------------------------------------------------------------------------


def test_collect_platform_events_does_not_infer_platform_from_unrelated_body_mention(monkeypatch):
    """item 90's clean_text mentions "F-16" once, in an unrelated sentence about a Turkish drone
    incident -- the old full-clean_text match wrongly attached the "מטוס קרב" (fighter jet)
    platform category to the air-defense-sale event. The match text is now the event's own
    title/summary/program + the item headline only."""
    from eoa.report import bd_territory as bdt

    row = {
        "id": 25, "item_id": 90, "kind": "deployment", "title": "מכירת שלושה מערכות הגנה אווירית ליוון",
        "date": None, "amount_usd": 3_600_000_000, "currency": "USD", "parties": ["Israel", "Greece"],
        "customer": None, "program": None, "summary_he": None,
        "item_url": "https://example.com/90", "item_title": ITEM_90["title"], "published_at": None,
        "geography": "GR", "clean_text": ITEM_90["clean_text"], "source_name": "Globes",
    }  # fmt: skip
    monkeypatch.setattr(bdt, "_fetchall", lambda *a, **k: [row])

    class _FakeSpec:
        def matches(self, text):
            return "f-16" in text.lower() or "fighter" in text.lower()

        category_he = "מטוס קרב"
        payload_need_he = "פוד כיוון (Targeting Pod)"

    import eoa.tenders.forecast as forecast_mod

    monkeypatch.setattr(forecast_mod, "load_platform_payloads", lambda *a, **k: [_FakeSpec()])

    out = bdt.collect_platform_events("GR", dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert len(out) == 1
    assert out[0]["platform_he"] != "מטוס קרב"
    assert out[0]["platform_he"] == "מכירת שלושה מערכות הגנה אווירית ליוון"


def test_collect_platform_events_buyer_never_equals_vendor(monkeypatch):
    """The old ``buyer = customer or parties[0]; vendor = first party != customer`` derivation
    produced buyer == vendor == "Israel" whenever customer was null (parties[0] != None is always
    true). A buyer is now only ever the event's own grounded customer -- never guessed from
    parties -- and is "לא ידוע" when missing."""
    from eoa.report import bd_territory as bdt

    row = {
        "id": 25, "item_id": 90, "kind": "deployment", "title": "x", "date": None,
        "amount_usd": 3_600_000_000, "currency": "USD", "parties": ["Israel", "Greece"], "customer": None,
        "program": None, "summary_he": None, "item_url": "u", "item_title": "t", "published_at": None,
        "geography": "GR", "clean_text": "", "source_name": "s",
    }  # fmt: skip
    monkeypatch.setattr(bdt, "_fetchall", lambda *a, **k: [row])
    import eoa.tenders.forecast as forecast_mod

    monkeypatch.setattr(forecast_mod, "load_platform_payloads", lambda *a, **k: [])

    out = bdt.collect_platform_events("GR", dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert len(out) == 1
    assert out[0]["buyer"] != out[0]["vendor"]
    assert out[0]["buyer"] == "לא ידוע"


def test_collect_platform_events_grounded_customer_becomes_buyer(monkeypatch):
    from eoa.report import bd_territory as bdt

    row = {
        "id": 74, "item_id": 150, "kind": "deployment", "title": "x", "date": None,
        "amount_usd": 3_500_000_000, "currency": "EUR", "parties": ["Rafael"], "customer": "Greece",
        "program": "Achilles Shield", "summary_he": None, "item_url": "u", "item_title": "t",
        "published_at": None, "geography": "GR", "clean_text": "", "source_name": "s",
    }  # fmt: skip
    monkeypatch.setattr(bdt, "_fetchall", lambda *a, **k: [row])
    import eoa.tenders.forecast as forecast_mod

    monkeypatch.setattr(forecast_mod, "load_platform_payloads", lambda *a, **k: [])

    out = bdt.collect_platform_events("GR", dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert out[0]["buyer"] == "Greece"
    assert out[0]["vendor"] == "Rafael"


def test_valuation_cue_right_after_the_figure_is_a_valuation_but_a_later_cue_is_not():
    """Lead follow-up (round 14): live event 19's item title "XTEND starts trading on NYSE this week
    at $1.5b valuation" carries the cue AFTER the figure; live event 23's "$10 billion, based on a
    company valuation of about $100 billion" carries a cue ~30 chars later that belongs to the
    NEXT figure and must not poison the real round size."""
    from eoa.pipeline.event_grounding import _amount_grounded

    grounded, is_val, _ = _amount_grounded(1.5e9, "XTEND starts trading on NYSE this week at $1.5b valuation")
    assert grounded and is_val
    grounded, is_val, _ = _amount_grounded(
        1e10, "the size of the round has reached about $10 billion, based on a company valuation of about $100 billion"
    )
    assert grounded and not is_val
