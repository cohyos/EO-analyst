"""Project-wide exception types."""

from __future__ import annotations


class EOAError(Exception):
    """Base class for all EO-Analyst errors."""


class ResourceUnavailable(EOAError):
    """The resource gate could not grant GPU/RAM/disk within the timeout."""


class LLMOutputError(EOAError):
    """The model returned output that failed schema validation after retry."""


class SecurityFlag(EOAError):
    """Content was flagged by the security gate and must not enter the model context."""


class FetchError(EOAError):
    """A source or page could not be fetched."""


class DeadlineExceeded(EOAError):
    """The night-window deadline was reached; the stage must wrap up."""


class ConfigError(EOAError):
    """Configuration is missing or invalid."""


class ModelNotAllowed(EOAError):
    """A model outside the Western-origin allow-list (or with a changed digest) was requested."""
