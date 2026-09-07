"""Round 7 QA-loop repair (R7-patents, docs/qa/loop/round_6_judge.md D8 worst #10) -- unit tests
for the term-derived cluster-label quality fix in ``eoa.patents.cluster``:

Finding: round 6 replaced the "לא מסווג" cluster label with a term-derived "אשכול נושאי: <terms>"
label, but the terms were often assignee-name fragments (company names, "inc", "systems", "co") or
generic words rather than technology terms. This round fixes the term pool that feeds that label:

1. the term pool for a TF-IDF sub-cluster is built from title + abstract + (a known CPC code's own)
   title text only, and NEVER from ``row["assignees"]`` -- every assignee-name word is stripped, and
   so is a corporate-suffix stoplist (inc/ltd/llc/corp/co/gmbh/ag/sa/systems/technologies/company/
   university/universitesi/institute/...) and a patent-administrative-boilerplate stoplist
   (assignment/reassignment/assignors/interest/document/details/...).
2. technology nouns from a curated EO/IR vocabulary (assembled from config/taxonomy.yaml's own
   English subdomain terms) are preferred over equally-frequent generic words, and a known
   multi-word EO/IR phrase ("focal plane array") is preferred over its own bare component words.
3. a label term is mapped to Hebrew where the vocabulary has one, else kept in English; the
   up-to-3 displayed terms are ordered Hebrew-first.
4. a cluster's CPC code that has no matching watch topic but does have a known title in this
   project's own data (config/patents.yaml's cpc: list) is labelled from that title instead of the
   bare "אשכול טכנולוגי <code>" fallback.

``eoa.patents.cluster._unclassified_label_he`` never emits "לא מסווג"/"ללא סיווג" once there is at
least one real term (unchanged round-6 contract, re-verified here against the new term pool).

Mirrors tests/unit/test_patents_round5.py's/test_patents_round6.py's own "stub every DB/network
call" convention -- no live DB/Ollama/HTTP calls anywhere in this file. Rows below reproduce the
*real* garbage shapes confirmed live against the ``patents`` table on 2026-09-07 (patent id 62:
title "Lattice mesh", abstract pure USPTO assignment-record boilerplate naming "Anduril Industries
Inc." twice; patent id 14: assignee stored as the placeholder "Europe", abstract naming "Sofradir
EC, Inc." as a third-party reference, not the row's own assignee).
"""

from __future__ import annotations

from eoa.patents.cluster import (
    UNCLASSIFIED_KEY,
    UNCLASSIFIED_LABEL_HE,
    _assignee_name_tokens,
    _boosted_top_terms,
    _cpc_label,
    _display_term_he,
    _term_pool_tokens,
    _unclassified_label_he,
    cluster_patents,
    tfidf_subcluster_unclassified,
)


class _Topic:
    def __init__(self, name_he: str, query: str, cpc: list[str]):
        self.name_he = name_he
        self.query = query
        self.cpc = cpc


# --------------------------------------------------------------------------
# _assignee_name_tokens
# --------------------------------------------------------------------------


class TestAssigneeNameTokens:
    def test_splits_multi_word_assignee_into_lowercase_tokens(self):
        assert _assignee_name_tokens(["Anduril Industries Inc"]) == {"anduril", "industries", "inc"}

    def test_multiple_assignees_all_contribute_tokens(self):
        # "Co" is below _WORD_RE's own 3-char minimum (same tokenizer as everywhere else in this
        # module) and so never becomes its own assignee token -- it is still covered separately by
        # the corporate-suffix stoplist, which matches it directly wherever it occurs standalone.
        tokens = _assignee_name_tokens(["Sabanci Universitesi", "Raytheon Corp"])
        assert tokens == {"sabanci", "universitesi", "raytheon", "corp"}

    def test_none_and_empty_list_both_return_empty_set(self):
        assert _assignee_name_tokens(None) == set()
        assert _assignee_name_tokens([]) == set()


# --------------------------------------------------------------------------
# _term_pool_tokens -- the core round-7 fix
# --------------------------------------------------------------------------


