import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from eoa.config import ChainEntryCfg, LlmProvidersCfg
from eoa.errors import DeadlineExceeded, LeaseLost, ResourceUnavailable
from eoa.llm import chain, ollama_client
from eoa.llm.providers.base import ProviderResult
from eoa.resources import gate as gate_module


def config(**kwargs):
    return LlmProvidersCfg(mode="local", auto_cloud_fallback=True,
                           chains={"resident": [ChainEntryCfg(provider="agy", model="test")]}, **kwargs)


@pytest.mark.parametrize("failure", [ResourceUnavailable("paused"), ConnectionError("offline")])
def test_unavailable_local_automatically_uses_cloud(monkeypatch, failure):
    local = Mock(side_effect=failure)
    remote = Mock()
    remote.is_available.return_value = True
    remote.chat.return_value = ProviderResult("cloud answer", "test", "agy")
    monkeypatch.setattr(chain, "_build_provider", lambda entry: remote)
    monkeypatch.setattr(chain, "_record", lambda *args: None)
    result, attempts = chain.run_chain("resident", config().effective_chain("resident"), local, messages=[])
    assert result.content == "cloud answer"
    assert [a.provider for a in attempts] == ["ollama", "agy"]
    assert attempts[-1].fell_back_from == "ollama"
    local.assert_called_once()
    remote.chat.assert_called_once()


@pytest.mark.parametrize("failure", [DeadlineExceeded("expired"), LeaseLost("lost")])
def test_cancellation_never_triggers_cloud(monkeypatch, failure):
    remote = Mock()
    monkeypatch.setattr(chain, "_build_provider", remote)
    with pytest.raises(type(failure)):
        chain.run_chain("resident", config().effective_chain("resident"), Mock(side_effect=failure), messages=[])
    remote.assert_not_called()


def test_cloud_forbidden_and_unconfigured_roles_stay_local():
    assert [e.provider for e in config(allow_cloud=False).effective_chain("resident")] == ["ollama"]
    assert [e.provider for e in config().effective_chain("embed")] == ["ollama"]


def test_pipeline_routes_local_mode_through_automatic_chain(monkeypatch):
    monkeypatch.setattr(ollama_client, "settings", lambda: SimpleNamespace(llm_providers=config()))
    monkeypatch.setattr(ollama_client, "_in_pipeline_process", lambda: True)
    dispatch = Mock(return_value="cloud result")
    monkeypatch.setattr(ollama_client, "_dispatch_chain", dispatch)
    assert ollama_client.chat("resident", []) == "cloud result"
    assert [e.provider for e in dispatch.call_args.kwargs["chain"]] == ["ollama", "agy"]


def test_local_success_does_not_send_content_to_cloud(monkeypatch):
    remote = Mock()
    monkeypatch.setattr(chain, "_build_provider", remote)
    monkeypatch.setattr(chain, "_record", lambda *args: None)
    result, _ = chain.run_chain("resident", config().effective_chain("resident"),
                               lambda: ProviderResult("local answer", "local", "ollama"), messages=[])
    assert result.content == "local answer"
    remote.assert_not_called()


def test_real_pause_gate_routes_to_cloud_without_ollama_http(monkeypatch):
    gate_module.LOCAL_INFERENCE_PAUSE_FILE.touch()
    monkeypatch.setattr(ollama_client, "settings", lambda: SimpleNamespace(llm_providers=config()))
    monkeypatch.setattr(ollama_client, "_in_pipeline_process", lambda: True)
    client = Mock(side_effect=AssertionError("No local HTTP while paused"))
    monkeypatch.setattr(ollama_client, "_client", client)
    remote = Mock()
    remote.is_available.return_value = True
    remote.chat.return_value = ProviderResult("cloud answer", "test", "agy")
    monkeypatch.setattr(chain, "_build_provider", lambda entry: remote)
    monkeypatch.setattr(chain, "_record", lambda *args: None)
    result = ollama_client.chat("resident", [{"role": "user", "content": "test"}])
    assert result.content == "cloud answer"
    client.assert_not_called()
    assert gate_module.LOCAL_INFERENCE_PAUSE_FILE.exists()


def test_cloud_batch_cap_preserves_item_ids(monkeypatch):
    class Output(BaseModel):
        value: str

    calls = []

    def respond(role, schema, messages, **kwargs):
        ids = [int(x) for x in re.findall(r"### item_id=(\d+)", messages[-1]["content"])]
        calls.append(ids)
        return schema.model_validate({"items": [{"item_id": i, "value": str(i)} for i in ids]})

    monkeypatch.setattr(ollama_client, "settings", lambda: SimpleNamespace(llm_providers=config(cloud_batch_size=5)))
    monkeypatch.setattr(ollama_client, "chat_structured", respond)
    result = ollama_client.chat_structured_batch("resident", Output, [(i, "text") for i in range(12)], system="test")
    assert [len(ids) for ids in calls] == [5, 5, 2]
    assert {i: output.value for i, output in result.items()} == {i: str(i) for i in range(12)}
