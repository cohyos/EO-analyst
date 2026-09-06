"""Round 4 P1 fix (incident 2026-09-06 16:15-16:24): every API request hung for 60-90s+ because
`POST /api/ask`'s async streaming generator drove a synchronous, blocking iterator (`chat_stream`,
whose first `next()` blocks inside `ResourceGate.acquire()`) directly inside the event loop --
freezing the whole single-worker uvicorn process (a real live incident: `GET /api/status` itself
timed out at 60s while the gate queued for 7+ minutes).

Covers, per the task brief:
  1. `POST /api/ask` no longer blocks a concurrent `GET /api/status` (`eoa.api.routes.ask.ask`'s
     `gen()` now offloads every `chat_stream` `next()` call to the threadpool via
     `run_in_threadpool`).
  2. The new interactive gate budget (`resources.interactive_wait_s`, default 20s):
     `ResourceGate._acquire_locked` uses it (instead of the patient `queue_timeout_min`) as the
     queue deadline for `interactive=True` callers, and `/api/ask` turns the resulting
     `ResourceUnavailable` into a clear Hebrew SSE message instead of hanging.
  3. `GET /api/reports` already returns lean summary cards (no report body) -- `_report_card`
     builds an explicit whitelist of fields rather than spreading the DB row, so a bloated/extra
     column on the `reports` row (e.g. an inline body some future migration might add) can never
     leak into the list endpoint; only `get_report` (detail) reads the HTML body. This is locked
     in as a regression test.

Run with: ``PYTHONPATH=agent .venv\\Scripts\\python -m pytest tests/unit/test_api_round4_gate_and_reports.py -q``
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
import time
import types
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from eoa.config import ModelSpec
from eoa.errors import ResourceUnavailable
from eoa.resources.gate import ResourceGate
from eoa.resources.gpu import GpuStatus, HostStatus

# ---------------------------------------------------------------------------------------------
# 1 + 2: end-to-end, through the real FastAPI app (TestClient)
# ---------------------------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from eoa import db

    monkeypatch.setattr(db, "get_pool", lambda: object())
    monkeypatch.setattr(db, "close_pool", lambda: None)

    from eoa.api.app import create_app

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def _sse_events(body: str) -> list[dict]:
    events = []
    for chunk in body.split("\n\n"):
        line = next((ln for ln in chunk.split("\n") if ln.startswith("data:")), None)
        if not line:
            continue
        events.append(json.loads(line[len("data:") :].strip()))
    return events


class _FakeChatResult:
    """Minimal stand-in for `ollama_client.ChatResult` -- only `.content` is read by the
    citation-repair pass (`eoa.api.routes.ask._run_citation_repair`)."""

    def __init__(self, content: str = "") -> None:
        self.content = content


CITATIONS = [
    {
        "n": 1,
        "item_id": 101,
        "title": "מקור א",
        "url": "https://a.test",
        "level": "red",
        "source_name": "Globes",
    },
]


def _mock_ask_basics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Everything `/api/ask` needs before it starts streaming, stubbed with no DB/Ollama."""
    from eoa.api import services
    from eoa.llm import ollama_client

    monkeypatch.setattr(services, "ask_retrieve", lambda *a, **k: [])
    monkeypatch.setattr(services, "ask_build_messages", lambda *a, **k: ([], CITATIONS))
    monkeypatch.setattr(ollama_client, "resolve_provider_info", lambda provider: ("ollama", "resident"))
    monkeypatch.setattr(ollama_client, "chat", lambda *a, **k: _FakeChatResult(""))


def _mock_status_basics(monkeypatch: pytest.MonkeyPatch) -> None:
    from eoa.api import services

    monkeypatch.setattr(
        services,
        "services_status",
        lambda: {"postgres": True, "ollama": True, "searxng": True, "ntfy": True},
    )
    monkeypatch.setattr(
        services,
        "pipeline_status",
        lambda: {
            "current_job": None,
            "queue_depth": 0,
            "stage": None,
            "night_window": False,
            "next_run_at": None,
            "last_run": None,
        },
    )


