"""Error classification: transient (retry) vs permanent (no retry)."""

from __future__ import annotations


class MDIFError(Exception):
    error_class = "permanent"

    def __init__(self, message: str, error_class: str | None = None):
        super().__init__(message)
        if error_class:
            self.error_class = error_class


class TransientError(MDIFError):
    error_class = "transient"


class PermanentError(MDIFError):
    error_class = "permanent"


class ConfigError(PermanentError):
    error_class = "config"


class ValidationError(PermanentError):
    error_class = "validation"


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, MDIFError):
        return exc.error_class
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    transient_tokens = (
        "timeout",
        "timed out",
        "connection",
        "temporarily",
        "429",
        "503",
        "502",
        "reset",
        "unavailable",
    )
    if any(t in msg for t in transient_tokens) or "timeout" in name:
        return "transient"
    return "permanent"
