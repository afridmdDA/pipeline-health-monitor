"""Email alerts for findings.

Optional layer, like explain.py: the monitor works fully without it.
When an ``email`` block is present and enabled in config.json, a run
with at least one finding sends a plain-text summary by email (stdlib
smtplib only — no extra dependencies, no external service beyond the
SMTP server the user points it at). When email is not configured, or
when a run finds nothing, nothing is sent and the monitor runs exactly
as before. Sending never raises: any failure is reported on stderr
and the run continues normally.
"""

from __future__ import annotations

import os
import smtplib
import sys
from dataclasses import dataclass, field
from email.message import EmailMessage

from .checks import Finding

_DEFAULT_PASSWORD_ENV = "PIPELINE_SMTP_PASSWORD"


@dataclass
class EmailSettings:
    """SMTP settings, read from the ``email`` block in config.json."""

    host: str
    port: int = 587
    use_tls: bool = True  # STARTTLS (port 587); set use_ssl for port 465
    use_ssl: bool = False
    user: str | None = None
    password_env: str = _DEFAULT_PASSWORD_ENV
    sender: str = ""
    recipients: list[str] = field(default_factory=list)


def email_settings_from_config(config: dict) -> EmailSettings | None:
    """Parse the ``email`` block of a config dict.

    Returns None when email is absent, disabled, or missing the pieces
    needed to send (a host and at least one recipient). Returning None
    is normal — it just means "email not configured".
    """
    email = config.get("email")
    if not isinstance(email, dict):
        return None
    if not email.get("enabled", False):
        return None

    host = email.get("host")
    if not host:
        return None

    to = email.get("to")
    if isinstance(to, str):
        recipients = [to]
    elif isinstance(to, list):
        recipients = [str(r) for r in to if r]
    else:
        recipients = []
    if not recipients:
        return None

    sender = email.get("from") or email.get("user") or ""
    if not sender:
        return None

    return EmailSettings(
        host=str(host),
        port=int(email.get("port", 587)),
        use_tls=bool(email.get("use_tls", True)),
        use_ssl=bool(email.get("use_ssl", False)),
        user=email.get("user"),
        password_env=str(email.get("password_env", _DEFAULT_PASSWORD_ENV)),
        sender=str(sender),
        recipients=recipients,
    )


def format_email_subject(findings: list[Finding]) -> str:
    """One-line subject summarising the run.

    Kept plain ASCII so it never needs RFC 2047 encoding.
    """
    if not findings:
        return "Pipeline Health Monitor: healthy - no problems found"
    critical = sum(1 for f in findings if f.severity == "critical")
    warning = len(findings) - critical
    return (
        f"Pipeline Health Monitor: {len(findings)} problem(s) - "
        f"{critical} critical, {warning} warning"
    )


def format_email_body(findings: list[Finding], checked: list[str]) -> str:
    """Plain-text summary of a run: the same plain-English findings
    the console summary shows, formatted for email."""
    lines = ["Pipeline Health Monitor"]
    lines.append(f"Checked {len(checked)} table(s): {', '.join(checked)}")
    lines.append("")
    if not findings:
        lines.append("No problems found. All tables are fresh and match their baselines.")
        return "\n".join(lines) + "\n"

    critical = sum(1 for f in findings if f.severity == "critical")
    warning = len(findings) - critical
    lines.append(
        f"Found {len(findings)} problem(s): {critical} critical, {warning} warning"
    )
    for finding in findings:
        lines.append("")
        lines.append(f"[{finding.severity.upper()}] {finding.summary}")
        lines.append(finding.explanation)
    return "\n".join(lines) + "\n"


def send_email_alert(
    findings: list[Finding],
    checked: list[str],
    settings: EmailSettings | None,
    smtp_class=None,
) -> bool:
    """Send the findings summary by email. Returns True only when an
    email was actually sent.

    Returns False — without raising — when there are no findings, when
    email is not configured, or when sending fails (bad credentials,
    unreachable server, ...). Failures are reported on stderr so a
    scheduled run does not fail silently, but the monitor run itself
    is unaffected.
    """
    if not findings:
        return False
    if settings is None:
        return False

    password = None
    if settings.user:
        password = os.environ.get(settings.password_env)
        if not password:
            print(
                f"Warning: email alert not sent — SMTP user is set but the "
                f"password env var {settings.password_env} is empty or unset.",
                file=sys.stderr,
            )
            return False

    message = EmailMessage()
    message["Subject"] = format_email_subject(findings)
    message["From"] = settings.sender
    message["To"] = ", ".join(settings.recipients)
    message.set_content(format_email_body(findings, checked))

    if smtp_class is None:
        smtp_class = smtplib.SMTP_SSL if settings.use_ssl else smtplib.SMTP
    try:
        with smtp_class(settings.host, settings.port, timeout=30) as server:
            server.ehlo()
            if settings.use_tls and not settings.use_ssl:
                server.starttls()
                server.ehlo()
            if settings.user:
                server.login(settings.user, password)
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001 — any SMTP failure skips email
        print(f"Warning: email alert not sent — {exc}", file=sys.stderr)
        return False
    return True