class TestAskDoesNotBlockConcurrentStatus:
    """Item 1 of the P1 fix: a slow `/api/ask` (stubbed to block 1.5s in the *sync* LLM path,
    exactly like the real incident's `chat_stream` blocking inside the gate) must not prevent a
    concurrent `GET /api/status` from returning promptly."""

    def test_slow_ask_stream_does_not_block_concurrent_status(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.llm import ollama_client

        _mock_ask_basics(monkeypatch)
        _mock_status_basics(monkeypatch)

        started = threading.Event()
        sleep_seconds = 1.5

        def slow_chunks() -> Iterator[str]:
            started.set()
            time.sleep(sleep_seconds)  # the "sync path" sleep the task brief asks the test to stub
            yield "תשובה [1]."

        monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: slow_chunks())

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            ask_future = pool.submit(client.post, "/api/ask", json={"question": "שאלה"})
            assert started.wait(timeout=5), "ask request never reached the slow LLM stub"

            t0 = time.monotonic()
            status_resp = client.get("/api/status")
            status_elapsed = time.monotonic() - t0

            ask_resp = ask_future.result(timeout=10)

        assert status_resp.status_code == 200
        assert "gate" in status_resp.json()
        assert status_elapsed < sleep_seconds, (
            f"GET /api/status took {status_elapsed:.2f}s while a slow /api/ask was in flight "
            f"(sleep={sleep_seconds}s) -- the event loop was blocked, reproducing the incident"
        )

        assert ask_resp.status_code == 200
        events = _sse_events(ask_resp.text)
        assert "".join(e["text"] for e in events if e["type"] == "token") == "תשובה [1]."
        assert events[-1]["type"] == "done"


