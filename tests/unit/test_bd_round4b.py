"""Unit tests for round 4b BD findings (docs/REVIEW_2026-09-06_evening.md, W15-W18, W22):

  - W16(c): ``eoa.report.bd_territory``'s territory-mention text/adjective fallback for items and
    procurement events, when ``items.geography``/``entities.country`` don't resolve to the
    territory directly.
  - W16(a): the tables-only/no-items drafts no longer repeat "no findings" in every section, and
    ``build_bd_territory`` fills a real, cited ``exec_summary`` from whatever deterministic tables
    do have content.
  - W16(b): a territory with configured watchlist competitors but zero market items enqueues a
    targeted expansion deep-search job (deduped while one is already queued/running) and the
    report says so.
  - W17: ``eoa.report.docx_builder``'s ``_html_cell``/``_md_cell`` turn a ``[n]`` marker inside a
    table cell into the same real link the prose gets.
  - W18: ``eoa.report.acquisition_watch`` filters M&A-signal events to the report's own territory
    and reclassifies a contract-with-customer-and-amount away from "investment".

Every DB-touching function is monkeypatched or driven through a fake cursor/connection -- no
Postgres required.

Run with: ``PYTHONPATH=agent python -m pytest tests/unit/test_bd_round4b.py -q``
"""

from __future__ import annotations

import datetime as dt

from eoa.report import acquisition_watch as aw_report
from eoa.report import bd_territory as bdt
from eoa.report import docx_builder as db

# --------------------------------------------------------------------------
# W16(c): territory text/adjective-mention fallback
# --------------------------------------------------------------------------


class TestTerritoryTextMention:
    def test_country_noun_mention_in_title_matches(self):
        row = {"title": "Germany Leans Toward Watercat M18 AMC for Sea Battalion", "summary_he": ""}
        assert bdt._item_text_mentions_territory(row, "DE") is True

    def test_adjective_form_mention_matches(self):
        row = {
            "title": "German Navy Conducts Firing with IAI's Naval LORA Ballistic Missile",
            "summary_he": "",
        }
        assert bdt._item_text_mentions_territory(row, "DE") is True

    def test_hebrew_summary_adjective_mention_matches(self):
        row = {"title": "x", "summary_he": "חברת Hensoldt הגרמנית חתמה על הסכם."}
        assert bdt._item_text_mentions_territory(row, "DE") is True

    def test_incidental_mention_in_so_what_he_does_not_match(self):
        """A live false positive: an item about Serbia/Elbit whose so_what_he merely draws a
        comparison to another deal ("...לצד מכירת מערכת Arrow 3 לגרמניה...") must not make the
        item count as a German-market item -- only title/summary_he are checked."""
        row = {
            "title": "Serbia to open joint UAV factory with Elbit in September",
            "summary_he": "סרביה צפויה לפתוח מפעל משותף לייצור כטבמים עם אלביט מערכות.",
            "so_what_he": "לצד עסקאות גדולות אחרות כמו מכירת מערכת Arrow 3 לגרמניה.",
        }
        assert bdt._item_text_mentions_territory(row, "DE") is False

    def test_no_mention_returns_false(self):
        row = {"title": "US Army awards new targeting pod contract", "summary_he": "צבא ארהב."}
        assert bdt._item_text_mentions_territory(row, "DE") is False

    def test_event_customer_mention_matches(self):
        ev = {"customer": "German Navy", "parties": []}
        assert bdt._event_mentions_territory(ev, "DE") is True

    def test_event_party_mention_matches(self):
        ev = {"customer": None, "parties": ["Bundeswehr", "Hensoldt"]}
        # "Bundeswehr" alone isn't a country name/adjective this table knows -- no match expected
        # unless a party string itself names the territory.
        assert bdt._event_mentions_territory(ev, "DE") is False
        ev2 = {"customer": None, "parties": ["German Air Force"]}
        assert bdt._event_mentions_territory(ev2, "DE") is True

    def test_unsupported_territory_code_never_matches_via_adjective_table(self):
        row = {"title": "Some unrelated headline", "summary_he": ""}
        assert bdt._item_text_mentions_territory(row, "ZZ") is False


