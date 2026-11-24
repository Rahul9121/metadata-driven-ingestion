"""Row-level validation: schema, not-null, ranges, uniqueness."""

from __future__ import annotations

from typing import Any

import pandas as pd


TYPE_MAP = {
    "string": "string",
    "integer": "Int64",
    "number": "float64",
    "boolean": "boolean",
    "datetime": "string",
}


def _is_null(v: Any) -> bool:
    if v is None:
        return True
    try:
        return bool(pd.isna(v))
    except Exception:  # noqa: BLE001
        return False


def validate_frame(
    df: pd.DataFrame, rules: dict[str, Any] | None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (good_rows, bad_rows_with_reason)."""
    if df is None or df.empty:
        empty_bad = df.copy() if df is not None else pd.DataFrame()
        if "quarantine_reason" not in empty_bad.columns:
            empty_bad["quarantine_reason"] = pd.Series(dtype="string")
        return df if df is not None else pd.DataFrame(), empty_bad

    rules = rules or {}
    working = df.copy()
    reasons: list[str] = [""] * len(working)

    schema = rules.get("schema") or {}
    for col, typ in schema.items():
        if col not in working.columns:
            for i in range(len(working)):
                reasons[i] = _join_reason(reasons[i], f"missing column {col}")
            continue
        if typ == "integer":
            coerced = pd.to_numeric(working[col], errors="coerce")
            bad = coerced.isna() & ~working[col].isna()
            for i in working.index[bad]:
                reasons[working.index.get_loc(i)] = _join_reason(
                    reasons[working.index.get_loc(i)], f"{col} not integer"
                )
            working[col] = coerced.astype("Int64")
        elif typ == "number":
            coerced = pd.to_numeric(working[col], errors="coerce")
            bad = coerced.isna() & working[col].notna()
            for idx_pos, idx in enumerate(working.index):
                if bad.loc[idx]:
                    reasons[idx_pos] = _join_reason(reasons[idx_pos], f"{col} not number")
            working[col] = coerced
        elif typ == "boolean":
            pass
        else:
            working[col] = working[col].astype("string")

    for col in rules.get("not_null") or []:
        if col not in working.columns:
            for i in range(len(working)):
                reasons[i] = _join_reason(reasons[i], f"not_null missing col {col}")
            continue
        mask = working[col].isna() | (working[col].astype(str).str.len() == 0)
        for idx_pos, idx in enumerate(working.index):
            if bool(mask.loc[idx]):
                reasons[idx_pos] = _join_reason(reasons[idx_pos], f"{col} is null")

    ranges = rules.get("ranges") or {}
    for col, bounds in ranges.items():
        if col not in working.columns:
            continue
        series = pd.to_numeric(working[col], errors="coerce")
        lo = bounds.get("min")
        hi = bounds.get("max")
        for idx_pos, idx in enumerate(working.index):
            val = series.loc[idx]
            if _is_null(val):
                continue
            if lo is not None and val < lo:
                reasons[idx_pos] = _join_reason(reasons[idx_pos], f"{col} < {lo}")
            if hi is not None and val > hi:
                reasons[idx_pos] = _join_reason(reasons[idx_pos], f"{col} > {hi}")

    for col in rules.get("unique") or []:
        if col not in working.columns:
            continue
        dup = working[col].duplicated(keep="first") & working[col].notna()
        for idx_pos, idx in enumerate(working.index):
            if bool(dup.loc[idx]):
                reasons[idx_pos] = _join_reason(reasons[idx_pos], f"{col} duplicate")

    reason_series = pd.Series(reasons, index=working.index)
    bad_mask = reason_series.str.len() > 0
    good = working.loc[~bad_mask].copy()
    bad = working.loc[bad_mask].copy()
    bad["quarantine_reason"] = reason_series.loc[bad_mask].values
    return good, bad


def _join_reason(existing: str, extra: str) -> str:
    if not existing:
        return extra
    return f"{existing}; {extra}"