class TestInteractiveGateBusyEvent:
    """Item 2 of the P1 fix: when the gate can't admit the model within the interactive budget,
    `/api/ask` must emit a clear Hebrew message and end the stream -- never hang."""

    def test_gate_busy_emits_hebrew_message_and_ends_stream_cleanly(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.api.routes import ask as ask_route
        from eoa.llm import ollama_client

        _mock_ask_basics(monkeypatch)

        def broken_stream() -> Iterator[str]:
            # `chat_stream` is a generator: the gate's `ResourceUnavailable` is raised from
            # `gate().acquire()`, which runs before the first `yield` -- i.e. on the very first
            # `next()` call, exactly like the real path.
            raise ResourceUnavailable("VRAM unavailable for resident after 20s")
            yield ""  # pragma: no cover -- unreachable; makes this a generator function

        monkeypatch.setattr(ollama_client, "chat_stream", lambda *a, **k: broken_stream())

        repair_calls: list[object] = []
        monkeypatch.setattr(
            ollama_client, "chat", lambda *a, **k: repair_calls.append(1) or _FakeChatResult("")
        )

        r = client.post("/api/ask", json={"question": "שאלה"})
        assert r.status_code == 200
        events = _sse_events(r.text)

        token_text = "".join(e["text"] for e in events if e["type"] == "token")
        assert ask_route._GATE_BUSY_MESSAGE in token_text

        gate_busy_events = [e for e in events if e["type"] == "gate_busy"]
        assert len(gate_busy_events) == 1
        assert gate_busy_events[0]["message"] == ask_route._GATE_BUSY_MESSAGE

        sources_events = [e for e in events if e["type"] == "sources"]
        assert len(sources_events) == 1
        assert sources_events[0]["items"] == []

        assert events[-1]["type"] == "done"
        # the citation-repair pass (a second, non-streamed LLM call) must never fire on the
        # gate-busy early-return path -- there is no answer to repair.
        assert not repair_calls


# ---------------------------------------------------------------------------------------------
# 2 (gate unit level): the interactive budget actually changes the queue deadline
# ---------------------------------------------------------------------------------------------


def _fake_resources(**overrides):
    defaults = dict(
        vram_total_mb=12227,
        vram_safety_margin_mb=1200,
        min_free_ram_mb=8000,
        min_free_disk_gb=20,
        warn_free_disk_gb=40,
        gpu_temp_pause_c=83,
        gpu_temp_stop_c=88,
        queue_backoff_seconds=[5, 10, 30, 60],
        queue_timeout_min=20,
        interactive_wait_s=20,
        min_loaded_seconds=300,
        polite_mode=types.SimpleNamespace(external_gpu_util_threshold=25, enabled_outside_night_window=True),
    )
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


class _FakeSettings:
    def __init__(self, models: dict[str, ModelSpec], resources: types.SimpleNamespace) -> None:
        self._models = models
        self.resources = resources
        self.ollama_url = "http://127.0.0.1:11434"

    def model(self, role: str) -> ModelSpec:
        return self._models[role]


RESIDENT_SPEC = ModelSpec(
    key="resident", ollama="gemma4:12b", vendor="Google", origin="US", license="Apache-2.0", est_vram_mb=8200
)


def _starved_host() -> HostStatus:
    """GPU present but with far less free VRAM than `RESIDENT_SPEC` needs, and nothing eligible
    to unload -- the gate can never admit the model, only queue-and-eventually-timeout."""
    return HostStatus(
        at=datetime.now(tz=UTC),
        gpu=GpuStatus(vram_total_mb=12227, vram_used_mb=12000, util_pct=90, temp_c=60, available=True),
        ram_free_mb=32000,
        ram_total_mb=64000,
        disk_free_gb=100,
        loaded_models=[],
    )


class TestInteractiveGateBudget:
    def test_interactive_call_times_out_after_interactive_wait_s_not_queue_timeout_min(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        resource_gate = ResourceGate()
        resources = _fake_resources(interactive_wait_s=20)
        monkeypatch.setattr(
            "eoa.resources.gate.settings", lambda: _FakeSettings({"resident": RESIDENT_SPEC}, resources)
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda *_a, **_kw: _starved_host())

        clock = {"t": 0.0}
        monkeypatch.setattr("eoa.resources.gate.time.monotonic", lambda: clock["t"])

        def fake_sleep(seconds: float) -> None:
            clock["t"] += seconds

        resource_gate._sleep = fake_sleep

        with pytest.raises(ResourceUnavailable):
            resource_gate.acquire("resident", interactive=True)

        # honored the short interactive budget, nowhere near the 20-minute batch deadline
        assert clock["t"] < 60, (
            f"interactive call waited {clock['t']}s -- expected << queue_timeout_min (1200s)"
        )

    def test_batch_call_under_the_same_starvation_waits_the_full_queue_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Contrast case: a non-interactive (nightly/pipeline) caller is unaffected by
        `interactive_wait_s` and keeps the patient `queue_timeout_min` behaviour."""
        resource_gate = ResourceGate()
        # #11 (test_gate.py's own convention): force the batch-window path so a non-interactive
        # `acquire()` never falls into `is_batch_window()` -> `_in_night_window()`, which needs a
        # real `settings().timezone` that `_FakeSettings` (duck-typed, gate-only fields) doesn't
        # carry.
        resource_gate.force_night_mode = True
        resources = _fake_resources(interactive_wait_s=20, queue_timeout_min=20)
        monkeypatch.setattr(
            "eoa.resources.gate.settings", lambda: _FakeSettings({"resident": RESIDENT_SPEC}, resources)
        )
        monkeypatch.setattr("eoa.resources.gate.telemetry.snapshot", lambda *_a, **_kw: _starved_host())

        clock = {"t": 0.0}
        monkeypatch.setattr("eoa.resources.gate.time.monotonic", lambda: clock["t"])

        def fake_sleep(seconds: float) -> None:
            clock["t"] += seconds

        resource_gate._sleep = fake_sleep

        with pytest.raises(ResourceUnavailable):
            resource_gate.acquire("resident", interactive=False)

        # queue_timeout_min=20 minutes -- far past the 20s interactive budget
        assert clock["t"] >= 20 * 60


# ---------------------------------------------------------------------------------------------
# 3: GET /api/reports stays a lean summary list; only the detail endpoint carries the HTML body
# ---------------------------------------------------------------------------------------------


class TestReportsListStaysLight:
    def test_report_card_never_includes_the_html_body(self) -> None:
        """`_report_card` builds an explicit whitelist of fields -- even if a `reports` row ever
        grows an inline body/content column (instead of just a `path_html` file reference), the
        list endpoint must never surface it; only `get_report` (detail) reads the file."""
        from eoa.api import services

        row = {
            "id": 1,
            "kind": "daily",
            "period_start": None,
            "period_end": None,
            "path_docx": "x.docx",
            "path_md": "x.md",
            "path_html": "x.html",
            "qa_passed": True,
            "created_at": None,
            "items_included": [1, 2, 3],
            "territory": None,
            # simulates a hypothetical future bloated column -- must never leak into the card
            "html": "<html>" + ("א" * 50_000) + "</html>",
        }
        card = services._report_card(row)
        assert "html" not in card
        assert card["headline_count"] == 3
        assert set(card) == {
            "id",
            "kind",
            "period_start",
            "period_end",
            "path_docx",
            "path_md",
            "path_html",
            "qa_passed",
            "created_at",
            "headline_count",
            "territory",
        }

    def test_list_reports_route_returns_only_light_cards(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from eoa.api import services

        cards = [
            {
                "id": 1,
                "kind": "daily",
                "period_start": None,
                "period_end": None,
                "path_docx": None,
                "path_md": None,
                "path_html": "x.html",
                "qa_passed": True,
                "created_at": "2026-09-06T00:00:00+00:00",
                "headline_count": 5,
                "territory": None,
            }
        ]
        monkeypatch.setattr(services, "list_reports", lambda **kwargs: cards)

        r = client.get("/api/reports")
        assert r.status_code == 200
        body = r.json()
        assert body == cards
        assert "html" not in body[0]

    def test_get_report_detail_does_carry_the_html_body(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Contrast case: the detail endpoint is where the body belongs."""
        from eoa.api import services

        detail = {
            "id": 1,
            "kind": "daily",
            "html": "<html>תוכן הדוח</html>",
            "open_points": [],
            "items_included": [1],
        }
        monkeypatch.setattr(services, "get_report", lambda report_id: detail)

        r = client.get("/api/reports/1")
        assert r.status_code == 200
        assert r.json()["html"] == "<html>תוכן הדוח</html>"
