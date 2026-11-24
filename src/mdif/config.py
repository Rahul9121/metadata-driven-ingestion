"""Load and validate pipeline YAML against JSON Schema."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from mdif.errors import ConfigError

DEFAULT_SCHEMA = Path(__file__).resolve().parents[2] / "config" / "schema.json"


def _resolve_env_placeholders(obj: Any) -> Any:
    if isinstance(obj, str):
        out = obj
        # ${VAR} pattern
        while "${" in out:
            start = out.index("${")
            end = out.index("}", start)
            key = out[start + 2 : end]
            val = os.environ.get(key, "")
            out = out[:start] + val + out[end + 1 :]
        return out
    if isinstance(obj, list):
        return [_resolve_env_placeholders(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _resolve_env_placeholders(v) for k, v in obj.items()}
    return obj


def load_yaml(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}")
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ConfigError("Pipeline config root must be a mapping")
    return data


def load_schema(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_SCHEMA
    with p.open(encoding="utf-8") as f:
        return json.load(f)


def validate_config(config: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    schema = schema or load_schema()
    validator = Draft202012Validator(schema)
    errors: list[str] = []
    for err in sorted(validator.iter_errors(config), key=lambda e: list(e.absolute_path)):
        path = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{path}: {err.message}")
    names = [s.get("name") for s in config.get("sources", []) if isinstance(s, dict)]
    if len(names) != len(set(names)):
        errors.append("sources: source names must be unique")
    known = set(names)
    for src in config.get("sources", []):
        if not isinstance(src, dict):
            continue
        for dep in src.get("depends_on") or []:
            if dep not in known:
                errors.append(f"{src.get('name')}: depends_on unknown source '{dep}'")
        cond = src.get("condition") or {}
        if cond.get("type") == "rows_gt" and cond.get("source") and cond["source"] not in known:
            errors.append(f"{src.get('name')}: condition.source unknown '{cond['source']}'")
    return errors


def load_and_validate(
    path: str | Path, schema_path: str | Path | None = None, resolve_env: bool = True
) -> dict[str, Any]:
    config = load_yaml(path)
    schema = load_schema(schema_path)
    errors = validate_config(config, schema)
    if errors:
        joined = "\n  - ".join(errors)
        raise ConfigError(f"Config validation failed:\n  - {joined}")
    if resolve_env:
        config = _resolve_env_placeholders(config)
    return config


def apply_defaults(config: dict[str, Any]) -> dict[str, Any]:
    pipe = config.setdefault("pipeline", {})
    pipe.setdefault("concurrency", 4)
    pipe.setdefault("state_dir", "data/state")
    pipe.setdefault("raw_dir", "data/raw")
    pipe.setdefault("quarantine_dir", "data/quarantine")
    pipe.setdefault("db_path", "data/state/warehouse.duckdb")
    pipe.setdefault("parameters", {})
    for src in config.get("sources", []):
        src.setdefault("depends_on", [])
        src.setdefault("on_failure", "fail")
        src.setdefault("load_mode", "full")
        src.setdefault("expected_fail", False)
        retry = src.setdefault("retry", {})
        retry.setdefault("attempts", 3)
        retry.setdefault("backoff_base_seconds", 0.5)
        retry.setdefault("backoff_max_seconds", 8)
        retry.setdefault("jitter", True)
        retry.setdefault("timeout_seconds", 30)
        src.setdefault("validation", {})
    return config
