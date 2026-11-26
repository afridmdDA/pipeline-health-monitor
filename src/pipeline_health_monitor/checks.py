"""Core check functions for the pipeline health monitor.

Each check is a pure function: it takes a current snapshot and a stored
baseline and returns a ``Finding`` (or ``None`` when the check passes).
Keeping the checks pure makes them easy to unit-test without any database.

A "baseline" is a plain dict produced by an earlier, known-good run:

    {
        "row_count": 1000,
        "columns": ["order_id", "amount", ...],
        "null_rates": {"email": 0.02, ...},
        "numeric_stats": {"amount": {"mean": 52.3, "std": 18.1}},
        "last_updated": "2026-11-26T10:00:00",
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd


@dataclass
class Finding:
    """One failed check, with a plain-English explanation attached."""

    table: str
    check: str  # e.g. "freshness", "row_count", "null_rate", "schema_drift", "value_drift"
    severity: str  # "critical" | "warning"
    summary: str  # one-line headline
    detail: str  # numbers behind the headline
    explanation: str  # plain-English: what broke and why it matters
    column: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "table": self.table,
            "check": self.check,
            "severity": self.severity,
            "summary": self.summary,
            "detail": self.detail,
            "explanation": self.explanation,
            "column": self.column,
        }


def check_freshness(
    table: str,
    last_updated: Optional[datetime],
    expected_interval: timedelta,
    now: Optional[datetime] = None,
) -> Optional[Finding]:
    """Flag a table that has not been updated within its expected schedule.

    ``last_updated`` is when the pipeline last wrote to the table.
    ``expected_interval`` is how often the pipeline is supposed to run
    (e.g. every 24 hours for a daily job).
    """
    now = now or datetime.now()
    if last_updated is None:
        return Finding(
            table=table,
            check="freshness",
            severity="critical",
            summary=f"{table} has never been updated",
            detail="No last-updated timestamp is recorded for this table.",
            explanation=(
                f"The {table} table has no recorded update time, so nothing downstream "
                "can trust it. Any dashboard or report reading this table is showing "
                "data of unknown age. Check whether the pipeline job that writes this "
                "table is still scheduled and running."
            ),
        )
    age = now - last_updated
    if age > expected_interval:
        return Finding(
            table=table,
            check="freshness",
            severity="critical",
            summary=f"{table} is stale: last updated {age.days}d {age.seconds // 3600}h ago",
            detail=(
                f"Last updated {last_updated.isoformat()}, "
                f"{age.total_seconds() / 3600:.1f} hours ago; "
                f"expected at least every {expected_interval.total_seconds() / 3600:.0f} hours."
            ),
            explanation=(
                f"The {table} table is stale. Its data is "
                f"{age.total_seconds() / 3600:.1f} hours old, but this pipeline is "
                f"supposed to refresh it every {expected_interval.total_seconds() / 3600:.0f} hours. "
                "Every report and dashboard built on this table is now showing old "
                "numbers, and anything that depends on fresh data (alerts, billing, "
                "inventory) is making decisions on yesterday's picture. The usual "
                "causes are a failed or stuck pipeline run, a source system outage, "
                "or a schedule that was changed or disabled."
            ),
        )
    return None


def check_row_count(
    table: str,
    current_count: int,
    baseline_count: int,
    threshold: float = 0.20,
) -> Optional[Finding]:
    """Flag a sudden drop or spike in row count vs the baseline.

    ``threshold`` is the fractional change that counts as suspicious
    (default 0.20 = a 20% change in either direction).
    """
    if baseline_count <= 0:
        return None
    change = (current_count - baseline_count) / baseline_count
    if abs(change) <= threshold:
        return None
    direction = "dropped" if change < 0 else "jumped"
    return Finding(
        table=table,
        check="row_count",
        severity="critical" if abs(change) > 0.5 else "warning",
        summary=f"{table} row count {direction} by {abs(change) * 100:.0f}% "
                f"({baseline_count:,} -> {current_count:,})",
        detail=(
            f"Baseline {baseline_count:,} rows; current {current_count:,} rows; "
            f"change {change * 100:+.1f}%; threshold {threshold * 100:.0f}%."
        ),
        explanation=(
            f"The {table} table {direction} from {baseline_count:,} rows to "
            f"{current_count:,} rows, a {abs(change) * 100:.0f}% change. "
            + (
                "A drop like this usually means the source stopped sending records, "
                "a filter or join in the pipeline started dropping rows, or a load "
                "failed halfway. Downstream totals, averages and counts will all be "
                "wrong until this is fixed."
                if change < 0
                else "A jump like this usually means duplicate loads (the same batch "
                     "loaded twice), a source sending backfilled history, or a filter "
                     "that stopped filtering. Downstream totals will be inflated and "
                     "any per-row logic (deduplication, billing) may double-count."
            )
        ),
    )


def check_null_rates(
    table: str,
    current_rates: dict,
    baseline_rates: dict,
    threshold_pp: float = 0.10,
) -> list[Finding]:
    """Flag columns whose null rate spiked vs the baseline.

    ``threshold_pp`` is the rise in null percentage points that counts as
    a spike (default 0.10 = 10 percentage points).
    """
    findings: list[Finding] = []
    for column, current in current_rates.items():
        baseline = baseline_rates.get(column, 0.0)
        rise = current - baseline
        if rise <= threshold_pp:
            continue
        findings.append(
            Finding(
                table=table,
                check="null_rate",
                severity="critical" if rise > 0.30 else "warning",
                column=column,
                summary=f"{table}.{column} null rate spiked "
                        f"from {baseline * 100:.1f}% to {current * 100:.1f}%",
                detail=(
                    f"Baseline null rate {baseline * 100:.1f}%; current "
                    f"{current * 100:.1f}%; rise {rise * 100:.1f} percentage points; "
                    f"threshold {threshold_pp * 100:.0f} points."
                ),
                explanation=(
                    f"The {column} column in {table} went from "
                    f"{baseline * 100:.1f}% missing to {current * 100:.1f}% missing. "
                    "That is not random noise — something upstream stopped sending "
                    "this field, a source changed its format, or a transformation "
                    "started blanking values. Any report, filter or model that uses "
                    f"{column} is now working with mostly empty data, so results "
                    "will be incomplete or misleading."
                ),
            )
        )
    return findings


def check_schema_drift(
    table: str,
    current_columns: list[str],
    baseline_columns: list[str],
) -> list[Finding]:
    """Flag added, removed or renamed columns vs the baseline schema."""
    current = set(current_columns)
    baseline = set(baseline_columns)
    findings: list[Finding] = []

    removed = sorted(baseline - current)
    added = sorted(current - baseline)

    if removed:
        findings.append(
            Finding(
                table=table,
                check="schema_drift",
                severity="critical",
                summary=f"{table} lost column(s): {', '.join(removed)}",
                detail=f"Baseline columns: {sorted(baseline_columns)}. "
                       f"Current columns: {sorted(current_columns)}.",
                explanation=(
                    f"The {table} table lost the column(s) {', '.join(removed)}. "
                    "Any query, dashboard or downstream job that reads those columns "
                    "will now fail outright or silently return nothing. This usually "
                    "happens when a source system renames or drops a field, or when "
                    "a pipeline change drops a column by mistake. Nothing downstream "
                    "can be trusted until the missing columns are restored or the "
                    "downstream code is updated to the new schema."
                ),
            )
        )
    if added:
        findings.append(
            Finding(
                table=table,
                check="schema_drift",
                severity="warning",
                summary=f"{table} gained new column(s): {', '.join(added)}",
                detail=f"Baseline columns: {sorted(baseline_columns)}. "
                       f"Current columns: {sorted(current_columns)}.",
                explanation=(
                    f"The {table} table gained new column(s): {', '.join(added)}. "
                    "New columns are usually harmless, but they can mean the source "
                    "changed its shape, and any strict schema validation or "
                    "column-position logic downstream may break. Worth a quick look "
                    "to confirm the new columns are expected."
                ),
            )
        )
    return findings


def check_value_drift(
    table: str,
    current_stats: dict,
    baseline_stats: dict,
    z_threshold: float = 2.0,
) -> list[Finding]:
    """Flag numeric columns whose distribution shifted vs the baseline.

    Compares the current mean against the baseline mean, measured in units
    of the baseline standard deviation (a z-score). A shift larger than
    ``z_threshold`` standard deviations counts as drift.
    """
    findings: list[Finding] = []
    for column, stats in current_stats.items():
        baseline = baseline_stats.get(column)
        if baseline is None:
            continue
        base_mean = baseline.get("mean")
        base_std = baseline.get("std")
        if base_mean is None or not base_std:
            continue
        current_mean = stats.get("mean")
        if current_mean is None:
            continue
        z = abs(current_mean - base_mean) / base_std
        if z <= z_threshold:
            continue
        findings.append(
            Finding(
                table=table,
                check="value_drift",
                severity="warning",
                column=column,
                summary=f"{table}.{column} mean shifted "
                        f"from {base_mean:.2f} to {current_mean:.2f} "
                        f"({z:.1f} std devs)",
                detail=(
                    f"Baseline mean {base_mean:.2f} (std {base_std:.2f}); current mean "
                    f"{current_mean:.2f}; z-score {z:.2f}; threshold {z_threshold}."
                ),
                explanation=(
                    f"The average value of {column} in {table} moved from "
                    f"{base_mean:.2f} to {current_mean:.2f} — {z:.1f} standard "
                    "deviations from where it normally sits. The data is still "
                    "flowing, but it no longer looks like itself. Common causes are "
                    "a unit change upstream (dollars to cents, minutes to seconds), "
                    "a new population of records entering the feed, or a calculation "
                    "change in the pipeline. Any threshold, forecast or alert built "
                    "on this column will behave differently until the shift is "
                    "understood."
                ),
            )
        )
    return findings


def snapshot_table(df: pd.DataFrame) -> dict:
    """Build a baseline-style snapshot dict from a DataFrame."""
    numeric = df.select_dtypes(include="number")
    return {
        "row_count": int(len(df)),
        "columns": list(df.columns),
        "null_rates": {c: float(df[c].isna().mean()) for c in df.columns},
        "numeric_stats": {
            c: {"mean": float(numeric[c].mean()), "std": float(numeric[c].std() or 0.0)}
            for c in numeric.columns
            if len(df) > 0
        },
    }
