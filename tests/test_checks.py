"""Unit tests for the check functions.

Run with the standard library only:
    python -m unittest discover -s tests -v
"""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pipeline_health_monitor.checks import (  # noqa: E402
    check_freshness,
    check_null_rates,
    check_row_count,
    check_schema_drift,
    check_value_drift,
    snapshot_table,
)


class TestFreshness(unittest.TestCase):
    def test_fresh_table_passes(self):
        result = check_freshness(
            "orders", datetime.now() - timedelta(hours=2), timedelta(hours=24)
        )
        self.assertIsNone(result)

    def test_stale_table_is_flagged(self):
        result = check_freshness(
            "orders", datetime.now() - timedelta(days=3), timedelta(hours=24)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.check, "freshness")
        self.assertEqual(result.severity, "critical")
        self.assertIn("stale", result.summary)

    def test_never_updated_is_flagged(self):
        result = check_freshness("orders", None, timedelta(hours=24))
        self.assertIsNotNone(result)
        self.assertIn("never been updated", result.summary)


class TestRowCount(unittest.TestCase):
    def test_small_change_passes(self):
        self.assertIsNone(check_row_count("orders", 1050, 1000))

    def test_big_drop_is_flagged(self):
        result = check_row_count("orders", 400, 1000)
        self.assertIsNotNone(result)
        self.assertIn("dropped", result.summary)
        self.assertEqual(result.severity, "critical")

    def test_big_spike_is_flagged(self):
        result = check_row_count("orders", 1300, 1000)
        self.assertIsNotNone(result)
        self.assertIn("jumped", result.summary)


class TestNullRates(unittest.TestCase):
    def test_spike_is_flagged(self):
        findings = check_null_rates(
            "products", {"price": 0.62}, {"price": 0.02}
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check, "null_rate")
        self.assertEqual(findings[0].column, "price")
        self.assertIn("62.0%", findings[0].summary)

    def test_small_rise_passes(self):
        findings = check_null_rates(
            "products", {"price": 0.05}, {"price": 0.02}
        )
        self.assertEqual(findings, [])


class TestSchemaDrift(unittest.TestCase):
    def test_removed_column_is_flagged(self):
        findings = check_schema_drift(
            "customers", ["customer_id", "name"], ["customer_id", "name", "email"]
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "critical")
        self.assertIn("email", findings[0].summary)

    def test_added_column_is_a_warning(self):
        findings = check_schema_drift(
            "customers",
            ["customer_id", "name", "loyalty_tier"],
            ["customer_id", "name"],
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warning")

    def test_unchanged_schema_passes(self):
        self.assertEqual(
            check_schema_drift("customers", ["a", "b"], ["a", "b"]), []
        )


class TestValueDrift(unittest.TestCase):
    def test_shifted_mean_is_flagged(self):
        findings = check_value_drift(
            "orders",
            {"amount": {"mean": 150.0, "std": 18.0}},
            {"amount": {"mean": 50.0, "std": 18.0}},
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check, "value_drift")

    def test_normal_mean_passes(self):
        findings = check_value_drift(
            "orders",
            {"amount": {"mean": 52.0, "std": 18.0}},
            {"amount": {"mean": 50.0, "std": 18.0}},
        )
        self.assertEqual(findings, [])


class TestSnapshot(unittest.TestCase):
    def test_snapshot_shape(self):
        df = pd.DataFrame({"a": [1, 2, None], "b": ["x", "y", "z"]})
        snap = snapshot_table(df)
        self.assertEqual(snap["row_count"], 3)
        self.assertEqual(snap["columns"], ["a", "b"])
        self.assertAlmostEqual(snap["null_rates"]["a"], 1 / 3)
        self.assertAlmostEqual(snap["numeric_stats"]["a"]["mean"], 1.5)


if __name__ == "__main__":
    unittest.main()
