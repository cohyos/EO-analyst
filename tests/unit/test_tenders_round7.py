"""R7-tenders (docs/qa/loop/round_6_judge.md D9 finding 2, plus the finding-1 SAM.gov key-gating
piece) -- pure logic + parsing tests, no DB/LLM/live network (every DB-touching function here is
monkeypatched, mirroring tests/unit/test_tenders_scan.py's own stubbing style).

Covers:
  - the word-boundary fix for short keywords/procurement signals (``_term_present``) -- the
    concrete round-6 bug: the EO/IR domain keyword "ATR" matching inside the unrelated Dutch word
    "privaatrechtelijke" in candidate id 34 ("Expert / Coach Transformatie en Contracten Juridisch",
    Gemeente Rotterdam);
  - the CPV-code-family pre-filter (``_cpv_gate_reject_reason``, wired into ``_gate_reject_reason``);
  - ``cpv_naics`` extraction in the TED / Contracts Finder / generic OCDS / generic JSON-list parsers;
  - dynamic key-gated ``api_json`` source enabling (``_api_json_source_enabled``) and the
    ``{api_key}`` substitution in ``_fetch_api_json`` (finding 1c, SAM.gov);
  - the repair path (``find_prefilter_violations`` / ``repair_relevance_prefilter``).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from eoa.tenders.scan import (
    DEFAULT_CPV_ALLOW_PREFIXES,
    DEFAULT_CPV_DENY_PREFIXES,
    NoticeRaw,
    PrefilterViolation,
    TenderSource,
    _api_json_source_enabled,
    _cpv_gate_reject_reason,
    _fetch_api_json,
    _gate_reject_reason,
    _has_procurement_signal,
    _matches_keywords,
    _parse_contracts_finder,
    _parse_generic_json_list,
    _parse_generic_ocds,
    _parse_ted_notices,
    _term_present,
    find_prefilter_violations,
    load_cpv_allow_prefixes,
    load_cpv_deny_prefixes,
    repair_relevance_prefilter,
)

DOMAIN_KEYWORDS = [
    "electro-optical",
    "infrared",
    "targeting pod",
    "seeker",
    "ATR",
]
PROCUREMENT_SIGNALS = ["tender", "RFP", "RFI", "RFQ", "sources sought", "מכרז"]

# The exact Dutch text from round-6 D9's candidate id 34 that produced the false "ATR" match --
# "privaatrechtelijke" ("under private law") contains "atr" as a mid-word fragment.
_ROTTERDAM_TITLE = "Expert / Coach Transformatie en Contracten Juridisch - cluster SO"
_ROTTERDAM_SUMMARY = (
    "Als juridisch expert/coach kijk je hoe je invloed kan uitoefenen via de privaatrechtelijke "
    "weg; via de contracten van vastgoed van derden."
)


def _notice(**overrides) -> NoticeRaw:
    base = dict(source_id="x", external_ref="x:1", title="", summary="")
    base.update(overrides)
    return NoticeRaw(**base)


def _ted_source(**overrides) -> TenderSource:
    base = dict(
        id="ted_eu",
        name="TED",
        kind="api_json",
        country="EU",
        url="https://api.ted.europa.eu/v3/notices/search",
        method="POST",
        query_template='{"query":"FT ~ \\"{keyword}\\"","fields":["ND","TI","PD"],"limit":20}',
        keywords=DOMAIN_KEYWORDS,
        verified=True,
    )
    base.update(overrides)
    return TenderSource(**base)


def _cf_source(**overrides) -> TenderSource:
    base = dict(
        id="uk_contracts_finder",
        name="UK Contracts Finder",
        kind="api_json",
        country="UK",
        url="https://www.contractsfinder.service.gov.uk/Published/Notices/OCDS/Search",
        keywords=DOMAIN_KEYWORDS,
        verified=True,
    )
    base.update(overrides)
    return TenderSource(**base)


# --------------------------------------------------------------------------
# _term_present / word-boundary keyword matching (round-6 D9 finding 2's ATR bug)
# --------------------------------------------------------------------------


class TestTermPresent:
    def test_short_keyword_does_not_match_inside_unrelated_word(self):
        """The concrete bug: 'ATR' must NOT match inside 'privaatrechtelijke'."""
        text = f"{_ROTTERDAM_TITLE} {_ROTTERDAM_SUMMARY}".casefold()
        assert _term_present("ATR", text) is False

    def test_short_keyword_matches_when_word_bounded(self):
        text = "the ATR seeker was procured".casefold()
        assert _term_present("ATR", text) is True

    def test_short_keyword_matches_at_string_edges(self):
        assert _term_present("ATR", "atr") is True
        assert _term_present("ATR", "atr system") is True
        assert _term_present("ATR", "system atr") is True

    def test_two_char_keyword_matches_when_word_bounded(self):
        """'AI' (2 chars, well under _SHORT_KEYWORD_MAX_LEN) is exactly the shape most prone to
        matching inside an unrelated word (e.g. 'domain', 'main', 'contain') -- must still match
        when it genuinely stands alone."""
        assert _term_present("AI", "an AI targeting system".casefold()) is True

    def test_two_char_keyword_does_not_match_inside_unrelated_word(self):
        assert _term_present("AI", "the domain remains unchanged") is False

    def test_five_char_keyword_above_threshold_keeps_substring_matching(self):
        """'C-UAS' is 5 characters -- above _SHORT_KEYWORD_MAX_LEN (4) -- so it deliberately keeps
        plain substring matching like any other longer keyword (no regression for the existing
        vocabulary's compound terms)."""
        assert _term_present("C-UAS", "a c-uas interceptor system") is True
        assert _term_present("C-UAS", "xc-uasx interceptor") is True

    def test_long_keyword_keeps_plain_substring_matching_for_plurals(self):
        """A long phrase must still match a plural/compound (no regression)."""
        assert _term_present("seeker", "advanced seekers for missiles") is True

    def test_long_keyword_multi_word_phrase_still_matches(self):
        assert _term_present("computer vision", "a computer vision platform") is True

    def test_long_keyword_not_present_returns_false(self):
        assert _term_present("electro-optical", "an unrelated procurement notice") is False


class TestMatchesKeywordsRegression:
    def test_rotterdam_hr_notice_no_longer_matches_atr(self):
        """End-to-end: candidate id 34's title+summary, run through the real gate function, no
        longer yields any domain term at all."""
        notice = _notice(title=_ROTTERDAM_TITLE, summary=_ROTTERDAM_SUMMARY)
        assert _matches_keywords(notice, DOMAIN_KEYWORDS) == []

    def test_genuine_atr_notice_still_matches(self):
        notice = _notice(title="RFI for ATR seeker upgrade", summary="automatic target recognition")
        assert "ATR" in _matches_keywords(notice, DOMAIN_KEYWORDS)

    def test_procurement_signal_short_acronym_word_boundary(self):
        """RFP/RFI/RFQ are also short (3 chars) -- same fix applies to the procurement-signal side."""
        notice = _notice(title="RFI for infrared targeting pod", summary="")
        assert _has_procurement_signal(notice, "search", PROCUREMENT_SIGNALS) is True

    def test_procurement_signal_still_requires_real_word(self):
        notice = _notice(title="electro-optical sensor market overview", summary="no process language here")
        assert _has_procurement_signal(notice, "search", PROCUREMENT_SIGNALS) is False

    def test_api_json_procurement_signal_always_implicit(self):
        notice = _notice(title=_ROTTERDAM_TITLE, summary=_ROTTERDAM_SUMMARY)
        assert _has_procurement_signal(notice, "api_json", PROCUREMENT_SIGNALS) is True


# --------------------------------------------------------------------------
# CPV pre-filter (_cpv_gate_reject_reason / _gate_reject_reason wiring)
# --------------------------------------------------------------------------


class TestCpvGateRejectReason:
    def test_no_cpv_data_never_rejected(self):
        assert (
            _cpv_gate_reject_reason(
                [], allow_prefixes=DEFAULT_CPV_ALLOW_PREFIXES, deny_prefixes=DEFAULT_CPV_DENY_PREFIXES
            )
            is None
        )
        assert (
            _cpv_gate_reject_reason(
                None, allow_prefixes=DEFAULT_CPV_ALLOW_PREFIXES, deny_prefixes=DEFAULT_CPV_DENY_PREFIXES
            )
            is None
        )

    def test_pure_deny_family_rejected(self):
        """79xxxxxx: business/legal/HR consulting -- candidate id 34's own family."""
        reason = _cpv_gate_reject_reason(
            ["79100000"], allow_prefixes=DEFAULT_CPV_ALLOW_PREFIXES, deny_prefixes=DEFAULT_CPV_DENY_PREFIXES
        )
        assert reason == "cpv_non_defence_79"

    def test_allow_family_never_rejected(self):
        assert (
            _cpv_gate_reject_reason(
                ["38600000"],
                allow_prefixes=DEFAULT_CPV_ALLOW_PREFIXES,
                deny_prefixes=DEFAULT_CPV_DENY_PREFIXES,
            )
            is None
        )
        assert (
            _cpv_gate_reject_reason(
                ["35700000"],
                allow_prefixes=DEFAULT_CPV_ALLOW_PREFIXES,
                deny_prefixes=DEFAULT_CPV_DENY_PREFIXES,
            )
            is None
        )

    def test_mixed_allow_and_deny_never_rejected(self):
        """A code in the allow family always wins, even alongside a deny-family code."""
        assert (
            _cpv_gate_reject_reason(
                ["79100000", "38600000"],
                allow_prefixes=DEFAULT_CPV_ALLOW_PREFIXES,
                deny_prefixes=DEFAULT_CPV_DENY_PREFIXES,
            )
            is None
        )

    def test_neutral_family_never_rejected(self):
        """A CPV code that's neither allow nor deny (e.g. 60000000 transport) is left alone -- this
        is a pre-filter for the *obvious* non-defence case, not a defence-CPV whitelist."""
        assert (
            _cpv_gate_reject_reason(
                ["60000000"],
                allow_prefixes=DEFAULT_CPV_ALLOW_PREFIXES,
                deny_prefixes=DEFAULT_CPV_DENY_PREFIXES,
            )
            is None
        )

    def test_multiple_deny_codes_all_denied_rejected(self):
        reason = _cpv_gate_reject_reason(
            ["79100000", "80500000"],
            allow_prefixes=DEFAULT_CPV_ALLOW_PREFIXES,
            deny_prefixes=DEFAULT_CPV_DENY_PREFIXES,
        )
        assert reason is not None and reason.startswith("cpv_non_defence_")

    def test_one_deny_one_neutral_not_rejected(self):
        """Not every code in the set is deny (one is deny, one is neutral) -- 'ALL codes denied'
        is not satisfied, so this is left alone."""
        assert (
            _cpv_gate_reject_reason(
                ["79100000", "60000000"],
                allow_prefixes=DEFAULT_CPV_ALLOW_PREFIXES,
                deny_prefixes=DEFAULT_CPV_DENY_PREFIXES,
            )
            is None
        )


class TestGateRejectReasonCpvWiring:
    def test_cpv_reject_reason_surfaces(self):
        notice = _notice(title="t", url="https://example.com/x", cpv_naics=["79100000"])
        reason = _gate_reject_reason(
            notice,
            deny_domains=[],
            cpv_allow_prefixes=DEFAULT_CPV_ALLOW_PREFIXES,
            cpv_deny_prefixes=DEFAULT_CPV_DENY_PREFIXES,
        )
        assert reason == "cpv_non_defence_79"

    def test_defaults_used_when_prefix_lists_omitted(self):
        """Callers (e.g. pre-R7 test code) that don't pass the new kwargs still get the CPV check
        applied via the module defaults."""
        notice = _notice(title="t", url="https://example.com/x", cpv_naics=["79100000"])
        assert _gate_reject_reason(notice, deny_domains=[]) == "cpv_non_defence_79"

    def test_status_hint_takes_priority_over_cpv(self):
        notice = _notice(
            title="t", url="https://example.com/x", cpv_naics=["79100000"], status_hint="awarded"
        )
        assert _gate_reject_reason(notice, deny_domains=[]) == "status_hint_awarded"

    def test_denylisted_domain_takes_priority_over_cpv(self):
        notice = _notice(title="t", url="https://scribd.com/x", cpv_naics=["79100000"])
        assert _gate_reject_reason(notice, deny_domains=["scribd.com"]) == "denylisted_domain"

    def test_clean_cpv_notice_not_rejected(self):
        notice = _notice(title="t", url="https://example.com/x", cpv_naics=["38600000"])
        assert _gate_reject_reason(notice, deny_domains=[]) is None

    def test_no_cpv_notice_not_rejected_by_cpv_check(self):
        notice = _notice(title="t", url="https://example.com/x", cpv_naics=[])
        assert _gate_reject_reason(notice, deny_domains=[]) is None


class TestLoadCpvPrefixes:
    def test_loads_real_config_defaults(self):
        allow = load_cpv_allow_prefixes()
        deny = load_cpv_deny_prefixes()
        assert "35" in allow and "38" in allow
        assert "79" in deny

    def test_fallback_to_module_defaults_on_missing_keys(self, tmp_path):
        cfg = tmp_path / "empty_tenders.yaml"
        cfg.write_text("sources: []\n", encoding="utf-8")
        assert load_cpv_allow_prefixes(cfg) == DEFAULT_CPV_ALLOW_PREFIXES
        assert load_cpv_deny_prefixes(cfg) == DEFAULT_CPV_DENY_PREFIXES


# --------------------------------------------------------------------------
# cpv_naics extraction in the structured parsers
# --------------------------------------------------------------------------


class TestCpvExtractionTed:
    def test_ted_parser_extracts_cpv_list(self):
        payload = {
            "notices": [
                {
                    "ND": "323139-2016",
                    "TI": {"eng": "United Kingdom-Bristol: optical instruments"},
                    "PD": "2016-09-17+02:00",
                    "classification-cpv": ["38000000"],
                    "links": {"htmlDirect": {"ENG": "https://ted.europa.eu/en/notice/323139-2016"}},
                }
            ]
        }
        notices = _parse_ted_notices(payload, _ted_source())
        assert notices[0].cpv_naics == ["38000000"]

    def test_ted_parser_missing_cpv_defaults_empty(self):
        payload = {"notices": [{"ND": "1", "TI": {"eng": "x"}, "PD": "2016-01-01"}]}
        notices = _parse_ted_notices(payload, _ted_source())
        assert notices[0].cpv_naics == []


class TestCpvExtractionContractsFinder:
    def test_cf_parser_extracts_single_cpv_code_as_list(self):
        payload = {
            "releases": [
                {
                    "ocid": "ocds-1",
                    "tag": [],
                    "date": "2026-08-25",
                    "tender": {
                        "title": "Counter-UAS system",
                        "description": "x",
                        "classification": {"scheme": "CPV", "id": "35700000"},
                    },
                }
            ]
        }
        notices = _parse_contracts_finder(payload, _cf_source())
        assert notices[0].cpv_naics == ["35700000"]

    def test_cf_parser_no_classification_defaults_empty(self):
        payload = {"releases": [{"ocid": "ocds-2", "tag": [], "tender": {"title": "x"}}]}
        notices = _parse_contracts_finder(payload, _cf_source())
        assert notices[0].cpv_naics == []


class TestCpvExtractionGenericOcds:
    def test_default_cpv_field_extracted(self):
        src = TenderSource(
            id="uk_find_tender",
            name="x",
            kind="api_json",
            country="UK",
            url="https://example.com",
            keywords=DOMAIN_KEYWORDS,
            parse_hints={"format": "ocds"},
            verified=True,
        )
        payload = {
            "releases": [
                {
                    "ocid": "ocds-3",
                    "tag": [],
                    "tender": {"title": "x", "classification": {"scheme": "CPV", "id": "79100000"}},
                }
            ]
        }
        notices = _parse_generic_ocds(payload, src)
        assert notices[0].cpv_naics == ["79100000"]

    def test_custom_cpv_field_hint_honored(self):
        src = TenderSource(
            id="uk_find_tender",
            name="x",
            kind="api_json",
            country="UK",
            url="https://example.com",
            keywords=DOMAIN_KEYWORDS,
            parse_hints={"format": "ocds", "cpv_field": "tender.mainCpv"},
            verified=True,
        )
        payload = {
            "releases": [{"ocid": "ocds-4", "tag": [], "tender": {"title": "x", "mainCpv": "38127000"}}]
        }
        notices = _parse_generic_ocds(payload, src)
        assert notices[0].cpv_naics == ["38127000"]


class TestCpvExtractionGenericJsonList:
    def test_no_cpv_field_hint_defaults_empty(self):
        src = TenderSource(
            id="nl_tenderned",
            name="x",
            kind="api_json",
            country="NL",
            url="https://example.com",
            keywords=DOMAIN_KEYWORDS,
            parse_hints={"format": "json_list", "notice_path": "content", "id_field": "id"},
            verified=True,
        )
        payload = {"content": [{"id": "1", "title": "x"}]}
        notices = _parse_generic_json_list(payload, src)
        assert notices[0].cpv_naics == []

    def test_opt_in_cpv_field_hint_extracted(self):
        src = TenderSource(
            id="some_json_source",
            name="x",
            kind="api_json",
            country="US",
            url="https://example.com",
            keywords=DOMAIN_KEYWORDS,
            parse_hints={"format": "json_list", "cpv_field": "cpvCode"},
            verified=True,
        )
        payload = {"_root": [{"id": "1", "title": "x", "cpvCode": "35000000"}]}
        notices = _parse_generic_json_list(payload, src)
        assert notices[0].cpv_naics == ["35000000"]

    def test_opt_in_cpv_field_hint_list_value(self):
        src = TenderSource(
            id="some_json_source",
            name="x",
            kind="api_json",
            country="US",
            url="https://example.com",
            keywords=DOMAIN_KEYWORDS,
            parse_hints={"format": "json_list", "cpv_field": "cpvCodes"},
            verified=True,
        )
        payload = {"_root": [{"id": "1", "title": "x", "cpvCodes": ["35000000", "38600000"]}]}
        notices = _parse_generic_json_list(payload, src)
        assert notices[0].cpv_naics == ["35000000", "38600000"]


# --------------------------------------------------------------------------
# finding 1c: dynamic key-gated api_json source enabling + {api_key} substitution
# --------------------------------------------------------------------------


class TestApiJsonSourceEnabled:
    def test_verified_source_always_enabled(self):
        src = _ted_source(verified=True)
        assert _api_json_source_enabled(src) is True

    def test_unverified_no_key_var_disabled(self):
        src = _ted_source(verified=False, needs_key_env_var=None)
        assert _api_json_source_enabled(src) is False

    def test_unverified_key_var_unset_disabled(self, monkeypatch):
        monkeypatch.delenv("SAM_GOV_API_KEY", raising=False)
        src = _ted_source(verified=False, needs_key_env_var="SAM_GOV_API_KEY")
        assert _api_json_source_enabled(src) is False

    def test_unverified_key_var_blank_disabled(self, monkeypatch):
        monkeypatch.setenv("SAM_GOV_API_KEY", "   ")
        src = _ted_source(verified=False, needs_key_env_var="SAM_GOV_API_KEY")
        assert _api_json_source_enabled(src) is False

    def test_unverified_key_var_set_enabled(self, monkeypatch):
        monkeypatch.setenv("SAM_GOV_API_KEY", "real-key-123")
        src = _ted_source(verified=False, needs_key_env_var="SAM_GOV_API_KEY")
        assert _api_json_source_enabled(src) is True


class TestFetchApiJsonApiKeySubstitution:
    def test_api_key_substituted_from_env_into_query_params(self, monkeypatch):
        monkeypatch.setenv("SAM_GOV_API_KEY", "real-key-123")
        src = TenderSource(
            id="sam_gov_api",
            name="x",
            kind="api_json",
            country="US",
            url="https://api.sam.gov/opportunities/v2/search",
            query_params={"api_key": "{api_key}", "keyword": "{keyword}"},
            keywords=DOMAIN_KEYWORDS,
            parse_hints={"format": "json", "notice_path": "opportunitiesData"},
            verified=False,
            needs_key_env_var="SAM_GOV_API_KEY",
        )
        with patch("eoa.tenders.scan.fetch_raw_remote") as mock_fetch:
            mock_fetch.return_value = {"json": {"opportunitiesData": []}}
            _fetch_api_json(src, "infrared")
        called_url = mock_fetch.call_args[0][0]
        assert "real-key-123" in called_url
        assert "{api_key}" not in called_url

    def test_missing_key_var_substitutes_empty_string(self, monkeypatch):
        monkeypatch.delenv("SAM_GOV_API_KEY", raising=False)
        src = TenderSource(
            id="sam_gov_api",
            name="x",
            kind="api_json",
            country="US",
            url="https://api.sam.gov/opportunities/v2/search",
            query_params={"api_key": "{api_key}", "keyword": "{keyword}"},
            keywords=DOMAIN_KEYWORDS,
            parse_hints={"format": "json", "notice_path": "opportunitiesData"},
            verified=False,
            needs_key_env_var="SAM_GOV_API_KEY",
        )
        with patch("eoa.tenders.scan.fetch_raw_remote") as mock_fetch:
            mock_fetch.return_value = {"json": {"opportunitiesData": []}}
            _fetch_api_json(src, "infrared")
        called_url = mock_fetch.call_args[0][0]
        assert "api_key=&" in called_url or called_url.endswith("api_key=")

    def test_source_without_key_var_unaffected(self):
        """TED/Contracts Finder-style sources with no needs_key_env_var must keep working exactly
        as before -- no {api_key} placeholder anywhere in their query."""
        src = _ted_source()
        with patch("eoa.tenders.scan.fetch_raw_remote") as mock_fetch:
            mock_fetch.return_value = {"json": {"notices": []}}
            _fetch_api_json(src, "infrared")
        body = mock_fetch.call_args.kwargs.get("json_body")
        assert body is not None
        assert "infrared" in body["query"]


# --------------------------------------------------------------------------
# repair path (find_prefilter_violations / repair_relevance_prefilter)
# --------------------------------------------------------------------------


def _fake_cursor(rows):
    cur = MagicMock()
    cur.fetchall.return_value = rows
    cur.__enter__.return_value = cur
    cur.__exit__.return_value = False
    return cur


def _fake_conn(cur):
    conn = MagicMock()
    conn.cursor.return_value = cur
    conn.__enter__.return_value = conn
    conn.__exit__.return_value = False
    return conn


class TestFindPrefilterViolations:
    def test_flags_rotterdam_like_candidate(self):
        rows = [
            {
                "id": 34,
                "source": "nl_tenderned",
                "title": _ROTTERDAM_TITLE,
                "cpv_naics": None,
                "clean_text": f"{_ROTTERDAM_TITLE}\n\n{_ROTTERDAM_SUMMARY}",
            }
        ]
        src = TenderSource(
            id="nl_tenderned",
            name="x",
            kind="api_json",
            country="NL",
            url="https://example.com",
            keywords=DOMAIN_KEYWORDS,
            verified=True,
        )
        with (
            patch("eoa.tenders.scan.load_tender_sources", return_value=[src]),
            patch("eoa.tenders.scan.connection") as mock_connection,
        ):
            mock_connection.return_value = _fake_conn(_fake_cursor(rows))
            violations = find_prefilter_violations()
        assert len(violations) == 1
        assert violations[0] == PrefilterViolation(
            id=34, source="nl_tenderned", title=_ROTTERDAM_TITLE, reason="keyword_gate_no_domain_term"
        )

    def test_cpv_violation_flagged(self):
        rows = [
            {
                "id": 99,
                "source": "ted_eu",
                "title": "Some notice",
                "cpv_naics": ["79100000"],
                "clean_text": "electro-optical tender infrared RFI",
            }
        ]
        src = _ted_source()
        with (
            patch("eoa.tenders.scan.load_tender_sources", return_value=[src]),
            patch("eoa.tenders.scan.connection") as mock_connection,
        ):
            mock_connection.return_value = _fake_conn(_fake_cursor(rows))
            violations = find_prefilter_violations()
        assert len(violations) == 1
        assert violations[0].reason == "cpv_non_defence_79"

    def test_clean_candidate_not_flagged(self):
        rows = [
            {
                "id": 100,
                "source": "ted_eu",
                "title": "RFI infrared targeting pod",
                "cpv_naics": ["38600000"],
                "clean_text": "RFI infrared targeting pod for naval EO/IR system",
            }
        ]
        src = _ted_source()
        with (
            patch("eoa.tenders.scan.load_tender_sources", return_value=[src]),
            patch("eoa.tenders.scan.connection") as mock_connection,
        ):
            mock_connection.return_value = _fake_conn(_fake_cursor(rows))
            violations = find_prefilter_violations()
        assert violations == []

    def test_row_with_unconfigured_source_skipped(self):
        rows = [
            {
                "id": 101,
                "source": "removed_source",
                "title": "x",
                "cpv_naics": None,
                "clean_text": "x",
            }
        ]
        with (
            patch("eoa.tenders.scan.load_tender_sources", return_value=[]),
            patch("eoa.tenders.scan.connection") as mock_connection,
        ):
            mock_connection.return_value = _fake_conn(_fake_cursor(rows))
            violations = find_prefilter_violations()
        assert violations == []

    def test_query_scoped_to_candidate_intake_only(self):
        """The SQL itself must never touch 'accepted'/'archived' rows."""
        with (
            patch("eoa.tenders.scan.load_tender_sources", return_value=[]),
            patch("eoa.tenders.scan.connection") as mock_connection,
        ):
            cur = _fake_cursor([])
            mock_connection.return_value = _fake_conn(cur)
            find_prefilter_violations()
        executed_sql = cur.execute.call_args[0][0]
        assert "intake = 'candidate'" in executed_sql


class TestRepairRelevancePrefilter:
    def test_dry_run_never_writes(self):
        rows = [
            {
                "id": 34,
                "source": "nl_tenderned",
                "title": _ROTTERDAM_TITLE,
                "cpv_naics": None,
                "clean_text": f"{_ROTTERDAM_TITLE}\n\n{_ROTTERDAM_SUMMARY}",
            }
        ]
        src = TenderSource(
            id="nl_tenderned",
            name="x",
            kind="api_json",
            country="NL",
            url="https://example.com",
            keywords=DOMAIN_KEYWORDS,
            verified=True,
        )
        select_cur = _fake_cursor(rows)
        with (
            patch("eoa.tenders.scan.load_tender_sources", return_value=[src]),
            patch("eoa.tenders.scan.connection") as mock_connection,
        ):
            mock_connection.return_value = _fake_conn(select_cur)
            violations = repair_relevance_prefilter(apply=False)
        assert len(violations) == 1
        # Only the one SELECT connection was ever opened -- no second connection/cursor for a write.
        assert mock_connection.call_count == 1
        assert not any("UPDATE" in str(c) for c in select_cur.execute.call_args_list)

    def test_apply_archives_only_flagged_ids_and_scopes_to_candidate(self):
        rows = [
            {
                "id": 34,
                "source": "nl_tenderned",
                "title": _ROTTERDAM_TITLE,
                "cpv_naics": None,
                "clean_text": f"{_ROTTERDAM_TITLE}\n\n{_ROTTERDAM_SUMMARY}",
            }
        ]
        src = TenderSource(
            id="nl_tenderned",
            name="x",
            kind="api_json",
            country="NL",
            url="https://example.com",
            keywords=DOMAIN_KEYWORDS,
            verified=True,
        )
        select_cur = _fake_cursor(rows)
        update_cur = _fake_cursor([])
        with (
            patch("eoa.tenders.scan.load_tender_sources", return_value=[src]),
            patch("eoa.tenders.scan.connection") as mock_connection,
        ):
            mock_connection.side_effect = [_fake_conn(select_cur), _fake_conn(update_cur)]
            violations = repair_relevance_prefilter(apply=True)
        assert len(violations) == 1
        update_sql, update_params = update_cur.execute.call_args[0]
        assert "UPDATE tenders" in update_sql
        assert "status = 'archived'" in update_sql
        assert "intake = 'candidate'" in update_sql
        assert update_params["ids"] == [34]

    def test_apply_with_no_violations_never_opens_write_connection(self):
        with (
            patch("eoa.tenders.scan.load_tender_sources", return_value=[]),
            patch("eoa.tenders.scan.connection") as mock_connection,
        ):
            mock_connection.return_value = _fake_conn(_fake_cursor([]))
            violations = repair_relevance_prefilter(apply=True)
        assert violations == []
        assert mock_connection.call_count == 1  # only the SELECT connection
