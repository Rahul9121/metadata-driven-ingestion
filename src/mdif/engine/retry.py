"""Retry helper with exponential backoff and optional jitter."""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from mdif.errors import PermanentError, TransientError, classify_exception

T = TypeVar("T")


@dataclass
class AttemptRecord:
    attempt: int
    status: str
    error_class: str | None = None
    error_message: str | None = None
    duration_ms: float = 0.0
    slept_ms: float = 0.0


@dataclass
class RetryResult:
    value: Any = None
    success: bool = False
    attempts: list[AttemptRecord] = field(default_factory=list)
    final_error: BaseException | None = None
    final_error_class: str | None = None


def compute_backoff(
    attempt: int,
    base: float,
    max_seconds: float,
    jitter: bool,
) -> float:
    delay = min(max_seconds, base * (2 ** max(0, attempt - 1)))
    if jitter:
        delay = delay * (0.5 + random.random())
    return max(0.0, delay)


def run_with_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    backoff_base_seconds: float = 0.5,
    backoff_max_seconds: float = 8.0,
    jitter: bool = True,
    sleep_fn: Callable[[float], None] | None = None,
) -> RetryResult:
    sleep_fn = sleep_fn or time.sleep
    result = RetryResult()
    last_exc: BaseException | None = None
    last_class: str | None = None

    for i in range(1, attempts + 1):
        t0 = time.perf_counter()
        try:
            value = fn()
            dur = (time.perf_counter() - t0) * 1000
            result.attempts.append(AttemptRecord(attempt=i, status="success", duration_ms=dur))
            result.value = value
            result.success = True
            return result
        except Exception as exc:  # noqa: BLE001 - classify then decide
            dur = (time.perf_counter() - t0) * 1000
            err_class = classify_exception(exc)
            last_exc = exc
            last_class = err_class
            rec = AttemptRecord(
                attempt=i,
                status="failed",
                error_class=err_class,
                error_message=str(exc),
                duration_ms=dur,
            )
            result.attempts.append(rec)
            if err_class == "permanent" or isinstance(exc, PermanentError):
                break
            if i >= attempts:
                break
            delay = compute_backoff(i, backoff_base_seconds, backoff_max_seconds, jitter)
            rec.slept_ms = delay * 1000
            if delay > 0:
                sleep_fn(delay)
            if not isinstance(exc, TransientError) and err_class != "transient":
                break

    result.final_error = last_exc
    result.final_error_class = last_class
    return result
