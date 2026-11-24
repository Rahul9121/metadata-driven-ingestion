"""Run state, watermarks, and structured run log backed by DuckDB."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = duckdb.connect(str(self.db_path))
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watermarks (
                source_name VARCHAR PRIMARY KEY,
                watermark_value VARCHAR,
                updated_at VARCHAR
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id VARCHAR PRIMARY KEY,
                pipeline_name VARCHAR,
                started_at VARCHAR,
                finished_at VARCHAR,
                status VARCHAR,
                parameters VARCHAR
            );
            CREATE TABLE IF NOT EXISTS run_events (
                run_id VARCHAR,
                source_name VARCHAR,
                attempt INTEGER,
                status VARCHAR,
                rows_read INTEGER,
                rows_loaded INTEGER,
                rows_rejected INTEGER,
                duration_ms DOUBLE,
                error_class VARCHAR,
                error_message VARCHAR,
                detail VARCHAR,
                ts VARCHAR
            );
            """
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def start_run(self, pipeline_name: str, parameters: dict[str, Any]) -> str:
        run_id = str(uuid.uuid4())
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs VALUES (?, ?, ?, NULL, ?, ?)",
                [run_id, pipeline_name, utc_now(), "running", json.dumps(parameters)],
            )
        return run_id

    def finish_run(self, run_id: str, status: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE runs SET finished_at = ?, status = ? WHERE run_id = ?",
                [utc_now(), status, run_id],
            )

    def log_event(self, run_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO run_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    run_id,
                    event.get("source_name"),
                    event.get("attempt", 0),
                    event.get("status"),
                    event.get("rows_read", 0),
                    event.get("rows_loaded", 0),
                    event.get("rows_rejected", 0),
                    event.get("duration_ms", 0.0),
                    event.get("error_class"),
                    event.get("error_message"),
                    json.dumps(event.get("detail") or {}),
                    event.get("ts") or utc_now(),
                ],
            )

    def get_watermark(self, source_name: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT watermark_value FROM watermarks WHERE source_name = ?",
                [source_name],
            ).fetchone()
        return row[0] if row else None

    def set_watermark(self, source_name: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO watermarks VALUES (?, ?, ?)
                ON CONFLICT (source_name) DO UPDATE SET
                  watermark_value = excluded.watermark_value,
                  updated_at = excluded.updated_at
                """,
                [source_name, value, utc_now()],
            )

    def load_table(self, table: str, df: pd.DataFrame, mode: str = "full") -> None:
        if df is None or df.empty:
            if mode == "full":
                with self._lock:
                    cols = list(df.columns) if df is not None else ["_empty"]
                    col_sql = ", ".join(f'CAST(NULL AS VARCHAR) AS "{c}"' for c in cols)
                    self._conn.execute(
                        f'CREATE OR REPLACE TABLE "{table}" AS SELECT {col_sql} WHERE 1=0'
                    )
            return
        with self._lock:
            self._conn.register("_df_load", df)
            if mode == "full":
                self._conn.execute(f'CREATE OR REPLACE TABLE "{table}" AS SELECT * FROM _df_load')
            else:
                exists = self._conn.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                    [table],
                ).fetchone()[0]
                if not exists:
                    self._conn.execute(f'CREATE TABLE "{table}" AS SELECT * FROM _df_load')
                else:
                    self._conn.execute(f'INSERT INTO "{table}" SELECT * FROM _df_load')
            self._conn.unregister("_df_load")

    def table_count(self, table: str) -> int:
        with self._lock:
            exists = self._conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
                [table],
            ).fetchone()[0]
            if not exists:
                return 0
            return int(self._conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

    def export_run(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            run = self._conn.execute(
                "SELECT run_id, pipeline_name, started_at, finished_at, status, parameters FROM runs WHERE run_id = ?",
                [run_id],
            ).fetchone()
            events = self._conn.execute(
                """
                SELECT source_name, attempt, status, rows_read, rows_loaded, rows_rejected,
                       duration_ms, error_class, error_message, detail, ts
                FROM run_events WHERE run_id = ? ORDER BY ts, attempt
                """,
                [run_id],
            ).fetchall()
            marks = self._conn.execute(
                "SELECT source_name, watermark_value, updated_at FROM watermarks"
            ).fetchall()
        return {
            "run": {
                "run_id": run[0],
                "pipeline_name": run[1],
                "started_at": run[2],
                "finished_at": run[3],
                "status": run[4],
                "parameters": json.loads(run[5] or "{}"),
            }
            if run
            else None,
            "events": [
                {
                    "source_name": e[0],
                    "attempt": e[1],
                    "status": e[2],
                    "rows_read": e[3],
                    "rows_loaded": e[4],
                    "rows_rejected": e[5],
                    "duration_ms": e[6],
                    "error_class": e[7],
                    "error_message": e[8],
                    "detail": json.loads(e[9] or "{}"),
                    "ts": e[10],
                }
                for e in events
            ],
            "watermarks": [
                {"source_name": m[0], "watermark_value": m[1], "updated_at": m[2]} for m in marks
            ],
        }

    def latest_run_id(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else None