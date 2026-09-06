"""Tests for eoa.patents.cluster (A14b, docs/PLAN_WINDOWS_NATIVE.md row A14b): deterministic
clustering, expiry/timeline math, business-relationship extraction, and the assignee-negation
consistency check -- all pure, no DB/LLM."""

from __future__ import annotations

import datetime as dt

from eoa.patents.cluster import (
    FLAG_EXPIRED_HE,
    FLAG_EXPIRING_SOON_HE,
    FLAG_PENDING_HE,
    UNCLASSIFIED_LABEL_HE,
    build_timeline_rows,
    cluster_patents,
    co_assignment_pairs,
    consistency_violations,
    cross_cluster_links,
    expiry_estimate,
    expiry_flag,
    filing_waves,
    relationship_edges_from_events,
    same_family_groups,
)


class _Topic:
    def __init__(self, name_he: str, query: str, cpc: list[str]):
        self.name_he = name_he
        self.query = query
        self.cpc = cpc


class TestExpiryEstimate:
    def test_uses_filing_date_when_present(self):
        out = expiry_estimate(dt.date(2020, 1, 1), dt.date(2019, 1, 1))
        assert out == dt.date(2040, 1, 1)

    def test_falls_back_to_priority_date(self):
        out = expiry_estimate(None, dt.date(2019, 1, 1))
        assert out == dt.date(2039, 1, 1)

    def test_none_when_neither_date_known(self):
        assert expiry_estimate(None, None) is None


class TestExpiryFlag:
    def test_pending_when_no_grant_date(self):
        flag = expiry_flag(expiry_date=dt.date(2040, 1, 1), grant_date=None, today=dt.date(2026, 1, 1))
        assert flag == FLAG_PENDING_HE

    def test_expired_flag(self):
        flag = expiry_flag(
            expiry_date=dt.date(2020, 1, 1), grant_date=dt.date(2005, 1, 1), today=dt.date(2026, 1, 1)
        )
        assert flag == FLAG_EXPIRED_HE

    def test_expiring_soon_flag(self):
        flag = expiry_flag(
            expiry_date=dt.date(2028, 6, 1), grant_date=dt.date(2010, 1, 1), today=dt.date(2026, 9, 6)
        )
        assert flag == FLAG_EXPIRING_SOON_HE

    def test_in_force_returns_empty_string(self):
        flag = expiry_flag(
            expiry_date=dt.date(2045, 1, 1), grant_date=dt.date(2010, 1, 1), today=dt.date(2026, 9, 6)
        )
        assert flag == ""

    def test_no_expiry_date_and_granted_returns_empty(self):
        assert expiry_flag(expiry_date=None, grant_date=dt.date(2010, 1, 1)) == ""


class TestBuildTimelineRows:
    def test_produces_one_row_per_patent_with_expiry_and_flag(self):
        rows = [
            {
                "n": 1,
                "pub_number": "US1",
                "title": "t",
                "priority_date": None,
                "filing_date": dt.date(2020, 1, 1),
                "publication_date": dt.date(2021, 1, 1),
                "grant_date": dt.date(2022, 1, 1),
            }
        ]
        out = build_timeline_rows(rows, today=dt.date(2026, 9, 6))
        assert len(out) == 1
        assert out[0].expiry_date == dt.date(2040, 1, 1)
        assert out[0].flag_he == ""

    def test_missing_dates_never_invented(self):
        rows = [{"n": 1, "pub_number": "US1", "title": "t"}]
        out = build_timeline_rows(rows)
        assert out[0].filing_date is None
        assert out[0].expiry_date is None
        assert out[0].flag_he == FLAG_PENDING_HE  # no grant_date


class TestFilingWaves:
    def test_groups_by_key_and_year(self):
        rows = [
            {"publication_date": dt.date(2020, 1, 1), "assignees": ["A"]},
            {"publication_date": dt.date(2020, 6, 1), "assignees": ["A"]},
            {"publication_date": dt.date(2021, 1, 1), "assignees": ["B"]},
        ]
        out = filing_waves(rows, lambda r: (r.get("assignees") or [None])[0])
        assert out["A"][2020] == 2
        assert out["B"][2021] == 1

    def test_skips_rows_without_publication_date_or_key(self):
        rows = [{"assignees": ["A"]}, {"publication_date": dt.date(2020, 1, 1), "assignees": []}]
        out = filing_waves(rows, lambda r: (r.get("assignees") or [None])[0])
        assert out == {}


