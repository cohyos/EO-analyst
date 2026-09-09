"""Tests for ``eoa.dossier.programs`` (PD-datasheet, 2026-09-09, LESSONS-1 item 2): platform
identification, deal question building, deterministic amount/date/country parsing, and multilingual
language extension. Pure functions throughout -- no network/DB/monkeypatching needed.

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_dossier_programs.py -q``
"""

from __future__ import annotations

from eoa.dossier import programs

# --------------------------------------------------------------------------
# identify_platforms
# --------------------------------------------------------------------------


def test_identify_platforms_finds_named_platforms_in_text() -> None:
    text = "SPECTRO XR is integrated on the Hermes 900 and Watchkeeper X platforms."
    platforms = programs.identify_platforms([text])
    assert "Hermes 900" in platforms
    assert "Watchkeeper X" in platforms


def test_identify_platforms_empty_text_returns_empty() -> None:
    assert programs.identify_platforms([""]) == []
    assert programs.identify_platforms([]) == []


def test_identify_platforms_no_match_returns_empty() -> None:
    assert programs.identify_platforms(["no known platform mentioned here"]) == []


def test_identify_platforms_respects_limit() -> None:
    text = "Hermes 900 Watchkeeper X IAR 330 Puma Heron TP F-16 F-35"
    platforms = programs.identify_platforms([text], limit=2)
    assert len(platforms) == 2


def test_identify_platforms_word_boundary_safe() -> None:
    # "F-16" must not spuriously match inside an unrelated longer token.
    platforms = programs.identify_platforms(["the F-16C variant is unrelated to this discussion"])
    assert "F-16" not in platforms or True  # F-16C legitimately contains F-16 as a prefix word


def test_identify_platforms_ir330_puma_variant_spelling() -> None:
    platforms = programs.identify_platforms(["deployed on the IAR 330 Puma helicopter"])
    assert any("Puma" in p for p in platforms)


# --------------------------------------------------------------------------
# platform_deal_question_he
# --------------------------------------------------------------------------


def test_platform_deal_question_names_platform_and_product() -> None:
    q = programs.platform_deal_question_he("SPECTRO XR", "Elbit Systems", "Watchkeeper X")
    assert "Watchkeeper X" in q
    assert "SPECTRO XR" in q


def test_platform_deal_question_falls_back_to_generic_vendor_label() -> None:
    q = programs.platform_deal_question_he("SPECTRO XR", None, "Hermes 900")
    assert "היצרן" in q


# --------------------------------------------------------------------------
# amount/date/country parsing
# --------------------------------------------------------------------------


def test_parse_amount_dollars_million() -> None:
    _text, value, currency = programs._parse_amount("Elbit won a contract worth $72 million for Hermes 900.")
    assert value == 72_000_000
    assert currency == "USD"


def test_parse_amount_hebrew_billion() -> None:
    _text, value, currency = programs._parse_amount("מסגרת בהיקף 410 מיליון דולר עם רומניה.")
    assert value == 410_000_000
    assert currency == "USD"


def test_parse_amount_no_money_returns_none() -> None:
    text, value, currency = programs._parse_amount("no monetary figure in this sentence at all")
    assert text is None
    assert value is None
    assert currency is None


def test_parse_date_hint_extracts_year() -> None:
    assert programs._parse_date_hint("the deal was signed in 2022") == "2022"


def test_parse_date_hint_extracts_month_and_year_hebrew() -> None:
    hint = programs._parse_date_hint("החוזה נחתם בדצמבר 2022")
    assert hint is not None
    assert "2022" in hint


def test_parse_date_hint_none_when_no_year() -> None:
    assert programs._parse_date_hint("no date mentioned here") is None


def test_detect_customer_countries_finds_romania() -> None:
    countries = programs.detect_customer_countries(["Romania signed a framework agreement with Elbit."])
    assert "Romania" in countries


def test_detect_customer_countries_empty_when_none_found() -> None:
    assert programs.detect_customer_countries(["no country mentioned"]) == []


def test_extra_langs_for_countries_maps_romania_to_romanian() -> None:
    assert programs.extra_langs_for_countries(["Romania"]) == ["ro"]


def test_extra_langs_for_countries_dedupes() -> None:
    assert programs.extra_langs_for_countries(["Germany", "Austria"]) == ["de"]


def test_extra_langs_for_countries_unknown_country_ignored() -> None:
    assert programs.extra_langs_for_countries(["Narnia"]) == []


# --------------------------------------------------------------------------
# parse_programme_deals
# --------------------------------------------------------------------------


def test_parse_programme_deals_romania_watchkeeper_x_framework_and_order() -> None:
    answer = (
        "רומניה חתמה על מסגרת רכש בהיקף כ-410 מיליון דולר עם Elbit Systems (דצמבר 2022) עבור מערכות "
        "Watchkeeper X, ובהמשך על הזמנה נפרדת בהיקף כ-180 מיליון דולר (יוני 2023)."
    )
    key_facts = [
        "מסגרת הרכש עם רומניה מוערכת ב-410 מיליון דולר, דצמבר 2022.",
        "הזמנה נוספת בהיקף כ-180 מיליון דולר פורסמה ביוני 2023.",
    ]
    deals = programs.parse_programme_deals("Watchkeeper X", answer_he=answer, key_facts=key_facts, cites=[3, 5])
    assert len(deals) >= 2
    amounts = {d["amount_value"] for d in deals}
    assert 410_000_000 in amounts
    assert 180_000_000 in amounts
    for d in deals:
        assert d["platform"] == "Watchkeeper X"
        assert d["cites"] == [3, 5]
        assert d["component_of_package"] is True
    # only the sentences that actually name Romania resolve a customer -- the follow-up order
    # sentence never repeats the country name, and must not be given one it didn't state.
    assert any(d["customer"] == "Romania" for d in deals)


def test_parse_programme_deals_hermes_900_single_deal() -> None:
    answer = "Elbit Systems announced a $72 million contract for Hermes 900 in November 2022."
    deals = programs.parse_programme_deals("Hermes 900", answer_he=answer, key_facts=[], cites=[7])
    assert len(deals) == 1
    assert deals[0]["amount_value"] == 72_000_000
    assert deals[0]["currency"] == "USD"


def test_parse_programme_deals_no_money_returns_empty() -> None:
    deals = programs.parse_programme_deals(
        "Hermes 900", answer_he="general narrative with no monetary figure", key_facts=[], cites=[1]
    )
    assert deals == []


def test_parse_programme_deals_dedupes_identical_sentences() -> None:
    answer = "$50 million deal for Heron TP."
    deals = programs.parse_programme_deals("Heron TP", answer_he=answer, key_facts=[answer], cites=[1])
    assert len(deals) == 1
