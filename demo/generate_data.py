"""Generate sample pipeline data and a baseline for the demo.

Creates three small tables that look like a real order pipeline:
  - orders     (SQLite) — one row per order
  - customers  (SQLite) — one row per customer
  - products   (CSV)    — one row per product

Then writes ``config.json`` and builds ``baseline.json`` from the
known-good data, plus ``metadata.json`` with fresh last-updated times.

Usage:
    python demo/generate_data.py
"""

from __future__ import annotations

import json
import random
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pipeline_health_monitor.monitor import build_baseline  # noqa: E402

DEMO_DIR = Path(__file__).resolve().parent
DATA_DIR = DEMO_DIR / "data"
DB_PATH = DATA_DIR / "pipeline.db"
PRODUCTS_CSV = DATA_DIR / "products.csv"
CONFIG_PATH = DEMO_DIR / "config.json"
BASELINE_PATH = DEMO_DIR / "baseline.json"
METADATA_PATH = DEMO_DIR / "metadata.json"

random.seed(42)


def make_orders(n: int = 2500) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "order_id": [f"ORD-{i:05d}" for i in range(1, n + 1)],
            "customer_id": [random.randint(1, 800) for _ in range(n)],
            "product_id": [random.randint(1, 300) for _ in range(n)],
            "amount": [round(random.uniform(10, 500), 2) for _ in range(n)],
            "quantity": [random.randint(1, 5) for _ in range(n)],
            "status": [random.choice(["shipped", "delivered", "pending"]) for _ in range(n)],
        }
    )


def make_customers(n: int = 800) -> pd.DataFrame:
    first = ["Ava", "Liam", "Maya", "Noah", "Zoe", "Ethan", "Ruby", "Owen"]
    last = ["Sharma", "Patel", "Kim", "Garcia", "Nguyen", "Smith", "Ali", "Chen"]
    return pd.DataFrame(
        {
            "customer_id": list(range(1, n + 1)),
            "name": [f"{random.choice(first)} {random.choice(last)}" for _ in range(n)],
            "email": [f"user{i}@example.com" for i in range(1, n + 1)],
            "country": [random.choice(["US", "CA", "GB", "IN", "AU"]) for _ in range(n)],
            "signup_year": [random.randint(2019, 2026) for _ in range(n)],
        }
    )


def make_products(n: int = 300) -> pd.DataFrame:
    categories = ["Electronics", "Clothing", "Home", "Sports", "Books"]
    return pd.DataFrame(
        {
            "product_id": list(range(1, n + 1)),
            "name": [f"Product {i}" for i in range(1, n + 1)],
            "category": [random.choice(categories) for _ in range(n)],
            # ~2% nulls on purpose, so the baseline null rate is small but real.
            "price": [
                None if random.random() < 0.02 else round(random.uniform(5, 200), 2)
                for _ in range(n)
            ],
            "stock": [random.randint(0, 500) for _ in range(n)],
        }
    )


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    orders = make_orders()
    customers = make_customers()
    products = make_products()

    conn = sqlite3.connect(DB_PATH)
    orders.to_sql("orders", conn, index=False)
    customers.to_sql("customers", conn, index=False)
    conn.close()
    products.to_csv(PRODUCTS_CSV, index=False)

    config = {
        "metadata_path": str(METADATA_PATH),
        "tables": [
            {
                "name": "orders",
                "source": {"type": "sqlite", "path": str(DB_PATH)},
                "expected_interval_hours": 24,
            },
            {
                "name": "customers",
                "source": {"type": "sqlite", "path": str(DB_PATH)},
                "expected_interval_hours": 24,
            },
            {
                "name": "products",
                "source": {"type": "csv", "path": str(PRODUCTS_CSV)},
                "expected_interval_hours": 24,
            },
        ],
    }
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    build_baseline(CONFIG_PATH, BASELINE_PATH)
    print(f"Generated: {len(orders):,} orders, {len(customers):,} customers, "
          f"{len(products):,} products")
    print(f"Config:    {CONFIG_PATH}")
    print(f"Baseline:  {BASELINE_PATH}")
    print(f"Metadata:  {METADATA_PATH}")


if __name__ == "__main__":
    main()
