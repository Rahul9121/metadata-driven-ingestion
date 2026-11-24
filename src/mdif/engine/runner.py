"""Pipeline runner: validate, order, concurrent execute, state, quarantine."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from mdif.config import apply_defaults, load_and_validate
from mdif.engine.graph import lineage_edges, sources_by_name, topological_layers
from mdif.engine.retry import run_with_retry
from mdif.engine.state import StateStore, utc_now
from mdif.engine.validate import validate_frame
from mdif.sources.base import extract_source
from mdif.sources.mock_api import MockAPIServer


@dataclass
class SourceResult:
    name: str
    status: str
    rows_read: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0
    attempts: list[dict[str, Any]] = field(default_factory=list)
    error_class: str | None = None
    error_message: str | None = None
    duration_ms: float = 0.0
    skipped_reason: str | None = None
    target: str | None = None
    yaml_snippet: dict[str, Any] | None = None


@dataclass
class RunResult:
    run_id: str
    status: str
    sources: dict[str, SourceResult]
    lineage: list[dict[str, str]]
    quarantine: list[dict[str, Any]]
    config: dict[str, Any]
    duration_ms: float = 0.0


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _write_quarantine(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _condition_ok(src: dict[str, Any], results: dict[str, SourceResult]) -> tuple[bool, str | None]:
    cond = src.get("condition")
    if not cond or cond.get("type") in (None, "always"):
        return True, None
    if cond.get("type") == "rows_gt":
        upstream = cond.get("source")
        need = float(cond.get("value") or 0)
        if upstream not in results:
            return False, f"condition upstream '{upstream}' missing"
        got = results[upstream].rows_loaded
        if got > need:
            return True, None
        return False, f"condition rows_gt failed: {upstream} loaded {got} <= {need}"
    return True, None


def _deps_ok(src: dict[str, Any], results: dict[str, SourceResult]) -> tuple[bool, str | None]:
    for dep in src.get("depends_on") or []:
        if dep not in results:
            return False, f"dependency '{dep}' not run"
        st = results[dep].status
        if st not in ("success", "success_with_rejections"):
            return False, f"dependency '{dep}' status={st}"
    return True, None


def _run_one_source(
    src: dict[str, Any],
    *,
    store: StateStore,
    run_id: str,
    raw_dir: Path,
    quarantine_dir: Path,
    root: Path,
) -> tuple[SourceResult, list[dict[str, Any]]]:
    t0 = time.perf_counter()
    name = src["name"]
    result = SourceResult(name=name, status="running", target=src.get("target"), yaml_snippet=src)
    quarantine_rows: list[dict[str, Any]] = []
    retry_cfg = src.get("retry") or {}

    def _extract_all() -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        foreach = src.get("foreach")
        if foreach and src["type"] in ("csv", "json"):
            param = foreach.get("param") or "item"
            for item in foreach.get("items") or []:
                path = (src.get("path") or "").replace("{" + param + "}", str(item))
                frames.append(extract_source(src, path_override=str(root / path)))
            if not frames:
                return pd.DataFrame()
            return pd.concat(frames, ignore_index=True)
        path = src.get("path")
        override = str(root / path) if path and src["type"] in ("csv", "json") else None
        return extract_source(src, path_override=override)

    retry_result = run_with_retry(
        _extract_all,
        attempts=int(retry_cfg.get("attempts") or 3),
        backoff_base_seconds=float(retry_cfg.get("backoff_base_seconds") or 0.5),
        backoff_max_seconds=float(retry_cfg.get("backoff_max_seconds") or 8),
        jitter=bool(retry_cfg.get("jitter", True)),
    )
    result.attempts = [
        {
            "attempt": a.attempt,
            "status": a.status,
            "error_class": a.error_class,
            "error_message": a.error_message,
            "duration_ms": round(a.duration_ms, 2),
            "slept_ms": round(a.slept_ms, 2),
        }
        for a in retry_result.attempts
    ]
    for a in result.attempts:
        store.log_event(
            run_id,
            {
                "source_name": name,
                "attempt": a["attempt"],
                "status": a["status"],
                "duration_ms": a["duration_ms"],
                "error_class": a["error_class"],
                "error_message": a["error_message"],
                "detail": {"slept_ms": a["slept_ms"]},
            },
        )

    if not retry_result.success:
        result.status = "failed"
        result.error_class = retry_result.final_error_class or "permanent"
        result.error_message = str(retry_result.final_error) if retry_result.final_error else "failed"
        result.duration_ms = (time.perf_counter() - t0) * 1000
        store.log_event(
            run_id,
            {
                "source_name": name,
                "attempt": len(result.attempts),
                "status": "failed",
                "error_class": result.error_class,
                "error_message": result.error_message,
                "duration_ms": result.duration_ms,
            },
        )
        return result, quarantine_rows

    df: pd.DataFrame = retry_result.value
    result.rows_read = len(df)

    # land raw
    raw_path = raw_dir / f"{name}.parquet"
    try:
        _write_parquet(df, raw_path)
    except Exception:  # noqa: BLE001
        raw_path = raw_dir / f"{name}.csv"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(raw_path, index=False)

    # incremental watermark filter
    mode = src.get("load_mode") or "full"
    wm_col = src.get("watermark_column")
    if mode == "incremental" and wm_col and wm_col in df.columns:
        prior = store.get_watermark(name)
        if prior is not None:
            df = df[df[wm_col].astype(str) > str(prior)].copy()

    good, bad = validate_frame(df, src.get("validation"))
    result.rows_rejected = len(bad)
    result.rows_loaded = len(good)

    if not bad.empty:
        qpath = quarantine_dir / f"{name}_rejected.csv"
        _write_quarantine(bad, qpath)
        for rec in bad.to_dict(orient="records"):
            row = {k: (None if pd.isna(v) else v) for k, v in rec.items()}
            row["_source"] = name
            quarantine_rows.append(row)

    if good.empty and result.rows_read > 0 and result.rows_rejected == result.rows_read:
        # all bad — permanent validation failure if on_failure=fail
        if src.get("on_failure") == "fail":
            result.status = "failed"
            result.error_class = "validation"
            result.error_message = f"All {result.rows_read} rows failed validation"
            result.duration_ms = (time.perf_counter() - t0) * 1000
            store.log_event(
                run_id,
                {
                    "source_name": name,
                    "attempt": len(result.attempts),
                    "status": "failed",
                    "rows_read": result.rows_read,
                    "rows_loaded": 0,
                    "rows_rejected": result.rows_rejected,
                    "error_class": result.error_class,
                    "error_message": result.error_message,
                    "duration_ms": result.duration_ms,
                },
            )
            return result, quarantine_rows

    target = src["target"]
    store.load_table(target, good, mode="full" if mode == "full" else "incremental")

    if mode == "incremental" and wm_col and not good.empty and wm_col in good.columns:
        new_wm = str(good[wm_col].astype(str).max())
        store.set_watermark(name, new_wm)

    result.status = "success_with_rejections" if result.rows_rejected else "success"
    result.duration_ms = (time.perf_counter() - t0) * 1000
    store.log_event(
        run_id,
        {
            "source_name": name,
            "attempt": len(result.attempts),
            "status": result.status,
            "rows_read": result.rows_read,
            "rows_loaded": result.rows_loaded,
            "rows_rejected": result.rows_rejected,
            "duration_ms": result.duration_ms,
        },
    )
    return result, quarantine_rows


def run_pipeline(
    config_path: str | Path,
    *,
    root: str | Path | None = None,
    start_mock_api: bool = True,
    dry_run: bool = False,
) -> RunResult:
    root_path = Path(root) if root else Path.cwd()
    config = apply_defaults(load_and_validate(root_path / config_path if not Path(config_path).is_absolute() else config_path))
    pipe = config["pipeline"]
    sources = config["sources"]
    lineage = lineage_edges(sources)

    if dry_run:
        layers = topological_layers(sources)
        fake_sources = {
            s["name"]: SourceResult(
                name=s["name"],
                status="planned",
                target=s.get("target"),
                yaml_snippet=s,
            )
            for s in sources
        }
        return RunResult(
            run_id="dry-run",
            status="dry_run",
            sources=fake_sources,
            lineage=lineage,
            quarantine=[],
            config=config,
            duration_ms=0,
        )

    state_dir = root_path / pipe["state_dir"]
    raw_dir = root_path / pipe["raw_dir"]
    quarantine_dir = root_path / pipe["quarantine_dir"]
    state_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    db_path = root_path / pipe["db_path"]
    store = StateStore(db_path)
    run_id = store.start_run(pipe["name"], pipe.get("parameters") or {})
    t0 = time.perf_counter()
    results: dict[str, SourceResult] = {}
    all_quarantine: list[dict[str, Any]] = []
    by_name = sources_by_name(sources)
    layers = topological_layers(sources)
    concurrency = int(pipe.get("concurrency") or 4)

    mock: MockAPIServer | None = None
    needs_http = any(s.get("type") == "http" for s in sources)
    try:
        if start_mock_api and needs_http:
            mock = MockAPIServer(port=8765, fail_first=2)
            mock.start()
            time.sleep(0.15)

        for layer in layers:
            runnable: list[dict[str, Any]] = []
            for name in layer:
                src = by_name[name]
                ok_dep, dep_reason = _deps_ok(src, results)
                if not ok_dep:
                    skipped = SourceResult(
                        name=name,
                        status="skipped",
                        skipped_reason=dep_reason,
                        target=src.get("target"),
                        yaml_snippet=src,
                        error_class="dependency",
                        error_message=dep_reason,
                    )
                    results[name] = skipped
                    store.log_event(
                        run_id,
                        {
                            "source_name": name,
                            "attempt": 0,
                            "status": "skipped",
                            "error_class": "dependency",
                            "error_message": dep_reason,
                        },
                    )
                    continue
                ok_cond, cond_reason = _condition_ok(src, results)
                if not ok_cond:
                    skipped = SourceResult(
                        name=name,
                        status="skipped",
                        skipped_reason=cond_reason,
                        target=src.get("target"),
                        yaml_snippet=src,
                        error_class="condition",
                        error_message=cond_reason,
                    )
                    results[name] = skipped
                    store.log_event(
                        run_id,
                        {
                            "source_name": name,
                            "attempt": 0,
                            "status": "skipped",
                            "error_class": "condition",
                            "error_message": cond_reason,
                        },
                    )
                    continue
                runnable.append(src)

            if not runnable:
                continue

            with ThreadPoolExecutor(max_workers=min(concurrency, len(runnable))) as pool:
                futs = {
                    pool.submit(
                        _run_one_source,
                        src,
                        store=store,
                        run_id=run_id,
                        raw_dir=raw_dir,
                        quarantine_dir=quarantine_dir,
                        root=root_path,
                    ): src["name"]
                    for src in runnable
                }
                for fut in as_completed(futs):
                    src_result, qrows = fut.result()
                    results[src_result.name] = src_result
                    all_quarantine.extend(qrows)

        # overall status: success if only expected failures failed
        unexpected_fail = False
        for s in sources:
            r = results[s["name"]]
            if r.status == "failed" and not s.get("expected_fail"):
                unexpected_fail = True
            if r.status == "failed" and s.get("expected_fail"):
                pass
        status = "failed" if unexpected_fail else "success"
        # still mark partial if any failed
        if any(results[s["name"]].status == "failed" for s in sources) and not unexpected_fail:
            status = "success_with_expected_failures"

        store.finish_run(run_id, status)
        duration = (time.perf_counter() - t0) * 1000
        run_result = RunResult(
            run_id=run_id,
            status=status,
            sources=results,
            lineage=lineage,
            quarantine=all_quarantine,
            config=config,
            duration_ms=duration,
        )
        _persist_run_artifact(state_dir, run_result)
        return run_result
    finally:
        if mock:
            mock.stop()
        store.close()


def _persist_run_artifact(state_dir: Path, result: RunResult) -> None:
    payload = serialize_run_result(result)
    path = state_dir / "last_run.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def serialize_run_result(result: RunResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "status": result.status,
        "duration_ms": round(result.duration_ms, 2),
        "generated_at": utc_now(),
        "pipeline": result.config.get("pipeline"),
        "lineage": result.lineage,
        "sources": [
            {
                "name": s.name,
                "status": s.status,
                "target": s.target,
                "rows_read": s.rows_read,
                "rows_loaded": s.rows_loaded,
                "rows_rejected": s.rows_rejected,
                "attempts": s.attempts,
                "error_class": s.error_class,
                "error_message": s.error_message,
                "duration_ms": round(s.duration_ms, 2),
                "skipped_reason": s.skipped_reason,
                "yaml_snippet": s.yaml_snippet,
            }
            for s in result.sources.values()
        ],
        "quarantine": result.quarantine,
        "adf_mapping": ADF_MAPPING,
    }


ADF_MAPPING = [
    {
        "adf": "Pipeline",
        "here": "YAML pipeline block + runner",
        "status": "implemented (simplified)",
    },
    {
        "adf": "Copy activity",
        "here": "Source extract + validate + load to DuckDB/Parquet",
        "status": "implemented (simplified)",
    },
    {
        "adf": "ForEach",
        "here": "foreach items over file partitions",
        "status": "implemented (list only)",
    },
    {
        "adf": "If condition",
        "here": "condition.rows_gt on upstream load counts",
        "status": "implemented (minimal)",
    },
    {
        "adf": "Linked service",
        "here": "source type + path/url; secrets via ${ENV}",
        "status": "pattern only",
    },
    {
        "adf": "Dataset",
        "here": "source path/url + target table name",
        "status": "implemented (simplified)",
    },
    {
        "adf": "Parameters",
        "here": "pipeline.parameters (e.g. run_date)",
        "status": "implemented",
    },
    {
        "adf": "Retry policy",
        "here": "attempts, exponential backoff, jitter, timeout",
        "status": "implemented",
    },
    {
        "adf": "Triggers",
        "here": "CLI / CI schedule only",
        "status": "not implemented",
    },
    {
        "adf": "Integration runtime",
        "here": "in-process Python thread pool",
        "status": "not implemented (no IR)",
    },
    {
        "adf": "Monitoring",
        "here": "structured run log + static HTML report",
        "status": "implemented (offline report)",
    },
    {
        "adf": "Azure resources",
        "here": "none — local DuckDB, files, mock HTTP",
        "status": "not implemented",
    },
]
