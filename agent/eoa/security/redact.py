"""Shared secret-redaction helper (Q2-3, Q2-15).

Originally lived in ``eoa.llm.providers.api`` (Q2-3, 2026-09-06) where it scrubbed cloud-LLM
API errors before they hit a log line or a client-visible exception message. Moved here so
``eoa.mcp_servers.*``, ``eoa.mcp.client``, and ``eoa.mcp.registry`` can share the exact same
patterns (Q2-15, 2026-09-06) instead of re-implementing (and inevitably drifting from) their own
copy -- every MCP data-source error path (procurement.py's SAM.gov/Congress.gov calls, janes.py,
patents.py, the generic stdio client, and the tool registry's audit log) runs error text through
this before it is logged, persisted to ``mcp_calls.error``, or returned to the model.

``eoa.llm.providers.api`` still imports ``redact_secrets`` from here (re-exported via a bare
import) so existing call sites and ``from eoa.llm.providers.api import redact_secrets`` keep
working unchanged.
"""

from __future__ import annotations

import re

# Q2-3 (2026-09-06): patterns for secrets that must never reach a log line or an exception
# message surfaced to a caller -- a `key=`/`api_key=`/`token=` query param (the old Gemini auth
# mechanism, SAM.gov/Congress.gov API keys, etc.), raw API key literals (`AIza...`, `sk-...`), and
# bearer tokens.
_SECRET_QUERY_PARAM_RE = re.compile(r"(?i)([?&](?:key|api_key|token)=)[^&\s\"']+")
_SECRET_AIZA_RE = re.compile(r"AIza[0-9A-Za-z_\-]{10,}")
_SECRET_SK_RE = re.compile(r"sk-[A-Za-z0-9_\-]{10,}")
_SECRET_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]+")


def redact_secrets(text: str) -> str:
    """Scrub API keys/tokens out of ``text`` before it is logged, persisted, or raised.

    Applied at every point an upstream HTTP exception, response body, or error string might
    echo back a key embedded in a URL, header, or the exception's own ``str()`` -- so a leaked
    key never lands in ``runtime/*.log``, the ``mcp_calls.error`` column, or a message returned
    to the model.
    """
    if not text:
        return text
    redacted = _SECRET_QUERY_PARAM_RE.sub(r"\1[REDACTED]", text)
    redacted = _SECRET_AIZA_RE.sub("[REDACTED]", redacted)
    redacted = _SECRET_SK_RE.sub("[REDACTED]", redacted)
    redacted = _SECRET_BEARER_RE.sub(r"\1[REDACTED]", redacted)
    return redacted
