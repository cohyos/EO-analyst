"""API-level exception type mapped to the ``{"error": {...}}`` JSON shape.

Routes raise these (via the factory helpers below) instead of FastAPI's
``HTTPException`` so every non-2xx response — including framework-generated
404s and validation errors, handled separately in ``app.py`` — shares one
error envelope: ``{"error": {"code", "message_he", "detail"}}``.
"""

from __future__ import annotations

from typing import Any


class APIError(Exception):
    """Raised by routes; translated to a JSON error response by `app.py`."""

    def __init__(self, status_code: int, code: str, message_he: str, detail: Any = None) -> None:
        super().__init__(message_he)
        self.status_code = status_code
        self.code = code
        self.message_he = message_he
        self.detail = detail


def not_found(message_he: str = "הפריט לא נמצא", detail: Any = None) -> APIError:
    return APIError(404, "not_found", message_he, detail)


def bad_request(message_he: str, detail: Any = None) -> APIError:
    return APIError(400, "bad_request", message_he, detail)


def not_implemented(message_he: str = "התכונה טרם מומשה", detail: Any = None) -> APIError:
    return APIError(501, "not_implemented", message_he, detail)
