"""Unit tests for A17 (EO payload spec/price documentation, docs/PLAN_WINDOWS_NATIVE.md row A17):

- ``eoa.payloads.models.field_diff`` -- the append-only version-diff rule.
- ``eoa.payloads.extract`` -- verbatim-number post-check, price-date parsing, item scan filter,
  and the append-only persistence decisions (new spec version only on an actual diff; every price
  mention always appended) via a fake DB cursor.
- ``eoa.api.routes.payloads`` -- list/detail/diff/CSV export routes via ``TestClient`` with a
  monkeypatched DB connection (no live Postgres).

Mirrors ``tests/unit/test_patents_scan.py``/``test_patents_round3.py``'s "stub every DB/network
call" convention -- no live DB/Ollama/HTTP calls anywhere in this file.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from eoa.llm.schemas.payloads import (
    DetectorOut,
    FovOut,
    PayloadExtractOut,
    PayloadPriceOut,
    PayloadSpecOut,
    RangesKmOut,
)
from eoa.payloads.extract import (
    _number_in_text,
    _parse_price_date,
    run_payload_extract,
    scan_candidate_items,
    verify_numbers_verbatim,
)
from eoa.payloads.models import field_diff

# --------------------------------------------------------------------------
# fake DB plumbing (mirrors tests/unit/test_patents_scan.py / test_patents_round3.py)
# --------------------------------------------------------------------------


class _FakeCursor:
    def __init__(
        self, responses: dict[str, object] | None = None, fetchall_responses: dict[str, list] | None = None
    ):
        self.responses = responses or {}
        self.fetchall_responses = fetchall_responses or {}
        self.executed: list[tuple] = []
        self._last_query = ""

    def execute(self, query, params=None):
        self.executed.append((query, params))
        self._last_query = query
        return self

    def fetchone(self):
        for key, value in self.responses.items():
            if key in self._last_query:
                return value
        return None

    def fetchall(self):
        for key, value in self.fetchall_responses.items():
            if key in self._last_query:
                return value
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, cur: _FakeCursor):
        self._cur = cur

    def cursor(self, *a, **k):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --------------------------------------------------------------------------
# field_diff (append-only version rule)
# --------------------------------------------------------------------------


class TestFieldDiff:
    def test_no_prior_version_every_nonempty_key_diffs(self):
        diffs = field_diff(None, {"mass_kg": 5.2, "channels": ["MWIR"]})
        assert set(diffs) == {"mass_kg", "channels"}

    def test_identical_spec_no_diff(self):
        spec = {"mass_kg": 5.2, "trl": "6"}
        assert field_diff(spec, dict(spec)) == []

    def test_changed_field_detected(self):
        old = {"mass_kg": 5.2}
        new = {"mass_kg": 6.0}
        assert field_diff(old, new) == ["mass_kg"]

    def test_both_sides_empty_never_counts_as_diff(self):
        old = {"mass_kg": None, "channels": []}
        new = {"mass_kg": None, "channels": None}
        assert field_diff(old, new) == []

    def test_new_key_added_counts_as_diff(self):
        old = {"mass_kg": 5.2}
        new = {"mass_kg": 5.2, "trl": "6"}
        assert field_diff(old, new) == ["trl"]


# --------------------------------------------------------------------------
# verbatim-number post-check (docs/CONVENTIONS.md rule 5)
# --------------------------------------------------------------------------


def _spec_out(**overrides) -> PayloadSpecOut:
    base = dict(
        mass_kg=None,
        channels=[],
        detector=DetectorOut(),
        fov=FovOut(),
        ranges_km=RangesKmOut(),
        stabilisation_urad=None,
        interfaces=[],
        trl=None,
        other={},
    )
    base.update(overrides)
    return PayloadSpecOut(**base)


class TestNumberInText:
    def test_integer_present(self):
        assert _number_in_text(15, "The MX-15 weighs 15 kg total.") is True

    def test_integer_absent(self):
        assert _number_in_text(42, "The MX-15 weighs 15 kg total.") is False

    def test_float_present(self):
        assert _number_in_text(5.2, "Mass: 5.2 kg") is True

    def test_none_is_always_ok(self):
        assert _number_in_text(None, "anything") is True

    def test_comma_thousands_variant(self):
        assert _number_in_text(1595, "Unit price $1,595 each") is True


class TestVerifyNumbersVerbatim:
    def test_mass_kg_present_in_text_is_kept(self):
        out = PayloadExtractOut(
            found=True,
            payload_name="Widget X",
            spec=_spec_out(mass_kg=5.2),
            source_quote="The Widget X weighs 5.2 kg.",
        )
        result = verify_numbers_verbatim(out, "The Widget X weighs 5.2 kg.")
        assert result.spec["mass_kg"] == 5.2
        assert result.rejected_fields == []

    def test_mass_kg_not_in_text_is_rejected_and_nulled(self):
        out = PayloadExtractOut(
            found=True,
            payload_name="Widget X",
            spec=_spec_out(mass_kg=99.9),
            source_quote="The Widget X is compact.",
        )
        result = verify_numbers_verbatim(out, "The Widget X is compact.")
        assert "mass_kg" not in result.spec
        assert "mass_kg" in result.rejected_fields

    def test_partial_rejection_keeps_verified_fields(self):
        out = PayloadExtractOut(
            found=True,
            payload_name="Widget X",
            spec=_spec_out(mass_kg=5.2, stabilisation_urad=99.0),
            source_quote="Mass 5.2 kg.",
        )
        result = verify_numbers_verbatim(out, "Mass 5.2 kg.")
        assert result.spec["mass_kg"] == 5.2
        assert "stabilisation_urad" not in result.spec
        assert result.rejected_fields == ["stabilisation_urad"]

    def test_price_amount_not_in_text_is_rejected(self):
        out = PayloadExtractOut(
            found=True,
            payload_name="Widget X",
            spec=_spec_out(),
            price=PayloadPriceOut(amount=500000.0, currency="USD"),
            source_quote="A contract was signed.",
        )
        result = verify_numbers_verbatim(out, "A contract was signed.")
        assert "price.amount" in result.price_rejected_fields

    def test_price_amount_in_text_is_accepted(self):
        out = PayloadExtractOut(
            found=True,
            payload_name="Widget X",
            spec=_spec_out(),
            price=PayloadPriceOut(amount=500000.0, currency="USD"),
            source_quote="The unit price was $500000.",
        )
        result = verify_numbers_verbatim(out, "The unit price was $500000.")
        assert result.price_rejected_fields == []

    def test_ranges_km_and_target_class_survive_together(self):
        out = PayloadExtractOut(
            found=True,
            payload_name="Widget X",
            spec=_spec_out(ranges_km=RangesKmOut(detect=10.0, target_class="vehicle")),
            source_quote="Detection range 10 km against a vehicle target.",
        )
        result = verify_numbers_verbatim(out, "Detection range 10 km against a vehicle target.")
        assert result.spec["ranges_km"]["detect"] == 10.0
        assert result.spec["ranges_km"]["target_class"] == "vehicle"


# --------------------------------------------------------------------------
# price date parsing
# --------------------------------------------------------------------------


class TestParsePriceDate:
    def test_year_in_date_text(self):
        assert _parse_price_date("Contract signed in 2024", None, dt.date(2026, 1, 1)) == dt.date(2024, 1, 1)

    def test_falls_back_to_published_at(self):
        published = dt.datetime(2023, 5, 1, tzinfo=dt.UTC)
        assert _parse_price_date(None, published, dt.date(2026, 1, 1)) == dt.date(2023, 5, 1)

    def test_falls_back_to_today(self):
        today = dt.date(2026, 9, 6)
        assert _parse_price_date(None, None, today) == today

    def test_no_year_found_falls_back(self):
        today = dt.date(2026, 9, 6)
        assert _parse_price_date("sometime recently", None, today) == today


# --------------------------------------------------------------------------
# item scan (vocabulary filter, DB stubbed)
# --------------------------------------------------------------------------


class TestScanCandidateItems:
    def test_filters_by_vocabulary_trigger(self, monkeypatch):
        rows = [
            {
                "id": 1,
                "title": "New gimbal unveiled",
                "clean_text": "",
                "raw_text": "",
                "url": "u1",
                "published_at": None,
            },
            {
                "id": 2,
                "title": "Quarterly earnings call",
                "clean_text": "",
                "raw_text": "",
                "url": "u2",
                "published_at": None,
            },
            {
                "id": 3,
                "title": "",
                "clean_text": 'המערכת כוללת מטע"ד חדש',
                "raw_text": "",
                "url": "u3",
                "published_at": None,
            },
        ]
        cur = _FakeCursor(fetchall_responses={"FROM items": rows})
        monkeypatch.setattr("eoa.payloads.extract.connection", lambda: _FakeConnection(cur))
        result = scan_candidate_items(limit=10)
        assert {r["id"] for r in result} == {1, 3}

    def test_respects_limit_after_filtering(self, monkeypatch):
        rows = [
            {"id": i, "title": "gimbal", "clean_text": "", "raw_text": "", "url": "u", "published_at": None}
            for i in range(5)
        ]
        cur = _FakeCursor(fetchall_responses={"FROM items": rows})
        monkeypatch.setattr("eoa.payloads.extract.connection", lambda: _FakeConnection(cur))
        result = scan_candidate_items(limit=2)
        assert len(result) == 2


# --------------------------------------------------------------------------
# run_payload_extract: append-only persistence decisions
# --------------------------------------------------------------------------


def _item(item_id=1, text="The Widget X weighs 5.2 kg."):
    return {
        "id": item_id,
        "title": text,
        "clean_text": "",
        "raw_text": "",
        "url": "https://example.com/a",
        "published_at": None,
    }


def _extracted(**overrides):
    base = dict(
        found=True,
        payload_name="Widget X",
        vendor="Acme",
        family=None,
        category="gimbal",
        spec=_spec_out(mass_kg=5.2),
        price=None,
        source_quote="The Widget X weighs 5.2 kg.",
        confidence=0.7,
    )
    base.update(overrides)
    return PayloadExtractOut(**base)


class TestRunPayloadExtractPersistence:
    def test_not_found_never_persists_but_marks_stage(self, monkeypatch):
        marked = []
        monkeypatch.setattr(
            "eoa.payloads.extract.scan_candidate_items", lambda limit, item_ids=None: [_item()]
        )
        monkeypatch.setattr("eoa.payloads.extract.mark_stage", lambda item_id, stage: marked.append(item_id))
        with patch("eoa.payloads.extract._extract_one", return_value=_extracted(found=False)):
            with patch("eoa.payloads.extract._resolve_payload_id") as resolve_mock:
                stats = run_payload_extract(5)
        resolve_mock.assert_not_called()
        assert stats.not_found == 1
        assert marked == [1]

    def test_llm_failure_never_marks_stage_so_it_can_retry(self, monkeypatch):
        from eoa.errors import LLMOutputError

        marked = []
        monkeypatch.setattr(
            "eoa.payloads.extract.scan_candidate_items", lambda limit, item_ids=None: [_item()]
        )
        monkeypatch.setattr("eoa.payloads.extract.mark_stage", lambda item_id, stage: marked.append(item_id))
        with patch("eoa.payloads.extract._extract_one", side_effect=LLMOutputError("bad json")):
            stats = run_payload_extract(5)
        assert stats.llm_failed == 1
        assert marked == []

    def test_new_payload_new_spec_version_created(self, monkeypatch):
        monkeypatch.setattr(
            "eoa.payloads.extract.scan_candidate_items", lambda limit, item_ids=None: [_item()]
        )
        monkeypatch.setattr("eoa.payloads.extract.mark_stage", lambda *a, **k: None)
        with (
            patch("eoa.payloads.extract._extract_one", return_value=_extracted()),
            patch("eoa.payloads.extract._resolve_payload_id", return_value=1),
            patch("eoa.payloads.extract._latest_spec_version", return_value=None),
            patch("eoa.payloads.extract._insert_spec_version", return_value=10) as insert_mock,
        ):
            stats = run_payload_extract(5)
        insert_mock.assert_called_once()
        assert insert_mock.call_args.kwargs["version_no"] == 1
        assert stats.spec_versions_created == 1
        assert stats.spec_unchanged == 0

    def test_identical_spec_creates_no_new_version(self, monkeypatch):
        """Append-only rule: a spec that matches the latest version byte-for-byte must never
        produce a new row -- only an actual field difference does."""
        monkeypatch.setattr(
            "eoa.payloads.extract.scan_candidate_items", lambda limit, item_ids=None: [_item()]
        )
        monkeypatch.setattr("eoa.payloads.extract.mark_stage", lambda *a, **k: None)
        with (
            patch("eoa.payloads.extract._extract_one", return_value=_extracted()),
            patch("eoa.payloads.extract._resolve_payload_id", return_value=1),
            patch(
                "eoa.payloads.extract._latest_spec_version",
                return_value={"version_no": 3, "spec": {"mass_kg": 5.2}},
            ),
            patch("eoa.payloads.extract.field_diff", return_value=[]),
            patch("eoa.payloads.extract._insert_spec_version") as insert_mock,
        ):
            stats = run_payload_extract(5)
        insert_mock.assert_not_called()
        assert stats.spec_unchanged == 1
        assert stats.spec_versions_created == 0

    def test_differing_spec_creates_next_version_number(self, monkeypatch):
        monkeypatch.setattr(
            "eoa.payloads.extract.scan_candidate_items", lambda limit, item_ids=None: [_item()]
        )
        monkeypatch.setattr("eoa.payloads.extract.mark_stage", lambda *a, **k: None)
        with (
            patch("eoa.payloads.extract._extract_one", return_value=_extracted()),
            patch("eoa.payloads.extract._resolve_payload_id", return_value=1),
            patch(
                "eoa.payloads.extract._latest_spec_version",
                return_value={"version_no": 3, "spec": {"mass_kg": 1.0}},
            ),
            patch("eoa.payloads.extract._insert_spec_version", return_value=11) as insert_mock,
        ):
            stats = run_payload_extract(5)
        assert insert_mock.call_args.kwargs["version_no"] == 4
        assert stats.spec_versions_created == 1

    def test_price_mention_always_appended_never_deduped(self, monkeypatch):
        """Prices are append-only observations -- calling the stage twice on the same price text
        must insert twice, never compare against an existing row."""
        item_text = "The Widget X sells for $500000 unit price."
        extracted = _extracted(
            spec=_spec_out(),
            price=PayloadPriceOut(amount=500000.0, currency="USD", price_kind="unit"),
            source_quote=item_text,
        )
        monkeypatch.setattr(
            "eoa.payloads.extract.scan_candidate_items", lambda limit, item_ids=None: [_item(text=item_text)]
        )
        monkeypatch.setattr("eoa.payloads.extract.mark_stage", lambda *a, **k: None)
        with (
            patch("eoa.payloads.extract._extract_one", return_value=extracted),
            patch("eoa.payloads.extract._resolve_payload_id", return_value=1),
            patch("eoa.payloads.extract._insert_price_ref", return_value=20) as price_mock,
        ):
            run_payload_extract(5)
            run_payload_extract(5)
        assert price_mock.call_count == 2

    def test_price_amount_not_verbatim_is_skipped(self, monkeypatch):
        extracted = _extracted(
            spec=_spec_out(),
            price=PayloadPriceOut(amount=999999.0, currency="USD"),
            source_quote="No numbers here.",
        )
        monkeypatch.setattr(
            "eoa.payloads.extract.scan_candidate_items",
            lambda limit, item_ids=None: [_item(text="No numbers here.")],
        )
        monkeypatch.setattr("eoa.payloads.extract.mark_stage", lambda *a, **k: None)
        with (
            patch("eoa.payloads.extract._extract_one", return_value=extracted),
            patch("eoa.payloads.extract._resolve_payload_id", return_value=1),
            patch("eoa.payloads.extract._insert_price_ref") as price_mock,
        ):
            stats = run_payload_extract(5)
        price_mock.assert_not_called()
        assert stats.price_refs_created == 0


# --------------------------------------------------------------------------
# API routes (read-only)
# --------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    from eoa.api.app import create_app

    return TestClient(create_app())


class TestListPayloadsRoute:
    def test_returns_payload_list(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        rows = [{"id": 1, "canonical_name": "Widget X", "category": "gimbal", "vendor_entity_name": "Acme"}]
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads": {"c": 1}},
            fetchall_responses={"FROM payloads p": rows},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/payloads")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["payloads"][0]["canonical_name"] == "Widget X"

    def test_empty_db_is_an_honest_empty_list(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        cur = _FakeCursor(responses={"SELECT count(*) AS c FROM payloads": {"c": 0}}, fetchall_responses={})
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/payloads")
        assert r.status_code == 200
        # R06/F33 (SOL-REVIEW2-2026-09-24): the response now also carries `page`/`limit`/
        # `has_more` for server-side pagination -- see TestListPayloadsRoutePagination below.
        assert r.json() == {"payloads": [], "total": 0, "page": 1, "limit": 200, "has_more": False}

    def test_total_uses_the_same_where_clause_as_the_row_query(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """F39 (docs/qa/content_review/SOL-AUDIT-2026-09-24.md): `total` used to always be a bare
        `SELECT count(*) AS c FROM payloads` -- the whole table -- regardless of `category`/
        `vendor`/`family`/`q`. Asserts the count statement itself carries the filter's own
        parameter."""
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads p WHERE": {"c": 2}},
            fetchall_responses={"FROM payloads p": []},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/payloads", params={"vendor": "Acme"})
        assert r.status_code == 200
        assert r.json()["total"] == 2

        count_query, count_params = next(
            (q, p) for q, p in cur.executed if q.startswith("SELECT count(*) AS c FROM payloads")
        )
        assert "p.vendor_entity_name ILIKE %(vendor)s" in count_query
        assert count_params["vendor"] == "%Acme%"


class TestListPayloadsRoutePagination:
    """R06/F33 (SOL-REVIEW2-2026-09-24 review): `page`/`limit` -> LIMIT/OFFSET, same 1-based
    `page` convention as `GET /api/tech/items` (`eoa.api.services.list_tech_items`)."""

    def test_page_2_applies_the_offset(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads": {"c": 5}},
            fetchall_responses={"FROM payloads p": []},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/payloads", params={"limit": 2, "page": 2})
        assert r.status_code == 200
        body = r.json()
        assert body == {"payloads": [], "total": 5, "page": 2, "limit": 2, "has_more": True}

        row_query, row_params = next((q, p) for q, p in cur.executed if "LIMIT %(limit)s" in q)
        assert "OFFSET %(offset)s" in row_query
        assert row_params["offset"] == 2  # (page - 1) * limit == (2 - 1) * 2

    def test_has_more_is_false_on_the_last_page(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        rows = [{"id": i, "canonical_name": f"Widget {i}", "category": "gimbal"} for i in range(3)]
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads": {"c": 3}},
            fetchall_responses={"FROM payloads p": rows},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/payloads", params={"limit": 200, "page": 1})
        assert r.json()["has_more"] is False


class TestListPayloadsRouteSearchFieldsAlignment:
    """R06 (SOL-REVIEW2-2026-09-24 review): the server's `q` filter must search the same field
    set the client's tree re-filter searches (`web/src/lib/payloadFamilies.ts`'s
    `filterPayloadTree`/`variantMatches`: vendor/family name match + canonical_name/variant/
    notes) -- a field present on one side but not the other lets a server match get silently
    dropped by the client's own (differently-scoped) re-filter."""

    def test_q_filter_includes_vendor_and_variant_and_notes(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads": {"c": 0}},
            fetchall_responses={"FROM payloads p": []},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        client.get("/api/payloads", params={"q": "toplite"})

        row_query, row_params = next((q, p) for q, p in cur.executed if "LIMIT %(limit)s" in q)
        assert "p.canonical_name ILIKE %(q)s" in row_query
        assert "p.vendor_entity_name ILIKE %(q)s" in row_query
        assert "p.family ILIKE %(q)s" in row_query
        assert "p.variant ILIKE %(q)s" in row_query
        assert "p.notes ILIKE %(q)s" in row_query
        assert row_params["q"] == "%toplite%"


class TestListPayloadsRouteSearchWildcardEscaping:
    """R06/P3 (SOL-REVIEW3-2026-09-24 "search"): SQL `ILIKE` treats `%`/`_` as wildcards while the
    client's tree filter (`web/src/lib/payloadFamilies.ts`'s `variantMatches`/`filterPayloadTree`,
    plain `.includes(q)`) treats them literally -- a query of `%` used to match every row
    server-side (false non-empty) while the client's own re-filter over that same query string
    would show nothing for anything but an exact `%` substring, and a bare `%` search could also
    produce a false-empty tree once the two disagreed. `_escape_ilike_term` + `ILIKE ... ESCAPE
    '\\'` make the server's `q` match literally, same as the client."""

    def test_percent_query_is_escaped_and_matched_literally(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        from eoa.api.routes.payloads import _escape_ilike_term

        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads": {"c": 0}},
            fetchall_responses={"FROM payloads p": []},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        client.get("/api/payloads", params={"q": "%"})

        row_query, row_params = next((q, p) for q, p in cur.executed if "LIMIT %(limit)s" in q)
        # Pre-fix code sent the raw `%` straight into the pattern (`f"%{q}%"` == `"%%%"`, three
        # bare wildcards) with no `ESCAPE` clause at all -- this would have matched everything.
        assert row_params["q"] == f"%{_escape_ilike_term('%')}%"
        assert row_params["q"] == "%\\%%"
        assert "ILIKE %(q)s ESCAPE '\\'" in row_query

    def test_underscore_query_is_escaped_and_matched_literally(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads": {"c": 0}},
            fetchall_responses={"FROM payloads p": []},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        client.get("/api/payloads", params={"q": "mx_15"})

        _row_query, row_params = next((q, p) for q, p in cur.executed if "LIMIT %(limit)s" in q)
        # A literal underscore must be escaped to `\_` -- otherwise it matches ANY single
        # character (SQL `_` wildcard), e.g. "mxA15" would also match.
        assert row_params["q"] == "%mx\\_15%"

    def test_escape_helper_escapes_backslash_before_wildcards(self):
        from eoa.api.routes.payloads import _escape_ilike_term

        # Backslash must be escaped FIRST so a user-typed backslash can't be re-interpreted as
        # (part of) the escape sequence produced for `%`/`_`.
        assert _escape_ilike_term("50%_off\\sale") == "50\\%\\_off\\\\sale"


class TestListPayloadsRouteVendorFamilyWildcardEscaping:
    """R06 remainder (SOL-REVIEW4-2026-09-24 backlog): the free-text `q` filter was already fixed
    (`TestListPayloadsRouteSearchWildcardEscaping` above) to escape `%`/`_` before building its
    `ILIKE` pattern, but the separate `vendor`/`family` filters still passed the raw user value
    straight into `f"%{value}%"` with no `ESCAPE` clause -- the exact same false-match bug, just
    on a different pair of query params. These are discriminating against that: pre-fix code sends
    the raw, un-escaped value and no `ESCAPE '\\'` clause."""

    def test_vendor_percent_query_is_escaped_and_matched_literally(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        from eoa.api.routes.payloads import _escape_ilike_term

        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads": {"c": 0}},
            fetchall_responses={"FROM payloads p": []},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        client.get("/api/payloads", params={"vendor": "%"})

        row_query, row_params = next((q, p) for q, p in cur.executed if "LIMIT %(limit)s" in q)
        assert row_params["vendor"] == f"%{_escape_ilike_term('%')}%"
        assert row_params["vendor"] == "%\\%%"
        assert "p.vendor_entity_name ILIKE %(vendor)s ESCAPE '\\'" in row_query

    def test_family_underscore_query_is_escaped_and_matched_literally(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads": {"c": 0}},
            fetchall_responses={"FROM payloads p": []},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        client.get("/api/payloads", params={"family": "mx_15"})

        row_query, row_params = next((q, p) for q, p in cur.executed if "LIMIT %(limit)s" in q)
        # A literal underscore must be escaped to `\_` -- otherwise it matches ANY single
        # character (SQL `_` wildcard), e.g. "mxA15" would also match.
        assert row_params["family"] == "%mx\\_15%"
        assert "p.family ILIKE %(family)s ESCAPE '\\'" in row_query


class TestPayloadFacetsTotal:
    """R09 (SOL-REVIEW2-2026-09-24 review): an unfiltered existence count so the UI's "database
    empty" decision can't be fooled by every row having a null vendor/category (which would make
    both facet VALUE lists empty even though the table has rows)."""

    def test_facets_response_carries_an_unfiltered_total(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads": {"c": 7}},
            fetchall_responses={},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/payloads/facets")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 7
        assert body["vendors"] == []
        assert body["categories"] == []

    def test_total_is_unaffected_by_the_category_filter(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """The `total` must be the WHOLE table, not scoped to `category` -- it answers "does the
        table have any rows at all", not "any rows in this category"."""
        cur = _FakeCursor(
            responses={"SELECT count(*) AS c FROM payloads": {"c": 4}},
            fetchall_responses={},
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/payloads/facets", params={"category": "gimbal"})
        assert r.json()["total"] == 4
        _total_query, total_params = next(
            (q, p) for q, p in cur.executed if q.strip() == "SELECT count(*) AS c FROM payloads"
        )
        assert not total_params


class TestGetPayloadRoute:
    def test_404_when_missing(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        cur = _FakeCursor(responses={"FROM payloads WHERE id": None})
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/payloads/999")
        assert r.status_code == 404

    def test_returns_payload_with_versions_and_prices(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        payload_row = {"id": 1, "canonical_name": "Widget X"}
        versions = [{"id": 5, "payload_id": 1, "version_no": 2, "spec": {"mass_kg": 5.2}}]
        prices = [{"id": 9, "payload_id": 1, "price_usd": 500000.0}]
        cur = _FakeCursor(
            responses={"FROM payloads WHERE id": payload_row},
            fetchall_responses={
                "FROM payload_spec_versions WHERE payload_id": versions,
                "FROM payload_price_refs WHERE payload_id": prices,
            },
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/payloads/1")
        assert r.status_code == 200
        body = r.json()
        assert body["payload"]["canonical_name"] == "Widget X"
        assert len(body["spec_versions"]) == 1
        assert len(body["price_refs"]) == 1


class TestDiffPayloadVersionsRoute:
    def test_diff_between_two_versions(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        v1 = {"payload_id": 1, "version_no": 1, "spec": {"mass_kg": 5.0}}
        v2 = {"payload_id": 1, "version_no": 2, "spec": {"mass_kg": 6.0}}

        class _DiffCursor(_FakeCursor):
            def fetchone(self):
                if self.executed[-1][1].get("v") == 1:
                    return v1
                return v2

        cur = _DiffCursor()
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/payloads/1/diff", params={"a": 1, "b": 2})
        assert r.status_code == 200
        body = r.json()
        assert body["changed_fields"] == ["mass_kg"]

    def test_404_when_version_missing(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        cur = _FakeCursor(responses={"payload_spec_versions": None})
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/payloads/1/diff", params={"a": 1, "b": 2})
        assert r.status_code == 404


class TestExportCsvRoute:
    def test_csv_has_header_and_is_streamable(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        payloads = [
            {
                "id": 1,
                "canonical_name": "Widget X",
                "vendor_entity_name": "Acme",
                "family": None,
                "category": "gimbal",
            }
        ]
        cur = _FakeCursor(fetchall_responses={"FROM payloads ORDER BY canonical_name": payloads})
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/payloads/export.csv")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/csv")
        assert "canonical_name" in r.text
        assert "Widget X" in r.text

    def test_no_payloads_is_header_only(self, client: TestClient, monkeypatch: pytest.MonkeyPatch):
        cur = _FakeCursor(fetchall_responses={"FROM payloads ORDER BY canonical_name": []})
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))
        r = client.get("/api/payloads/export.csv")
        assert r.status_code == 200
        lines = [line for line in r.text.splitlines() if line.strip()]
        assert len(lines) == 1  # header row only

    def test_latest_spec_and_price_are_batched_not_queried_per_payload(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ):
        """Efficiency (SOL-AUDIT-2026-09-24.md #4, "Batch related-row lookups for exports and
        citations"): this used to run 2 queries per payload (2N+1 total). Asserts exactly one
        `payload_spec_versions` query and one `payload_price_refs` query cover all N payloads,
        each keyed by `payload_id = ANY(...)` rather than a per-row `payload_id = %(id)s`."""
        payloads = [
            {"id": 1, "canonical_name": "Widget X", "vendor_entity_name": "Acme", "family": None, "category": "gimbal"},
            {"id": 2, "canonical_name": "Widget Y", "vendor_entity_name": "Acme", "family": None, "category": "gimbal"},
            {"id": 3, "canonical_name": "Widget Z", "vendor_entity_name": "Acme", "family": None, "category": "gimbal"},
        ]
        specs = [
            {"payload_id": 1, "version_no": 2, "spec": {"mass_kg": 5.2}},
            {"payload_id": 2, "version_no": 1, "spec": {"mass_kg": 3.1}},
        ]
        prices = [{"payload_id": 1, "price_usd": 500000.0}]
        cur = _FakeCursor(
            fetchall_responses={
                "FROM payloads ORDER BY canonical_name": payloads,
                "FROM payload_spec_versions WHERE payload_id = ANY": specs,
                "FROM payload_price_refs WHERE payload_id = ANY": prices,
            }
        )
        monkeypatch.setattr("eoa.api.routes.payloads.connection", lambda: _FakeConnection(cur))

        r = client.get("/api/payloads/export.csv")
        assert r.status_code == 200

        spec_queries = [q for q, _p in cur.executed if "FROM payload_spec_versions" in q]
        price_queries = [q for q, _p in cur.executed if "FROM payload_price_refs" in q]
        assert len(spec_queries) == 1
        assert len(price_queries) == 1
        assert "= ANY(%(ids)s)" in spec_queries[0]
        assert "= ANY(%(ids)s)" in price_queries[0]

        rows = [line for line in r.text.splitlines() if line.strip()]
        assert len(rows) == 4  # header + 3 payloads, including the one with neither spec nor price
        assert "5.2" in r.text  # payload 1's batched-in spec value made it to the row
