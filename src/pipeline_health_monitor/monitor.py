"""Pipeline Health Monitor — run checks, report results."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

from . import email_alerts
from .checks import (
    Finding,
    check_freshness,
    check_null_rates,
    check_row_count,
    check_schema_drift,
    check_value_drift,
    snapshot_table,
)
from .email_alerts import email_settings_from_config
from .explain import enhance_explanation


class Monitor:
    """Loads tables from SQLite/CSV, compares them to a stored baseline,
    and writes a console summary, a markdown report and an alerts log."""

    def __init__(self, config_path: str | Path, baseline_path: str | Path):
        self.config_path = Path(config_path)
        self.baseline_path = Path(baseline_path)
        self.config = json.loads(self.config_path.read_text())
        self.baseline = json.loads(self.baseline_path.read_text())
        self.findings: list[Finding] = []
        self.checked: list[str] = []
        # None when the config has no (enabled) "email" block — the
        # monitor then runs exactly as before and never sends email.
        self.email_settings = email_settings_from_config(self.config)

    # -- data loading -------------------------------------------------
    def load_table(self, table_cfg: dict) -> pd.DataFrame:
        source = table_cfg["source"]
        if source["type"] == "sqlite":
            conn = sqlite3.connect(source["path"])
            try:
                return pd.read_sql_query(
                    f"SELECT * FROM {table_cfg['name']}", conn
                )
            finally:
                conn.close()
        if source["type"] == "csv":
            return pd.read_csv(source["path"])
        raise ValueError(f"Unknown source type: {source['type']}")

    def last_updated(self, table_cfg: dict) -> Optional[datetime]:
        """Last-updated time for a table.

        Read from the metadata file the pipeline writes after each load
        (``metadata_path`` in the config). Returns None when unknown.
        """
        metadata_path = self.config.get("metadata_path")
        if not metadata_path:
            return None
        metadata_file = Path(metadata_path)
        if not metadata_file.exists():
            return None
        metadata = json.loads(metadata_file.read_text())
        stamp = metadata.get(table_cfg["name"])
        if not stamp:
            return None
        return datetime.fromisoformat(stamp)

    # -- running checks ------------------------------------------------
    def run(self, enhance: bool = False) -> list[Finding]:
        """Run every configured check and return the list of findings."""
        self.findings = []
        self.checked = []
        now = datetime.now()
        for table_cfg in self.config["tables"]:
            name = table_cfg["name"]
            self.checked.append(name)
            baseline = self.baseline.get("tables", {}).get(name, {})
            df = self.load_table(table_cfg)
            snapshot = snapshot_table(df)

            # 1. Freshness
            interval_hours = table_cfg.get("expected_interval_hours", 24)
            finding = check_freshness(
                name,
                self.last_updated(table_cfg),
                timedelta(hours=interval_hours),
                now=now,
            )
            if finding:
                self.findings.append(finding)

            # 2. Row-count change
            finding = check_row_count(
                name,
                snapshot["row_count"],
                baseline.get("row_count", snapshot["row_count"]),
                threshold=table_cfg.get("row_count_threshold", 0.20),
            )
            if finding:
                self.findings.append(finding)

            # 3. Null-rate spikes
            self.findings.extend(
                check_null_rates(
                    name,
                    snapshot["null_rates"],
                    baseline.get("null_rates", {}),
                    threshold_pp=table_cfg.get("null_rate_threshold_pp", 0.10),
                )
            )

            # 4. Schema drift
            self.findings.extend(
                check_schema_drift(
                    name,
                    snapshot["columns"],
                    baseline.get("columns", snapshot["columns"]),
                )
            )

            # 5. Value drift
            self.findings.extend(
                check_value_drift(
                    name,
                    snapshot["numeric_stats"],
                    baseline.get("numeric_stats", {}),
                    z_threshold=table_cfg.get("value_drift_z", 2.0),
                )
            )

        if enhance:
            for finding in self.findings:
                finding.explanation = enhance_explanation(finding)
        return self.findings

    # -- outputs --------------------------------------------------------
    def console_summary(self) -> str:
        lines = [
            "Pipeline Health Monitor",
            f"Checked {len(self.checked)} table(s): {', '.join(self.checked)}",
        ]
        if not self.findings:
            lines.append("OK — no problems found. All tables are fresh and match their baselines.")
            return "\n".join(lines)
        critical = sum(1 for f in self.findings if f.severity == "critical")
        warning = len(self.findings) - critical
        lines.append(
            f"FOUND {len(self.findings)} problem(s): "
            f"{critical} critical, {warning} warning"
        )
        for finding in self.findings:
            lines.append(f"\n[{finding.severity.upper()}] {finding.summary}")
            lines.append(f"  {finding.explanation}")
        return "\n".join(lines)

    def write_markdown_report(self, out_path: str | Path) -> Path:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Pipeline Health Report",
            "",
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            f"Tables checked: {', '.join(self.checked)}",
            "",
        ]
        if not self.findings:
            lines.append("## Result: healthy")
            lines.append("")
            lines.append("No problems found. All tables are fresh and match their baselines.")
        else:
            critical = sum(1 for f in self.findings if f.severity == "critical")
            lines.append(
                f"## Result: {len(self.findings)} problem(s) "
                f"({critical} critical, {len(self.findings) - critical} warning)"
            )
            for finding in self.findings:
                lines.append("")
                lines.append(f"### [{finding.severity.upper()}] {finding.summary}")
                lines.append("")
                lines.append(f"- **Table:** {finding.table}")
                lines.append(f"- **Check:** {finding.check}")
                if finding.column:
                    lines.append(f"- **Column:** {finding.column}")
                lines.append(f"- **Detail:** {finding.detail}")
                lines.append("")
                lines.append(finding.explanation)
        out_path.write_text("\n".join(lines) + "\n")
        return out_path

    def write_alerts_log(self, out_path: str | Path) -> Path:
        """Append one JSON line per finding to the alerts log."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as fh:
            for finding in self.findings:
                record = finding.to_dict()
                record["timestamp"] = datetime.now().isoformat(timespec="seconds")
                fh.write(json.dumps(record) + "\n")
        return out_path

    def send_email_alert(self, smtp_class=None) -> bool:
        """Send the findings summary by email when configured.

        Returns True only when an email was actually sent. Returns False
        (silently) when there are no findings or email is not configured,
        and False with a stderr warning when sending fails. ``smtp_class``
        is injectable so tests can verify sending without a real server.
        """
        return email_alerts.send_email_alert(
            self.findings, self.checked, self.email_settings, smtp_class=smtp_class
        )


