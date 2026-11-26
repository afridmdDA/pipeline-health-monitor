"""Inject three realistic faults into the demo data.

  (a) Stale table   — backdate the orders last-updated time by 3 days,
                      as if the daily orders job stopped running.
  (b) Schema change — drop the `email` column from customers and add a
                      new `loyalty_tier` column, as if the source system
                      changed its shape.
  (c) Null spike    — blank out ~60% of product prices, as if the source
                      stopped sending the price field.

Usage:
    python demo/inject_faults.py

After running this, `python -m pipeline_health_monitor.cli run ...`
must report all three problems.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DEMO_DIR = Path(__file__).resolve().parent
DB_PATH = DEMO_DIR / "data" / "pipeline.db"
PRODUCTS_CSV = DEMO_DIR / "data" / "products.csv"
METADATA_PATH = DEMO_DIR / "metadata.json"

random.seed(7)


def inject_stale_table() -> None:
    metadata = json.loads(METADATA_PATH.read_text())
    stale_time = datetime.now() - timedelta(days=3)
    metadata["orders"] = stale_time.isoformat()
    METADATA_PATH.write_text(json.dumps(metadata, indent=2))
    print(f"(a) orders last-updated backdated to {stale_time.isoformat()} (3 days ago)")


def inject_schema_change() -> None:
    conn = sqlite3.connect(DB_PATH)
    # SQLite supports DROP COLUMN since 3.35.0.
    conn.execute("ALTER TABLE customers DROP COLUMN email")
    conn.execute("ALTER TABLE customers ADD COLUMN loyalty_tier TEXT")
    conn.execute("UPDATE customers SET loyalty_tier = 'standard'")
    conn.commit()
    cols = [row[1] for row in conn.execute("PRAGMA table_info(customers)")]
    conn.close()
    print(f"(b) customers schema changed — columns now: {cols}")


def inject_null_spike() -> None:
    products = pd.read_csv(PRODUCTS_CSV)
    mask = products.index.map(lambda _: random.random() < 0.60)
    products.loc[mask, "price"] = None
    products.to_csv(PRODUCTS_CSV, index=False)
    null_rate = products["price"].isna().mean()
    print(f"(c) products.price null rate now {null_rate:.0%} (was ~2%)")


def main() -> None:
    inject_stale_table()
    inject_schema_change()
    inject_null_spike()
    print("\nThree faults injected. Run the monitor to see them caught.")


if __name__ == "__main__":
    main()