class TestCollectMarketItemsTextFallback:
    def test_geography_mismatch_but_title_mentions_territory_is_included(self, monkeypatch):
        rows = [
            {
                "id": 1,
                "url": "u",
                "title": "German Navy Conducts Firing with IAI's Naval LORA",
                "domain": "d",
                "subdomain": None,
                "published_at": dt.date(2026, 8, 1),
                "level": "yellow",
                "score": 1,
                "summary_he": "",
                "so_what_he": "",
                "geography": "other",
                "entities_mentioned": [],
                "source_name": "s",
            }
        ]

        def fake_fetchall(query, params=None):
            if "FROM entities" in query:
                return []
            return rows

        monkeypatch.setattr(bdt, "_fetchall", fake_fetchall)
        out = bdt.collect_market_items("DE", dt.date(2026, 6, 1), dt.date(2026, 9, 1))
        assert len(out) == 1
        assert out[0]["n"] == 1

    def test_unrelated_item_is_excluded(self, monkeypatch):
        rows = [
            {
                "id": 2,
                "url": "u",
                "title": "US Army awards new targeting pod contract",
                "domain": "d",
                "subdomain": None,
                "published_at": dt.date(2026, 8, 1),
                "level": "yellow",
                "score": 1,
                "summary_he": "",
                "so_what_he": "",
                "geography": "other",
                "entities_mentioned": [],
                "source_name": "s",
            }
        ]

        def fake_fetchall(query, params=None):
            if "FROM entities" in query:
                return []
            return rows

        monkeypatch.setattr(bdt, "_fetchall", fake_fetchall)
        out = bdt.collect_market_items("DE", dt.date(2026, 6, 1), dt.date(2026, 9, 1))
        assert out == []


# --------------------------------------------------------------------------
# W16(a): no repeated "no findings" messaging
# --------------------------------------------------------------------------


class TestNoRepeatedEmptyMessaging:
    def test_tables_only_draft_has_no_risks_text(self):
        draft = bdt._tables_only_draft("US", bdt.BdTableCounts(forecasts=1))
        assert draft.risks_assumptions_he == ""
        assert "לא זוהו פריטי שוק חדשים" in draft.system_note_he

    def test_no_items_draft_has_no_risks_text(self):
        draft = bdt._no_items_draft()
        assert draft.risks_assumptions_he == ""
        assert "אין ממצאים" in draft.system_note_he

    def test_tables_summary_sentences_prioritizes_events_then_tenders_then_forecasts_then_conferences(self):
        events = [{"n": 1, "platform_he": "p", "buyer": "b", "vendor": "v", "date": dt.date(2026, 8, 1)}]
        tenders_data = {
            "tenders": [{"n": 2, "title": "t", "agency": "a", "status": "open", "deadline": None}],
            "forecasts": [{"n": 3, "platform": "p2", "payload_need": "need", "likelihood": 0.5}],
        }
        conferences_data = {"territory": [{"n": 4, "name": "AUSA", "start_date": dt.date(2026, 10, 1)}]}

        sentences = bdt._tables_summary_sentences(events, tenders_data, conferences_data, limit=4)
        assert [s.cites for s in sentences] == [[1], [2], [3], [4]]

    def test_tables_summary_sentences_empty_when_everything_empty(self):
        assert bdt._tables_summary_sentences([], {"tenders": [], "forecasts": []}, {"territory": []}) == []

    def test_tables_summary_sentences_respects_limit(self):
        tenders_data = {
            "tenders": [{"n": i, "title": f"t{i}", "agency": "a", "status": "open"} for i in range(1, 10)],
            "forecasts": [],
        }
        sentences = bdt._tables_summary_sentences([], tenders_data, {"territory": []}, limit=2)
        assert len(sentences) == 2


class TestForecastsTable:
    def test_none_when_no_forecasts(self):
        assert bdt.forecasts_table({"forecasts": []}) is None

    def test_renders_row_with_citation_marker(self):
        data = {
            "forecasts": [
                {
                    "n": 5,
                    "platform": "כטבם MALE",
                    "payload_need": "מטען EO/IR",
                    "likelihood": 0.6,
                    "window_from": dt.date(2027, 1, 1),
                    "window_to": dt.date(2027, 6, 1),
                }
            ]
        }
        table = bdt.forecasts_table(data)
        assert table["headers"] == ["פלטפורמה", "צורך/Payload", "סבירות", "חלון", "מקור"]
        assert table["rows"][0][-1] == "[5]"


# --------------------------------------------------------------------------
# W16(b): targeted expansion search when a territory has watchlist competitors but no items
# --------------------------------------------------------------------------