class TestClusterPatents:
    def _rows(self):
        return [
            {
                "n": 1,
                "id": 10,
                "cpc": ["G01J5"],
                "assignees": ["Anduril"],
                "title": "IR detector",
                "abstract": "",
                "publication_date": dt.date(2020, 1, 1),
                "grant_date": dt.date(2021, 1, 1),
            },
            {
                "n": 2,
                "id": 11,
                "cpc": ["G01J5"],
                "assignees": ["Anduril", "Lockheed"],
                "title": "IR detector 2",
                "abstract": "",
                "publication_date": dt.date(2021, 1, 1),
                "grant_date": None,
            },
            {
                "n": 3,
                "id": 12,
                "cpc": ["H04N23"],
                "assignees": ["Lockheed"],
                "title": "camera",
                "abstract": "",
                "publication_date": dt.date(2010, 1, 1),
                "grant_date": dt.date(2011, 1, 1),
            },
        ]

    def test_groups_by_primary_cpc_code(self):
        clusters = cluster_patents(self._rows())
        assert len(clusters) == 2
        biggest = clusters[0]
        assert biggest.size == 2
        assert biggest.cpc_codes == ["G01J5"]

    def test_sorted_largest_first(self):
        clusters = cluster_patents(self._rows())
        assert clusters[0].size >= clusters[1].size

    def test_grant_ratio_and_maturity(self):
        clusters = cluster_patents(self._rows())
        camera_cluster = next(c for c in clusters if "H04N23" in c.cpc_codes)
        assert camera_cluster.grant_ratio == 1.0
        assert "בשל" in camera_cluster.maturity_label_he

    def test_dominant_assignees_most_common_first(self):
        clusters = cluster_patents(self._rows())
        g01j5 = next(c for c in clusters if "G01J5" in c.cpc_codes)
        assert g01j5.dominant_assignees[0] == "Anduril"

    def test_keyword_fallback_for_patents_without_cpc(self):
        rows = [
            {
                "n": 1,
                "id": 1,
                "cpc": [],
                "assignees": ["X"],
                "title": "digital pixel readout integrated circuit for infrared focal plane array",
                "abstract": "",
                "publication_date": dt.date(2020, 1, 1),
            }
        ]
        topics = [
            _Topic(
                "FPA עם פיקסל דיגיטלי",
                "digital pixel readout integrated circuit infrared focal plane",
                ["H01L27"],
            )
        ]
        clusters = cluster_patents(rows, topics=topics)
        assert clusters[0].label_he == "FPA עם פיקסל דיגיטלי"

    def test_unclassified_bucket_for_no_signal(self):
        rows = [
            {
                "n": 1,
                "id": 1,
                "cpc": [],
                "assignees": [],
                "title": "",
                "abstract": "",
                "publication_date": None,
            }
        ]
        clusters = cluster_patents(rows)
        assert clusters[0].label_he == UNCLASSIFIED_LABEL_HE

    def test_cpc_label_uses_matching_watch_topic(self):
        rows = [
            {
                "n": 1,
                "id": 1,
                "cpc": ["G01J5"],
                "assignees": [],
                "title": "t",
                "abstract": "",
                "publication_date": None,
            }
        ]
        topics = [_Topic("FPA עם פיקסל דיגיטלי", "digital pixel readout", ["G01J5"])]
        clusters = cluster_patents(rows, topics=topics)
        assert clusters[0].label_he == "FPA עם פיקסל דיגיטלי"


class TestCrossClusterLinks:
    def test_finds_shared_assignees_between_clusters(self):
        rows = [
            {
                "n": 1,
                "id": 1,
                "cpc": ["G01J5"],
                "assignees": ["A"],
                "title": "",
                "abstract": "",
                "publication_date": None,
            },
            {
                "n": 2,
                "id": 2,
                "cpc": ["H04N23"],
                "assignees": ["A"],
                "title": "",
                "abstract": "",
                "publication_date": None,
            },
        ]
        clusters = cluster_patents(rows)
        links = cross_cluster_links(clusters)
        assert len(links) == 1
        assert links[0][2] == ["A"]

    def test_no_links_when_no_shared_assignees(self):
        rows = [
            {
                "n": 1,
                "id": 1,
                "cpc": ["G01J5"],
                "assignees": ["A"],
                "title": "",
                "abstract": "",
                "publication_date": None,
            },
            {
                "n": 2,
                "id": 2,
                "cpc": ["H04N23"],
                "assignees": ["B"],
                "title": "",
                "abstract": "",
                "publication_date": None,
            },
        ]
        clusters = cluster_patents(rows)
        assert cross_cluster_links(clusters) == []


class TestCoAssignmentPairs:
    def test_counts_pairs_alphabetically_ordered(self):
        rows = [{"assignees": ["B", "A"]}, {"assignees": ["A", "B"]}]
        out = co_assignment_pairs(rows)
        assert out[("A", "B")] == 2
        assert ("B", "A") not in out

    def test_single_assignee_produces_no_pair(self):
        rows = [{"assignees": ["A"]}]
        assert co_assignment_pairs(rows) == {}


class TestSameFamilyGroups:
    def test_groups_shared_family_id(self):
        rows = [{"n": 1, "family_id": "F1"}, {"n": 2, "family_id": "F1"}, {"n": 3, "family_id": "F2"}]
        out = same_family_groups(rows)
        assert out == {"F1": [1, 2]}

    def test_single_member_family_excluded(self):
        rows = [{"n": 1, "family_id": "F1"}]
        assert same_family_groups(rows) == {}


class TestRelationshipEdgesFromEvents:
    def test_builds_edge_from_customer_field(self):
        events = [{"customer": "Israel MoD", "kind": "contract_award", "n": 5, "program": "C-UAS"}]
        edges = relationship_edges_from_events("Anduril", events)
        assert edges == [
            {
                "from": "Anduril",
                "to": "Israel MoD",
                "kind": "contract_award",
                "n": 5,
                "program": "C-UAS",
                "title": None,
            }
        ]

    def test_excludes_self_from_parties(self):
        events = [{"parties": ["Anduril", "Palantir"], "kind": "partnership", "n": 1}]
        edges = relationship_edges_from_events("Anduril", events)
        assert [e["to"] for e in edges] == ["Palantir"]


class TestConsistencyViolations:
    def test_flags_negation_of_known_assignee_with_patents(self):
        text = "אין פטנטים של Anduril בתחום זה."
        out = consistency_violations(text, {"Anduril": 3})
        assert len(out) == 1
        assert "Anduril" in out[0]

    def test_no_violation_when_count_is_zero(self):
        text = "אין פטנטים של Nonexistent Corp בתחום זה."
        assert consistency_violations(text, {"Nonexistent Corp": 0}) == []

    def test_no_violation_for_unrelated_sentence(self):
        assert consistency_violations("יש לאנדוריל פטנטים רבים.", {"Anduril": 3}) == []

    def test_alternate_negation_phrasing(self):
        text = "לא נרשמו פטנטים של Anduril."
        assert len(consistency_violations(text, {"Anduril": 1})) == 1