class TestTermPoolTokens:
    def test_real_anduril_assignment_boilerplate_never_leaks_assignee_or_boilerplate_words(self):
        """Real shape confirmed live against patents.id=62 (2026-09-07): the entire abstract is
        USPTO assignment-record boilerplate, not a technical description."""
        row = {
            "n": 62,
            "id": 62,
            "title": "US10506436B1 - Lattice mesh",
            "abstract": (
                "2019-03-07 Assigned to Anduril Industries Inc. reassignment Anduril Industries "
                "Inc. ASSIGNMENT OF ASSIGNORS INTEREST (SEE DOCUMENT FOR DETAILS)."
            ),
            "assignees": ["Anduril"],
            "cpc": [],
        }
        tokens = _term_pool_tokens(row)
        for banned in ("anduril", "industries", "inc", "assignment", "reassignment", "assignors", "interest"):
            assert banned not in tokens, banned
        assert "lattice" in tokens
        assert "mesh" in tokens

    def test_real_europe_placeholder_assignee_and_third_party_reference_handled(self):
        """Real shape confirmed live against patents.id=14: the row's own (bogus) assignee is the
        placeholder "Europe", and the abstract names an unrelated third party ("Sofradir EC,
        Inc.") only as prior-art context -- that third-party mention is not this row's assignee,
        so it is real (if not very useful) text, not an assignee leak; only the row's own
        assignee token ("europe") must be scrubbed."""
        row = {
            "n": 14,
            "id": 14,
            "title": "US20150015759A1 - Self-reset asynchronous pulse frequency modulated droic",
            "abstract": (
                "Another publicly available example of a pulse modulated DROIC has been shown by "
                "Sofradir EC, Inc. in 2010, a well known leading company in FPA and infrared "
                "imaging modules in Europe."
            ),
            "assignees": ["Europe"],
            "cpc": [],
        }
        tokens = _term_pool_tokens(row)
        assert "europe" not in tokens
        assert "droic" in tokens
        assert "infrared" in tokens

    def test_corporate_suffix_stripped_even_when_not_the_row_assignee_itself(self):
        """The corporate-suffix stoplist fires on the bare word regardless of whether it is part
        of *this* row's own assignee -- covering a stray "Systems"/"Inc" that leaked into the
        title/abstract text from elsewhere in the scraped page."""
        row = {
            "n": 1,
            "id": 1,
            "title": "Systems and methods for a thing",
            "abstract": "A widget company co gmbh institute",
            "assignees": [],
            "cpc": [],
        }
        tokens = _term_pool_tokens(row)
        for banned in ("systems", "company", "co", "gmbh", "institute"):
            assert banned not in tokens

    def test_known_eoir_phrase_extracted_as_its_own_multiword_token(self):
        row = {
            "n": 1,
            "id": 1,
            "title": "Focal plane array readout circuit",
            "abstract": "A focal plane array with ROIC for infrared detector applications.",
            "assignees": [],
            "cpc": [],
        }
        tokens = _term_pool_tokens(row)
        assert "focal plane array" in tokens
        assert tokens.count("focal plane array") == 2  # appears once in title, once in abstract

    def test_known_cpc_code_title_text_feeds_the_term_pool(self):
        """Point 1's "... + CPC-title text": a row that does carry a known CPC code (see
        ``_CPC_CODE_TITLES``) gets that code's own English title words added to its term pool,
        even though the title/abstract text alone says nothing technical."""
        row = {
            "n": 1,
            "id": 1,
            "title": "untitled",
            "abstract": "",
            "assignees": [],
            "cpc": ["G01S17"],
        }
        tokens = _term_pool_tokens(row)
        assert "lidar" in tokens

    def test_unknown_cpc_code_contributes_no_extra_words(self):
        row = {"n": 1, "id": 1, "title": "widget", "abstract": "", "assignees": [], "cpc": ["Z99Z99"]}
        tokens = _term_pool_tokens(row)
        assert tokens == ["widget"]


# --------------------------------------------------------------------------
# _boosted_top_terms -- prefer technology nouns and phrases (point 2)
# --------------------------------------------------------------------------


class TestBoostedTopTerms:
    def test_vocab_unigram_wins_over_a_slightly_higher_weight_generic_word(self):
        vec = {"widget": 1.5, "gimbal": 1.0, "alpha": 0.9}
        assert _boosted_top_terms(vec, 2) == ["gimbal", "widget"]

    def test_known_phrase_wins_over_its_own_bare_component_words(self):
        vec = {"focal plane array": 1.0, "focal": 1.0, "plane": 1.0, "array": 1.0}
        assert _boosted_top_terms(vec, 1) == ["focal plane array"]

    def test_non_vocab_terms_still_rank_by_plain_weight_and_break_ties_alphabetically(self):
        vec = {"zebra": 1.0, "alpha": 1.0}
        assert _boosted_top_terms(vec, 2) == ["alpha", "zebra"]

    def test_empty_vector_returns_empty_list(self):
        assert _boosted_top_terms({}, 3) == []