class TestExpansionSearch:
    def test_has_pending_expansion_search_true_when_job_queued(self, monkeypatch):
        monkeypatch.setattr(bdt, "_fetchall", lambda q, p=None: [{"1": 1}])
        assert bdt._has_pending_expansion_search("DE") is True

    def test_has_pending_expansion_search_false_when_none_queued(self, monkeypatch):
        monkeypatch.setattr(bdt, "_fetchall", lambda q, p=None: [])
        assert bdt._has_pending_expansion_search("DE") is False

    def test_enqueue_skips_when_already_pending(self, monkeypatch):
        monkeypatch.setattr(bdt, "_has_pending_expansion_search", lambda code: True)
        calls = []
        monkeypatch.setattr("eoa.memory.relational.enqueue_job", lambda *a, **k: calls.append((a, k)) or 1)
        bdt._enqueue_territory_expansion_search("DE")
        assert calls == []

    def test_enqueue_calls_enqueue_job_with_expanded_from_tag(self, monkeypatch):
        monkeypatch.setattr(bdt, "_has_pending_expansion_search", lambda code: False)
        calls = []

        def fake_enqueue(kind, payload, priority=5, **kw):
            calls.append((kind, payload, priority))
            return 42

        monkeypatch.setattr("eoa.memory.relational.enqueue_job", fake_enqueue)
        bdt._enqueue_territory_expansion_search("DE")
        assert len(calls) == 1
        kind, payload, _priority = calls[0]
        assert kind == "deep_search"
        assert payload["expanded_from"] == "bd:DE"
        assert payload["territory"] == "DE"
        assert "DE" in payload["question"] or "גרמניה" in payload["question"]

    def test_enqueue_never_raises_on_db_failure(self, monkeypatch):
        def boom(code):
            raise RuntimeError("no db")

        monkeypatch.setattr(bdt, "_has_pending_expansion_search", boom)
        bdt._enqueue_territory_expansion_search("DE")  # must not raise

    def test_build_bd_territory_triggers_expansion_and_notes_it(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bdt, "collect_market_items", lambda t, s, e, max_items=250: [])
        monkeypatch.setattr(bdt, "collect_platform_events", lambda t, s, e, limit=25: [])
        monkeypatch.setattr(
            bdt, "collect_tenders_and_forecasts", lambda t, limit=20: {"tenders": [], "forecasts": []}
        )
        monkeypatch.setattr(bdt, "collect_active_competitors", lambda t, ids, s, e, limit=15: [])
        monkeypatch.setattr(
            bdt,
            "collect_conferences_for_territory",
            lambda t, months=12, international_limit=5: {"territory": [], "international": []},
        )
        monkeypatch.setattr(
            bdt, "collect_dormant_watchlist_competitors", lambda t, active, limit=20: ["Rheinmetall"]
        )
        monkeypatch.setattr(bdt, "_persist_report", lambda *a, **k: 1)
        monkeypatch.setattr(bdt, "_report_path", lambda territory, period_end, ext: tmp_path / f"bd.{ext}")

        enqueue_calls = []
        monkeypatch.setattr(
            bdt, "_enqueue_territory_expansion_search", lambda code: enqueue_calls.append(code)
        )
        monkeypatch.setattr(
            "eoa.report.bd_territory.chat_structured",
            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not call the LLM")),
        )

        paths = bdt.build_bd_territory("DE", 90, period_end=dt.date(2026, 9, 6))
        assert enqueue_calls == ["DE"]
        assert paths.qa.passed
        md_text = paths.md.read_text(encoding="utf-8")
        assert bdt.EXPANDED_SEARCH_NOTE_HE in md_text

    def test_build_bd_territory_skips_expansion_when_no_watchlist_competitor(self, monkeypatch, tmp_path):
        monkeypatch.setattr(bdt, "collect_market_items", lambda t, s, e, max_items=250: [])
        monkeypatch.setattr(bdt, "collect_platform_events", lambda t, s, e, limit=25: [])
        monkeypatch.setattr(
            bdt, "collect_tenders_and_forecasts", lambda t, limit=20: {"tenders": [], "forecasts": []}
        )
        monkeypatch.setattr(bdt, "collect_active_competitors", lambda t, ids, s, e, limit=15: [])
        monkeypatch.setattr(
            bdt,
            "collect_conferences_for_territory",
            lambda t, months=12, international_limit=5: {"territory": [], "international": []},
        )
        monkeypatch.setattr(bdt, "collect_dormant_watchlist_competitors", lambda t, active, limit=20: [])
        monkeypatch.setattr(bdt, "_persist_report", lambda *a, **k: 1)
        monkeypatch.setattr(bdt, "_report_path", lambda territory, period_end, ext: tmp_path / f"bd.{ext}")

        enqueue_calls = []
        monkeypatch.setattr(
            bdt, "_enqueue_territory_expansion_search", lambda code: enqueue_calls.append(code)
        )

        paths = bdt.build_bd_territory("PL", 90, period_end=dt.date(2026, 9, 6))
        assert enqueue_calls == []
        md_text = paths.md.read_text(encoding="utf-8")
        assert bdt.EXPANDED_SEARCH_NOTE_HE not in md_text