def build_baseline(config_path: str | Path, baseline_path: str | Path) -> Path:
    """Create a baseline file from the current state of the tables.

    Run this once on known-good data. The monitor then compares every
    later run against this snapshot.
    """
    config = json.loads(Path(config_path).read_text())
    baseline: dict = {"tables": {}, "generated_at": datetime.now().isoformat()}
    metadata: dict = {}
    for table_cfg in config["tables"]:
        source = table_cfg["source"]
        if source["type"] == "sqlite":
            conn = sqlite3.connect(source["path"])
            try:
                df = pd.read_sql_query(f"SELECT * FROM {table_cfg['name']}", conn)
            finally:
                conn.close()
        elif source["type"] == "csv":
            df = pd.read_csv(source["path"])
        else:
            raise ValueError(f"Unknown source type: {source['type']}")
        baseline["tables"][table_cfg["name"]] = snapshot_table(df)
        metadata[table_cfg["name"]] = datetime.now().isoformat()
    baseline_path = Path(baseline_path)
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(json.dumps(baseline, indent=2))
    metadata_path = config.get("metadata_path")
    if metadata_path:
        Path(metadata_path).parent.mkdir(parents=True, exist_ok=True)
        Path(metadata_path).write_text(json.dumps(metadata, indent=2))
    return baseline_path
