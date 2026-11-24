"""Core tests for MDIF."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml

from mdif.config import load_and_validate, validate_config, load_schema
from mdif.engine.graph import topological_layers
from mdif.engine.retry import run_with_retry
from mdif.engine.runner import run_pipeline
from mdif.engine.state import StateStore
from mdif.engine.validate import validate_frame
from mdif.errors import ConfigError, PermanentError, TransientError
from mdif.sources.mock_api import MockAPIServer

ROOT = Path(__file__).resolve().parents[1]


def test_config_validation_errors():
    bad = {"pipeline": {"name": "x"}, "sources": [{"name": "a", "type": "csv", "target": "t"}]}
    errs = validate_config(bad, load_schema())
    assert errs
    with pytest.raises(ConfigError):
        load_and_validate(ROOT / "config" / "does_not_exist.yaml")


def test_dependency_ordering():
    sources = [
        {"name": "b", "depends_on": ["a"], "target": "tb", "type": "csv", "path": "x"},
        {"name": "a", "depends_on": [], "target": "ta", "type": "csv", "path": "x"},
        {"name": "c", "depends_on": ["b"], "target": "tc", "type": "csv", "path": "x"},
    ]
    layers = topological_layers(sources)
    assert layers[0] == ["a"]
    assert layers[1] == ["b"]
    assert layers[2] == ["c"]


def test_retry_then_success():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientError("temporary")
        return "ok"

    result = run_with_retry(flaky, attempts=4, backoff_base_seconds=0.01, jitter=False, sleep_fn=lambda s: None)
    assert result.success
    assert result.value == "ok"
    assert len(result.attempts) == 3


def test_no_retry_on_permanent():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise PermanentError("bad config")

    result = run_with_retry(boom, attempts=5, backoff_base_seconds=0.01, jitter=False, sleep_fn=lambda s: None)
    assert not result.success
    assert calls["n"] == 1
    assert result.final_error_class == "permanent"


def test_quarantine_rows():
    df = pd.DataFrame(
        [
            {"id": 1, "amount": 10},
            {"id": None, "amount": 5},
            {"id": 3, "amount": -1},
        ]
    )
    good, bad = validate_frame(
        df,
        {
            "schema": {"id": "integer", "amount": "number"},
            "not_null": ["id"],
            "ranges": {"amount": {"min": 0, "max": 100}},
        },
    )
    assert len(good) == 1
    assert len(bad) == 2
    assert "quarantine_reason" in bad.columns


def test_idempotent_rerun(tmp_path: Path):
    cfg = {
        "pipeline": {
            "name": "idem",
            "concurrency": 2,
            "state_dir": str(tmp_path / "state"),
            "raw_dir": str(tmp_path / "raw"),
            "quarantine_dir": str(tmp_path / "q"),
            "db_path": str(tmp_path / "state" / "w.duckdb"),
            "parameters": {"run_date": "2024-01-01"},
        },
        "sources": [
            {
                "name": "cust",
                "type": "csv",
                "path": str(ROOT / "fixtures" / "csv" / "customers.csv"),
                "target": "dim_customers",
                "load_mode": "full",
                "on_failure": "quarantine",
                "retry": {"attempts": 1, "backoff_base_seconds": 0.01, "jitter": False},
                "validation": {
                    "schema": {
                        "customer_id": "integer",
                        "name": "string",
                        "email": "string",
                        "country": "string",
                        "signup_date": "string",
                    },
                    "not_null": ["customer_id", "email"],
                    "unique": ["customer_id"],
                },
            }
        ],
    }
    cfg_path = tmp_path / "p.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    r1 = run_pipeline(cfg_path, root=tmp_path, start_mock_api=False)
    r2 = run_pipeline(cfg_path, root=tmp_path, start_mock_api=False)
    assert r1.sources["cust"].rows_loaded == r2.sources["cust"].rows_loaded
    store = StateStore(tmp_path / "state" / "w.duckdb")
    assert store.table_count("dim_customers") == r2.sources["cust"].rows_loaded
    store.close()


def test_skipped_downstream_on_failure(tmp_path: Path):
    cfg = {
        "pipeline": {
            "name": "failpath",
            "concurrency": 1,
            "state_dir": str(tmp_path / "state"),
            "raw_dir": str(tmp_path / "raw"),
            "quarantine_dir": str(tmp_path / "q"),
            "db_path": str(tmp_path / "state" / "w.duckdb"),
        },
        "sources": [
            {
                "name": "broken_feed",
                "type": "csv",
                "path": str(ROOT / "fixtures" / "csv" / "broken_feed.csv"),
                "target": "stg_broken",
                "expected_fail": True,
                "on_failure": "fail",
                "retry": {"attempts": 1, "jitter": False, "backoff_base_seconds": 0.01},
                "validation": {
                    "schema": {"id": "integer", "value": "number"},
                    "not_null": ["id", "value"],
                },
            },
            {
                "name": "downstream_of_broken",
                "type": "json",
                "path": str(ROOT / "fixtures" / "json" / "downstream.json"),
                "target": "stg_down",
                "depends_on": ["broken_feed"],
                "on_failure": "skip",
                "retry": {"attempts": 1},
                "validation": {"schema": {"id": "integer", "note": "string"}, "not_null": ["id"]},
            },
        ],
    }
    cfg_path = tmp_path / "p.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    result = run_pipeline(cfg_path, root=tmp_path, start_mock_api=False)
    assert result.sources["broken_feed"].status == "failed"
    assert result.sources["downstream_of_broken"].status == "skipped"


def test_mock_api_retries(tmp_path: Path):
    with MockAPIServer(port=8766, fail_first=1) as api:
        from mdif.sources.base import extract_http

        def call():
            return extract_http(f"http://127.0.0.1:{api.port}/products")

        res = run_with_retry(
            call, attempts=5, backoff_base_seconds=0.05, jitter=False, sleep_fn=lambda s: None
        )
        assert res.success
        assert len(res.value) == 4
