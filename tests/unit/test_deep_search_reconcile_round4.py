"""Round-3 judge: one reconciled answer per repeated investigation question."""

from eoa.report.daily import reconcile_deep_search_reruns


def _e(job, q, outcome, item=None):
    return {"job_id": job, "trigger_item_id": item, "question": q, "outcome": outcome, "answer_he": f"a{job}"}


def test_best_outcome_wins_and_reruns_are_annotated() -> None:
    rows = [
        _e(3, "מה סטטוס Iron Beam?", "not_found"),
        _e(2, "מה  סטטוס iron beam?", "found"),
        _e(1, "מה סטטוס Iron Beam?", "not_found"),
    ]
    out = reconcile_deep_search_reruns(rows)
    assert len(out) == 1 and out[0]["job_id"] == 2
    assert out[0]["rerun_count"] == 3 and "3 פעמים" in out[0]["rerun_note_he"]


def test_ties_keep_newest_and_distinct_questions_kept() -> None:
    rows = [_e(9, "q1", "partial"), _e(8, "q1", "partial"), _e(7, "q2", "not_found", item=5)]
    out = reconcile_deep_search_reruns(rows)
    assert [r["job_id"] for r in out] == [9, 7]
    assert "rerun_count" not in out[1]