# --------------------------------------------------------------------------
# W17: table-cell citation rendering (docx_builder)
# --------------------------------------------------------------------------


class TestTableCellCitationLinks:
    def test_html_cell_turns_bare_marker_into_anchor_link(self):
        assert db._html_cell("[10]") == '<a href="#src-10" class="cite">[10]</a>'

    def test_html_cell_turns_marker_inside_text_into_anchor_link(self):
        out = db._html_cell("זכייה [7]")
        assert '<a href="#src-7" class="cite">[7]</a>' in out

    def test_html_cell_url_still_becomes_real_link_not_citation(self):
        out = db._html_cell("https://example.com/x")
        assert out.startswith("<a href=")
        assert "cite" not in out

    def test_html_cell_hebrew_text_unaffected(self):
        # No `[n]` marker, no URL -- an ordinary Hebrew value passes straight through
        # `_bidi_html`, same as before W17 (unlike a bare "—", plain punctuation with no preceding
        # Hebrew letter is classified "other" and legitimately gets an LTR `<bdi>` wrap by
        # `_bidi_html` itself -- pre-existing, unrelated to this fix).
        assert db._html_cell("מתחרה") == "מתחרה"
        assert db._html_cell(None) == "—"

    def test_md_cell_turns_marker_into_anchor_link(self):
        assert db._md_cell("[10]") == "[10](#src-10)"

    def test_md_cell_url_still_becomes_markdown_link(self):
        assert db._md_cell("https://example.com/x") == "[https://example.com/x](https://example.com/x)"


# --------------------------------------------------------------------------
# W18: acquisition-watch territory filter + event-kind reclassification
# --------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.last_query = (sql, params)

    def fetchall(self):
        return self._rows


class FakeConn:
    """Hands out one `FakeCursor` per `cursor()` call, in the fixed order this test queues them."""

    def __init__(self, cursor_rows):
        self._queue = list(cursor_rows)

    def cursor(self):
        rows = self._queue.pop(0) if self._queue else []
        return FakeCursor(rows)


def _event_row(**overrides):
    base = dict(
        id=1,
        item_id=100,
        kind="investment",
        date=dt.date(2026, 9, 3),
        amount_usd=370_000_000,
        currency="USD",
        customer="US Air Force",
        parties=["Elbit"],
        geography="US",
        item_url="https://example.com/a",
        item_title="Elbit Systems wins US orders worth $370m",
        published_at=dt.datetime(2026, 9, 3),
        source_name="Globes",
    )
    base.update(overrides)
    return base


class TestReclassifiedKind:
    def test_investment_with_amount_and_customer_becomes_contract_award(self):
        assert aw_report._reclassified_kind(_event_row()) == "contract_award"

    def test_investment_without_customer_stays_investment(self):
        ev = _event_row(customer=None)
        assert aw_report._reclassified_kind(ev) == "investment"

    def test_investment_without_amount_stays_investment(self):
        ev = _event_row(amount_usd=None)
        assert aw_report._reclassified_kind(ev) == "investment"

    def test_non_investment_kind_passes_through(self):
        ev = _event_row(kind="partnership")
        assert aw_report._reclassified_kind(ev) == "partnership"