# --------------------------------------------------------------------------
# _display_term_he / _unclassified_label_he -- Hebrew mapping + ordering (point 3)
# --------------------------------------------------------------------------


class TestDisplayTermHe:
    def test_known_unigram_maps_to_hebrew(self):
        assert _display_term_he("detector") == "גלאי"

    def test_known_phrase_maps_to_hebrew(self):
        assert _display_term_he("focal plane array") == "מערך מישור מוקד"

    def test_unknown_term_kept_in_english(self):
        assert _display_term_he("widget") == "widget"
        assert _display_term_he("counter drone") == "counter drone"


class TestUnclassifiedLabelHeTermMapping:
    def test_mixed_terms_are_translated_and_ordered_hebrew_first(self):
        label = _unclassified_label_he(["widget", "detector", "focal plane array"])
        assert label == "אשכול נושאי: גלאי / מערך מישור מוקד / widget"

    def test_never_emits_the_unclassified_substring_once_any_real_term_exists(self):
        label = _unclassified_label_he(["thermal"])
        assert "לא מסווג" not in label
        assert "ללא סיווג" not in label

    def test_all_untranslatable_terms_preserve_round6_behavior(self):
        # regression guard: round 6's own exact fixture/expectation must still hold verbatim.
        assert (
            _unclassified_label_he(["sensor", "drone", "counter"]) == "אשכול נושאי: sensor / drone / counter"
        )

    def test_empty_terms_still_falls_back_to_bare_label(self):
        assert _unclassified_label_he([]) == UNCLASSIFIED_LABEL_HE


# --------------------------------------------------------------------------
# _cpc_label -- CPC-title fallback (point 4)
# --------------------------------------------------------------------------


class TestCpcLabelTitleFallback:
    def test_known_untopic_ed_code_uses_its_own_cpc_title_instead_of_bare_code(self):
        label = _cpc_label("G01S17", topics=[])
        assert label != "אשכול טכנולוגי G01S17"
        assert "לידאר" in label or "laser" in label.lower()

    def test_matching_watch_topic_still_wins_over_the_cpc_title_fallback(self):
        topic = _Topic("שם מוגדר מראש", "query text", ["G01S17"])
        assert _cpc_label("G01S17", topics=[topic]) == "שם מוגדר מראש"

    def test_truly_unknown_code_keeps_the_bare_fallback(self):
        assert _cpc_label("Z99Z99", topics=[]) == "אשכול טכנולוגי Z99Z99"


# --------------------------------------------------------------------------
# end-to-end regression through cluster_patents / tfidf_subcluster_unclassified
# --------------------------------------------------------------------------


class TestEndToEndRealShapeRegression:
    def test_anduril_assignment_boilerplate_row_never_gets_an_assignee_name_label(self):
        rows = [
            {
                "n": 62,
                "id": 62,
                "title": "US10506436B1 - Lattice mesh",
                "abstract": (
                    "2019-03-07 Assigned to Anduril Industries Inc. reassignment Anduril "
                    "Industries Inc. ASSIGNMENT OF ASSIGNORS INTEREST (SEE DOCUMENT FOR DETAILS)."
                ),
                "assignees": ["Anduril"],
                "cpc": [],
                "publication_date": None,
            }
        ]
        clusters = cluster_patents(rows)
        unclassified = [c for c in clusters if c.key.startswith(UNCLASSIFIED_KEY)]
        assert len(unclassified) == 1
        assert "anduril" not in unclassified[0].label_he.lower()
        assert "industries" not in unclassified[0].label_he.lower()

    def test_real_eoir_content_beats_a_polluting_assignee_name_for_the_label(self):
        rows = [
            {
                "n": 63,
                "id": 63,
                "title": "US99999999B1 - Focal plane array readout",
                "abstract": (
                    "A focal plane array with readout integrated circuit ROIC for infrared "
                    "detector applications."
                ),
                "assignees": ["Some Systems Technologies Inc"],
                "cpc": [],
                "publication_date": None,
            }
        ]
        subs = tfidf_subcluster_unclassified(rows)
        assert len(subs) == 1
        terms = subs[0]["label_terms"]
        assert "some" not in terms
        assert "systems" not in terms
        assert "technologies" not in terms
        assert "inc" not in terms
        assert "focal plane array" in terms
