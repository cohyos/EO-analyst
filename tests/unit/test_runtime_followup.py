import json
import subprocess
from unittest.mock import Mock

import pytest

from eoa.api import services
from eoa.errors import DeadlineExceeded, LeaseLost, ResourceUnavailable
from eoa.fetch.service import _extract_links
from eoa.orchestrator import jobs
from eoa.pipeline import analyze, classify, dedup, triage


def test_installed_css_selector_extracts_source_links():
    html = '<article class="news"><a href="/story">Read</a></article><a href="/other">Other</a>'
    assert _extract_links(html, "https://example.com/news", "article.news", "a") == ["https://example.com/story"]


def test_paused_embeddings_stop_after_one_attempt_and_leave_items_pending(monkeypatch):
    monkeypatch.setattr(dedup, "get_items_for_stage", lambda *a, **k: [{"id": i} for i in range(40)])
    embed = Mock(side_effect=ResourceUnavailable("paused"))
    mark = Mock()
    monkeypatch.setattr(dedup, "embed", embed)
    monkeypatch.setattr(dedup, "mark_stage", mark)
    with pytest.raises(ResourceUnavailable):
        dedup.run_dedup()
    assert embed.call_count == 1
    mark.assert_not_called()


@pytest.mark.parametrize("module", [classify, triage, analyze])
@pytest.mark.parametrize("batch", [True, False])
def test_unavailable_models_are_not_silent_success(monkeypatch, module, batch):
    item = {"id": 1, "security_status": "clean", "domain": "thermal_imaging", "level": "red"}
    monkeypatch.setattr(module, "get_items_for_stage", lambda *a, **k: [item])
    monkeypatch.setattr(module, "is_cloud_batch_mode", lambda: batch)
    if module is classify:
        monkeypatch.setattr(module, "get_items_stuck_unclassified", lambda *a: [])
    if module is analyze:
        monkeypatch.setattr(module, "_content_status_precheck", lambda item: "full")
    name = module.__name__.split('.')[-1]
    call = Mock(side_effect=ResourceUnavailable("provider unavailable"))
    monkeypatch.setattr(module, f"{name}_{'batch' if batch else 'item'}", call)
    mark = Mock()
    monkeypatch.setattr(module, "mark_stage", mark)
    with pytest.raises(ResourceUnavailable):
        getattr(module, f"run_{name}")()
    call.assert_called_once()
    mark.assert_not_called()


@pytest.mark.parametrize("cause", [DeadlineExceeded, LeaseLost])
def test_catchup_stops_after_losing_time_or_ownership(monkeypatch, cause):
    monkeypatch.setattr(jobs, "_tender_items_needing_pipeline", lambda: [1])
    monkeypatch.setattr(dedup, "run_dedup", Mock(side_effect=cause("stop")))
    later = Mock()
    monkeypatch.setattr(classify, "run_classify", later)
    with pytest.raises(cause):
        jobs._post_tenders_catchup()
    later.assert_not_called()


def test_catchup_continues_cloud_work_when_only_embedding_is_paused(monkeypatch):
    monkeypatch.setattr(jobs, "_tender_items_needing_pipeline", lambda: [1])
    monkeypatch.setattr(dedup, "run_dedup", Mock(side_effect=ResourceUnavailable("paused")))
    monkeypatch.setattr(classify, "run_classify", lambda **k: {"done": 1})
    monkeypatch.setattr(triage, "run_triage", lambda **k: {"done": 1})
    stats = jobs._post_tenders_catchup()
    assert stats["embed_dedup"] == {"deferred": "paused"}
    assert stats["triage"] == {"done": 1}
    assert jobs._compute_run_status({"report": {}, "post_tenders_catchup": stats}) == "partial"


@pytest.mark.parametrize("event,detail,expected", [
    ("deadline", {"minutes": 15}, "partial"),
    ("deferred", {"error": "paused"}, "deferred"),
    ("done", {"failed": 375}, "partial"),
    ("done", {"scan": {"sources_failed": 1}}, "partial"),
    ("done", {"failed": 0, "done": 2}, "done"),
])
def test_timeline_does_not_hide_partial_work(monkeypatch, event, detail, expected):
    monkeypatch.setattr(services, "_fetchall", lambda *a: [{"stage": "tenders", "event": event, "detail": detail}])
    assert services._stage_timeline_from_log(1, "partial")["tenders"]["status"] == expected


def test_guard_keeps_conservative_verdict_when_l2_is_paused(monkeypatch):
    from eoa.llm import ollama_client
    from eoa.security import guard

    monkeypatch.setattr(guard, "_l1_score", lambda text: 0.85)
    monkeypatch.setattr(ollama_client, "chat_structured", Mock(side_effect=ResourceUnavailable("paused")))
    result = guard.screen("A plain English source paragraph.")
    assert result.verdict == "flagged"


def test_guard_does_not_swallow_a_stage_deadline(monkeypatch):
    from eoa.llm import ollama_client
    from eoa.security import guard

    monkeypatch.setattr(ollama_client, "chat_structured", Mock(side_effect=DeadlineExceeded("expired")))
    with pytest.raises(DeadlineExceeded):
        guard._l2_judge("source", 1, [])


@pytest.mark.parametrize("kind", ["agy", "claude", "codex"])
def test_cli_empty_output_is_a_provider_failure(kind, tmp_path):
    from eoa.llm.providers.cli import CliProvider, CliProviderError

    output = tmp_path / "response.txt"
    output.write_text("", encoding="utf-8")
    proc = subprocess.CompletedProcess([], 0, stdout='{"status":"SUCCESS"}', stderr="")
    with pytest.raises(CliProviderError, match="empty response"):
        CliProvider(kind)._parse_output(proc, output)


def test_cli_error_message_is_not_hidden_behind_usage_metadata():
    from eoa.llm.providers.cli import CliProvider, CliProviderError

    data = {"usage": {"metadata": "x" * 500}, "is_error": True, "result": "Account quota exhausted"}
    proc = subprocess.CompletedProcess([], 1, stdout=json.dumps(data), stderr="")
    with pytest.raises(CliProviderError, match="Account quota exhausted"):
        CliProvider("claude")._parse_output(proc, None)