class TestAcquisitionWatchTerritoryFilter:
    def test_reclassified_contract_award_is_excluded_from_section(self):
        # events query only -- the reclassified row never reaches the M&A-signal set, so no
        # entity-countries lookup or patents query is needed either.
        conn = FakeConn([[_event_row()], []])
        body = aw_report.acquisition_watch_section_md(conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), [])
        # The row itself never renders (kept out of the table entirely) -- the boilerplate
        # "no events" placeholder line (which legitimately contains the word "השקעה" as part of
        # "רכישה/השקעה/שותפות") is what's left, not an actual table row for this event.
        assert "Elbit Systems wins US orders" not in body
        assert aw_report._NO_EVENTS_LINE_HE in body
        assert "| השקעה |" not in body

    def test_event_outside_territory_is_dropped_from_territory_report(self):
        # Elbit/India event (unrelated to Germany): events query, entity-countries query (empty --
        # no entity rows for Elbit/India in this fixture), patents query.
        ev = _event_row(
            id=2,
            kind="m_and_a",
            amount_usd=None,
            customer=None,
            parties=["Elbit"],
            geography="IN",
            item_title="Elbit eyes converting vessels into drone carriers",
        )
        conn = FakeConn([[ev], [], []])
        body = aw_report.acquisition_watch_section_md(
            conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), [], territory="DE"
        )
        assert "Elbit eyes converting vessels" not in body
        assert aw_report._NO_EVENTS_LINE_HE in body

    def test_event_in_territory_by_geography_is_kept(self):
        ev = _event_row(
            id=3,
            kind="partnership",
            amount_usd=None,
            customer=None,
            parties=["Hensoldt"],
            geography="DE",
            item_title="Hensoldt partnership item",
        )
        conn = FakeConn([[ev], [], []])
        body = aw_report.acquisition_watch_section_md(
            conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), [], territory="DE"
        )
        assert "Hensoldt" in body
        assert "[1]" in body

    def test_event_matching_via_party_country_is_kept(self):
        ev = _event_row(
            id=4,
            kind="partnership",
            amount_usd=None,
            customer="Rheinmetall",
            parties=["Hensoldt"],
            geography="other",
            item_title="Hensoldt and Rheinmetall partnership",
        )
        conn = FakeConn([[ev], [{"name": "Rheinmetall", "country": "DE"}], []])
        body = aw_report.acquisition_watch_section_md(
            conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), [], territory="DE"
        )
        assert "Hensoldt" in body

    def test_watch_company_own_country_event_shown_as_global_context(self):
        # Elbit (an "acquisition_watch" peer) is headquartered in IL -- an unrelated-to-Germany
        # Elbit event should surface as global context only on Elbit's own territory report (IL),
        # never silently promoted into the Germany report's main table. The raw `parties` entry is
        # "Elbit Systems" (a real `companies:` alias, per config/watchlist.yaml) while the
        # `entities` row is only under the canonical "Elbit" -- exercises the canonical-name
        # fallback lookup (`_split_events_by_territory` must resolve the alias to "Elbit" via
        # `watch_or_peer_hit` before it can find its `entities.country`).
        ev = _event_row(
            id=5,
            kind="m_and_a",
            amount_usd=None,
            customer=None,
            parties=["Elbit Systems"],
            geography="RS",  # e.g. the Serbia UAV-factory story
            item_title="Elbit Serbia joint venture",
        )
        conn = FakeConn([[ev], [{"name": "Elbit", "country": "IL"}], []])
        registry: list[dict] = []
        body = aw_report.acquisition_watch_section_md(
            conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), registry, territory="IL"
        )
        # The row itself (company + citation into the registry, which now carries the item title)
        # shows up under the "global context" sub-table, not the main one.
        assert "פעילות גלובלית" in body
        global_part = body.split("פעילות גלובלית")[1]
        assert "| Elbit |" in global_part
        assert registry[0]["title"] == "Elbit Serbia joint venture"

    def test_event_with_own_entity_country_match_is_kept_as_regular_territory_row(self):
        # A subtler case than the above: the raw party string IS the canonical name and DOES carry
        # its own `entities.country`, and that country happens to equal the report's own
        # territory -- this is a normal, direct territory match (via `party_countries`), not the
        # "global context" branch (which only ever applies to a *different* territory's report).
        ev = _event_row(
            id=7,
            kind="m_and_a",
            amount_usd=None,
            customer=None,
            parties=["Elbit"],
            geography="RS",
            item_title="Elbit Serbia joint venture",
        )
        conn = FakeConn([[ev], [{"name": "Elbit", "country": "IL"}], []])
        registry: list[dict] = []
        body = aw_report.acquisition_watch_section_md(
            conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), registry, territory="IL"
        )
        assert "| Elbit |" in body
        assert "פעילות גלובלית" not in body
        assert registry[0]["title"] == "Elbit Serbia joint venture"

    def test_no_territory_argument_reproduces_prior_unfiltered_behavior(self):
        ev = _event_row(id=6, kind="partnership", amount_usd=None, customer=None, geography="IN")
        conn = FakeConn([[ev], []])
        body = aw_report.acquisition_watch_section_md(conn, dt.date(2026, 9, 1), dt.date(2026, 9, 7), [])
        assert "Elbit Systems wins US orders" in body or "Elbit" in body
