"""`eoa.llm.providers.cli._parse_agy` -- the agy CLI's JSON envelope.

2026-09-22: on the night of 2026-09-21 both the `light` and the `resident` chains exhausted into
the (paused) local leg and deferred the deep_search/analyze stages, because agy answered
`status: ERROR` while still carrying a complete, usable `response` payload and the parser rejected
it outright. A non-empty response is now used with a warning -- schema validation downstream stays
the real gate.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from eoa.errors import CliProviderError
from eoa.llm.providers.cli import _parse_agy


def _proc(payload: dict, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["agy"], returncode=returncode, stdout=json.dumps(payload), stderr=""
    )


class TestParseAgy:
    def test_success_returns_content_and_usage(self) -> None:
        content, usage = _parse_agy(
            _proc({"status": "SUCCESS", "response": " hello ", "usage": {"input_tokens": 3}})
        )
        assert content == "hello"
        assert usage == {"input_tokens": 3}

    def test_error_status_with_usable_response_is_accepted(self) -> None:
        content, _usage = _parse_agy(
            _proc({"status": "ERROR", "response": '```json\n{"verdict": "yes"}\n```'})
        )
        assert "verdict" in content

    def test_error_status_without_response_still_raises(self) -> None:
        with pytest.raises(CliProviderError, match="status=ERROR"):
            _parse_agy(_proc({"status": "ERROR", "response": ""}))

    def test_empty_response_on_success_raises(self) -> None:
        with pytest.raises(CliProviderError, match="empty response"):
            _parse_agy(_proc({"status": "SUCCESS", "response": ""}))

    def test_non_json_stdout_raises(self) -> None:
        proc = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout="not json", stderr="")
        with pytest.raises(CliProviderError, match="non-JSON"):
            _parse_agy(proc)

    def test_json_list_instead_of_object_raises_cli_provider_error(self) -> None:
        """F26 (audit 2026-09-24): syntactically valid JSON that isn't an object (e.g. a bare
        list) used to raise a bare AttributeError from `data.get(...)` -- not `CliProviderError`,
        so `eoa.llm.chain.FALLBACK_EXCEPTIONS` never caught it and the whole chain aborted instead
        of moving on to the next leg."""
        proc = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout=json.dumps([1, 2, 3]), stderr="")
        with pytest.raises(CliProviderError, match="not an object"):
            _parse_agy(proc)
