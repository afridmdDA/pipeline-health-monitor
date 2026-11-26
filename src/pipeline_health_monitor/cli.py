"""Command-line interface for the pipeline health monitor."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running as `python -m pipeline_health_monitor.cli` from src/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline_health_monitor.monitor import Monitor, build_baseline  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pipeline-health-monitor",
        description="Check data tables for pipeline problems and explain them in plain English.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run checks against the baseline")
    run.add_argument("--config", required=True, help="Path to config JSON")
    run.add_argument("--baseline", required=True, help="Path to baseline JSON")
    run.add_argument("--report", default="reports/latest.md", help="Markdown report output path")
    run.add_argument("--alerts", default="alerts.log", help="Alerts log output path")
    run.add_argument(
        "--enhance",
        action="store_true",
        help="Rewrite explanations with OpenAI if OPENAI_API_KEY is set (works without it)",
    )

    base = sub.add_parser("baseline", help="Build a baseline from current data")
    base.add_argument("--config", required=True, help="Path to config JSON")
    base.add_argument("--baseline", required=True, help="Path to write baseline JSON")

    args = parser.parse_args(argv)

    if args.command == "baseline":
        path = build_baseline(args.config, args.baseline)
        print(f"Baseline written to {path}")
        return 0

    monitor = Monitor(args.config, args.baseline)
    findings = monitor.run(enhance=args.enhance)
    print(monitor.console_summary())
    report = monitor.write_markdown_report(args.report)
    print(f"\nReport written to {report}")
    if findings:
        log = monitor.write_alerts_log(args.alerts)
        print(f"Alerts appended to {log}")
        # Only sends when findings exist and email is configured;
        # silently skipped otherwise (see email_alerts.py).
        if monitor.send_email_alert():
            recipients = ", ".join(monitor.email_settings.recipients)
            print(f"Email alert sent to {recipients}")
    return 1 if any(f.severity == "critical" for f in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
