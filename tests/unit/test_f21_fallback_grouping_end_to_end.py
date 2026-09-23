"""F21 (SOL-AUDIT-2026-09-24 / SOL-REVIEW-2026-09-24 review): end-to-end fallback grouping by
`story_id`.

`tests/unit/test_reports_round4.py::TestClusterItems::test_story_id_groups_items_with_unrelated_titles_and_no_dedup_of`
already covers the pure `eoa.report.clustering.cluster_items` function directly. What the review
still flags as missing is END TO END: that each report kind's own collector row shape (as it
actually flows out of `collect_items`/`collect_week_items`/the bd_territory collector -- i.e.
already carrying `story_id`, `lang`, and a registry `n`) reaches the SAME fallback-synthesis
sentence builder each report kind calls when the LLM draft fails
(`_fallback_top_item_sentences`, identical in `eoa.report.daily`/`eoa.report.weekly`/
`eoa.report.bd_territory`) and comes out as ONE grouped sentence citing both outlets' `n`s -- not
two separate, redundant sentences -- even though the two items' TITLES are completely dissimilar
(the exact case `story_id` grouping exists for: paraphrased headlines in different outlets/
languages that plain title-similarity would never catch).

Run with: ``PYTHONPATH=agent PYTHONUTF8=1 python -m pytest tests/unit/test_f21_fallback_grouping_end_to_end.py -q``
"""

from __future__ import annotations

import datetime as dt

from eoa.report import bd_territory, daily


def _collector_row(
    *, id: int, n: int, title: str, story_id: int, level: str = "red", lang: str = "en"
) -> dict:
    """Shaped exactly like a row `collect_items`/`collect_week_items`/the bd_territory collector
    return -- `story_id`/`lang` included (F21's own fix: all four collectors now SELECT them),
    already carrying its registry `n` (assigned before fallback synthesis ever runs, same as in
    each report builder's real pipeline)."""
    return {
        "id": id,
        "n": n,
        "title": title,
        "level": level,
        "lang": lang,
        "story_id": story_id,
        "score": 5,
        "summary_he": "תקציר בעברית.",
        "so_what_he": "המשמעות העסקית.",
        "published_at": dt.date(2026, 9, 1),
    }


class TestDailyFallbackGroupsByStoryIdAcrossDissimilarTitles:
    def test_two_dissimilar_titled_outlets_same_story_id_become_one_sentence(self) -> None:
        items = [
            _collector_row(
                id=10, n=1, title="Rafael Integrates SPICE 1000 Precision Weapon With F-35",
                story_id=5, lang="en",
            ),
            _collector_row(
                id=11, n=2, title="רפאל: משלבים את ה-SPICE 1000 עם מטוסי F-35 בחיל האוויר",
                story_id=5, lang="he",
            ),
        ]
        sentences = daily._fallback_top_item_sentences(items, limit=10)
        assert len(sentences) == 1, (
            f"expected the two same-story_id, dissimilar-titled items to fold into ONE fallback "
            f"sentence, got {len(sentences)}: {[s.text_he for s in sentences]}"
        )
        assert set(sentences[0].cites) == {1, 2}

    def test_dissimilar_titles_with_different_story_ids_stay_separate(self) -> None:
        """Control: without a shared `story_id` (and no dedup_of/title similarity), two genuinely
        different stories must still render as two separate sentences -- this end-to-end path must
        not over-merge."""
        items = [
            _collector_row(id=20, n=1, title="Elbit wins radar upgrade contract", story_id=20),
            _collector_row(id=21, n=2, title="Completely unrelated submarine procurement news", story_id=21),
        ]
        sentences = daily._fallback_top_item_sentences(items, limit=10)
        assert len(sentences) == 2


class TestBdTerritoryFallbackGroupsByStoryIdAcrossDissimilarTitles:
    def test_two_dissimilar_titled_outlets_same_story_id_become_one_sentence(self) -> None:
        items = [
            _collector_row(
                id=30, n=1, title="IAI unveils new maritime patrol radar", story_id=7, lang="en",
            ),
            _collector_row(
                id=31, n=2, title="התעשייה האווירית חושפת מכ\"ם סיור ימי חדש", story_id=7, lang="he",
            ),
        ]
        sentences = bd_territory._fallback_top_item_sentences(items, limit=10)
        assert len(sentences) == 1, (
            f"expected one grouped sentence, got {len(sentences)}: {[s.text_he for s in sentences]}"
        )
        assert set(sentences[0].cites) == {1, 2}
