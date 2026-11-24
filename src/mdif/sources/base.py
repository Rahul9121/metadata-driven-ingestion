"""Source extractors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from mdif.errors import PermanentError, TransientError


def extract_csv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise PermanentError(f"CSV not found: {p}")
    try:
        return pd.read_csv(p)
    except Exception as exc:  # noqa: BLE001
        raise PermanentError(f"Failed reading CSV {p}: {exc}") from exc


def extract_json(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise PermanentError(f"JSON not found: {p}")
    try:
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("data") or data.get("items") or [data]
        return pd.DataFrame(data)
    except PermanentError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PermanentError(f"Failed reading JSON {p}: {exc}") from exc


def extract_http(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> pd.DataFrame:
    try:
        resp = requests.request(method.upper(), url, headers=headers or {}, timeout=timeout)
    except requests.Timeout as exc:
        raise TransientError(f"HTTP timeout calling {url}") from exc
    except requests.ConnectionError as exc:
        raise TransientError(f"HTTP connection error calling {url}") from exc
    except Exception as exc:  # noqa: BLE001
        raise TransientError(f"HTTP error calling {url}: {exc}") from exc

    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientError(f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")
    if resp.status_code >= 400:
        raise PermanentError(f"HTTP {resp.status_code} from {url}: {resp.text[:200]}")

    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        raise PermanentError(f"Invalid JSON from {url}") from exc

    if isinstance(payload, dict) and payload.get("malformed") is True:
        # Treated as transient so the demo can retry into a clean page.
        raise TransientError("Malformed API payload flagged by mock server")

    if isinstance(payload, dict):
        rows = payload.get("data") or payload.get("items") or payload.get("products")
        if rows is None:
            rows = [payload]
    else:
        rows = payload
    return pd.DataFrame(rows)


def extract_source(src: dict[str, Any], path_override: str | None = None) -> pd.DataFrame:
    stype = src["type"]
    if stype == "csv":
        return extract_csv(path_override or src["path"])
    if stype == "json":
        return extract_json(path_override or src["path"])
    if stype == "http":
        return extract_http(
            src["url"],
            method=src.get("method") or "GET",
            headers=src.get("headers"),
            timeout=float((src.get("retry") or {}).get("timeout_seconds") or 10),
        )
    raise PermanentError(f"Unsupported source type: {stype}")
