"""Unit tests for the email alert output.

Run with the standard library only:
    python -m unittest discover -s tests -v

Sending is verified with a recording fake SMTP class — no network,
no real server. ``smtplib.SMTP`` is only ever touched through the
injectable ``smtp_class`` argument.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pipeline_health_monitor.checks import Finding  # noqa: E402
from pipeline_health_monitor.email_alerts import (  # noqa: E402
    email_settings_from_config,
    format_email_body,
    format_email_subject,
    send_email_alert,
)
from pipeline_health_monitor.monitor import Monitor  # noqa: E402


def _finding(summary="orders is stale: last updated 3d 0h ago",
             severity="critical"):
    return Finding(
        table="orders",
        check="freshness",
        severity=severity,
        summary=summary,
        detail="Last updated 3 days ago; expected every 24 hours.",
        explanation="The orders table is stale. Every report built on it is showing old numbers.",
    )


class FakeSMTP:
    """Records every call instead of talking to a server."""

    instances: list = []

    def __init__(self, host, port, timeout=None):
        self.host = host
        self.port = port
        self.calls: list = []
        self.sent_messages: list = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.quit()
        return False

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self):
        self.calls.append("starttls")

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, message):
        self.calls.append("send_message")
        self.sent_messages.append(message)

    def quit(self):
        self.calls.append("quit")


class FailingSMTP(FakeSMTP):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        raise ConnectionError("cannot reach smtp.example.com")


def _settings(**overrides):
    base = {
        "host": "smtp.example.com",
        "port": 587,
        "use_tls": True,
        "from": "alerts@example.com",
        "to": ["team@example.com"],
    }
    base.update(overrides)
    return base


class TestFormatEmail(unittest.TestCase):
    def test_subject_counts_problems(self):
        findings = [_finding(), _finding("products.price null rate spiked", "warning")]
        self.assertEqual(
            format_email_subject(findings),
            "Pipeline Health Monitor: 2 problem(s) - 1 critical, 1 warning",
        )

    def test_subject_healthy(self):
        self.assertEqual(
            format_email_subject([]),
            "Pipeline Health Monitor: healthy - no problems found",
        )

    def test_body_lists_every_finding(self):
        findings = [_finding(), _finding("products.price null rate spiked", "warning")]
        body = format_email_body(findings, ["orders", "products"])
        self.assertIn("Checked 2 table(s): orders, products", body)
        self.assertIn("Found 2 problem(s): 1 critical, 1 warning", body)
        self.assertIn("[CRITICAL] orders is stale", body)
        self.assertIn("[WARNING] products.price null rate spiked", body)
        # Plain-English explanation travels with the headline.
        self.assertIn("Every report built on it is showing old numbers", body)

    def test_body_healthy(self):
        body = format_email_body([], ["orders"])
        self.assertIn("No problems found", body)


class TestEmailSettings(unittest.TestCase):
    def test_absent_block_is_none(self):
        self.assertIsNone(email_settings_from_config({"tables": []}))

    def test_disabled_is_none(self):
        config = {"email": _settings(enabled=False)}
        self.assertIsNone(email_settings_from_config(config))

    def test_enabled_missing_is_none(self):
        # Email is opt-in: no "enabled" flag means do not send.
        config = {"email": _settings()}
        self.assertIsNone(email_settings_from_config(config))

    def test_full_config_parses(self):
        config = {"email": _settings(enabled=True)}
        settings = email_settings_from_config(config)
        self.assertIsNotNone(settings)
        self.assertEqual(settings.host, "smtp.example.com")
        self.assertEqual(settings.port, 587)
        self.assertTrue(settings.use_tls)
        self.assertFalse(settings.use_ssl)
        self.assertEqual(settings.sender, "alerts@example.com")
        self.assertEqual(settings.recipients, ["team@example.com"])

    def test_defaults(self):
        config = {"email": {
            "enabled": True, "host": "smtp.example.com",
            "from": "a@example.com", "to": "b@example.com",
        }}
        settings = email_settings_from_config(config)
        self.assertEqual(settings.port, 587)
        self.assertTrue(settings.use_tls)
        self.assertEqual(settings.password_env, "PIPELINE_SMTP_PASSWORD")

    def test_to_string_becomes_one_recipient(self):
        config = {"email": _settings(enabled=True, to="solo@example.com")}
        settings = email_settings_from_config(config)
        self.assertEqual(settings.recipients, ["solo@example.com"])

    def test_from_falls_back_to_user(self):
        config = {"email": {
            "enabled": True, "host": "smtp.example.com",
            "user": "alerts@example.com", "to": ["team@example.com"],
        }}
        settings = email_settings_from_config(config)
        self.assertEqual(settings.sender, "alerts@example.com")

    def test_missing_host_is_none(self):
        config = {"email": _settings(enabled=True, host="")}
        self.assertIsNone(email_settings_from_config(config))

    def test_missing_recipients_is_none(self):
        config = {"email": _settings(enabled=True, to=[])}
        self.assertIsNone(email_settings_from_config(config))


class TestSendEmailAlert(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances.clear()

    def test_sends_with_tls_and_login(self):
        settings = email_settings_from_config(
            {"email": _settings(enabled=True, user="alerts@example.com")}
        )
        with mock.patch.dict(os.environ, {"PIPELINE_SMTP_PASSWORD": "s3cret"}):
            sent = send_email_alert(
                [_finding()], ["orders"], settings, smtp_class=FakeSMTP
            )
        self.assertTrue(sent)
        self.assertEqual(len(FakeSMTP.instances), 1)
        server = FakeSMTP.instances[0]
        self.assertEqual((server.host, server.port), ("smtp.example.com", 587))
        self.assertIn("starttls", server.calls)
        self.assertIn(("login", "alerts@example.com", "s3cret"), server.calls)
        self.assertIn("send_message", server.calls)
        self.assertIn("quit", server.calls)
        message = server.sent_messages[0]
        self.assertEqual(
            message["Subject"], "Pipeline Health Monitor: 1 problem(s) - 1 critical, 0 warning"
        )
        self.assertEqual(message["From"], "alerts@example.com")
        self.assertEqual(message["To"], "team@example.com")
        self.assertIn("orders is stale", message.get_content())

    def test_no_findings_sends_nothing(self):
        settings = email_settings_from_config({"email": _settings(enabled=True)})
        sent = send_email_alert([], ["orders"], settings, smtp_class=FakeSMTP)
        self.assertFalse(sent)
        self.assertEqual(FakeSMTP.instances, [])

    def test_unconfigured_sends_nothing(self):
        sent = send_email_alert([_finding()], ["orders"], None, smtp_class=FakeSMTP)
        self.assertFalse(sent)
        self.assertEqual(FakeSMTP.instances, [])

    def test_no_tls_when_disabled(self):
        settings = email_settings_from_config(
            {"email": _settings(enabled=True, use_tls=False)}
        )
        send_email_alert([_finding()], ["orders"], settings, smtp_class=FakeSMTP)
        self.assertNotIn("starttls", FakeSMTP.instances[0].calls)

    def test_no_login_without_user(self):
        settings = email_settings_from_config({"email": _settings(enabled=True)})
        send_email_alert([_finding()], ["orders"], settings, smtp_class=FakeSMTP)
        calls = FakeSMTP.instances[0].calls
        self.assertFalse(any(c[0] == "login" for c in calls if isinstance(c, tuple)))

    def test_missing_password_env_skips_send(self):
        settings = email_settings_from_config(
            {"email": _settings(enabled=True, user="alerts@example.com")}
        )
        env = {k: v for k, v in os.environ.items() if k != "PIPELINE_SMTP_PASSWORD"}
        with mock.patch.dict(os.environ, env, clear=True):
            sent = send_email_alert(
                [_finding()], ["orders"], settings, smtp_class=FakeSMTP
            )
        self.assertFalse(sent)
        self.assertEqual(FakeSMTP.instances, [])

    def test_smtp_failure_returns_false(self):
        settings = email_settings_from_config({"email": _settings(enabled=True)})
        sent = send_email_alert(
            [_finding()], ["orders"], settings, smtp_class=FailingSMTP
        )
        self.assertFalse(sent)

    def test_default_class_is_smtp_ssl_when_use_ssl(self):
        settings = email_settings_from_config(
            {"email": _settings(enabled=True, use_ssl=True, port=465)}
        )
        with mock.patch(
            "pipeline_health_monitor.email_alerts.smtplib.SMTP_SSL", FakeSMTP
        ):
            sent = send_email_alert([_finding()], ["orders"], settings)
        self.assertTrue(sent)
        self.assertEqual(FakeSMTP.instances[0].port, 465)


class TestMonitorWiring(unittest.TestCase):
    def setUp(self):
        FakeSMTP.instances.clear()

    def _write_project(self, tmp, with_email):
        csv_path = tmp / "products.csv"
        # Half the prices missing vs a 0% baseline -> null-rate spike.
        csv_path.write_text(
            "id,price\n1,10\n2,\n3,10\n4,\n5,10\n6,\n7,10\n8,\n9,10\n10,\n"
        )
        config = {
            "tables": [{
                "name": "products",
                "source": {"type": "csv", "path": str(csv_path)},
                "expected_interval_hours": 24,
            }],
        }
        if with_email:
            config["email"] = _settings(enabled=True)
        config_path = tmp / "config.json"
        config_path.write_text(json.dumps(config))
        baseline = {"tables": {"products": {
            "row_count": 10,
            "columns": ["id", "price"],
            "null_rates": {"id": 0.0, "price": 0.0},
            "numeric_stats": {
                "id": {"mean": 5.5, "std": 3.0},
                "price": {"mean": 10.0, "std": 0.0},
            },
        }}}
        baseline_path = tmp / "baseline.json"
        baseline_path.write_text(json.dumps(baseline))
        return config_path, baseline_path

    def test_monitor_sends_when_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path, baseline_path = self._write_project(Path(tmp), True)
            monitor = Monitor(config_path, baseline_path)
            findings = monitor.run()
            self.assertTrue(findings)
            sent = monitor.send_email_alert(smtp_class=FakeSMTP)
            self.assertTrue(sent)
            message = FakeSMTP.instances[0].sent_messages[0]
            self.assertIn("null rate spiked", message.get_content())

    def test_monitor_skips_when_not_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path, baseline_path = self._write_project(Path(tmp), False)
            monitor = Monitor(config_path, baseline_path)
            monitor.run()
            sent = monitor.send_email_alert(smtp_class=FakeSMTP)
            self.assertFalse(sent)
            self.assertEqual(FakeSMTP.instances, [])


if __name__ == "__main__":
    unittest.main()
