"""Weekly meta section: net feedback deltas, no rate-and-restore noise, capped."""

import datetime as dt

from eoa.report.weekly import _META_MAX_DELTAS, net_feedback_deltas


def _fb(i, item, agent, user, minute):
    return {
        "id": i,
        "item_id": item,
        "agent_level": agent,
        "user_level": user,
        "created_at": dt.datetime(2026, 9, 6, 12, minute),
        "item_title": f"item {item}",
    }


def test_toggle_and_restore_nets_to_nothing() -> None:
    rows = [_fb(1, 10, "yellow", "red", 1), _fb(2, 10, "red", "yellow", 2)]
    assert net_feedback_deltas(rows) == []


def test_real_change_kept_with_first_agent_level() -> None:
    rows = [_fb(1, 10, "yellow", "red", 1), _fb(2, 10, "red", "orange", 2)]
    out = net_feedback_deltas(rows)
    assert len(out) == 1 and out[0]["agent_level"] == "yellow" and out[0]["user_level"] == "orange"


def test_capped_and_newest_first() -> None:
    rows = [_fb(i, i, "yellow", "red", i % 60) for i in range(1, 40)]
    out = net_feedback_deltas(rows)
    assert len(out) == _META_MAX_DELTAS
    assert out[0]["created_at"] >= out[-1]["created_at"]
