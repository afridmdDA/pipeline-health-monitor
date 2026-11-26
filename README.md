# Pipeline Health Monitor

A small Python tool that watches data tables for pipeline problems and
explains them in plain English — so a data team finds out a pipeline
broke **before** a stakeholder finds out the dashboard is wrong.

## The real problem

Data pipelines usually fail silently. A source system renames a column,
an upstream job stops sending a field, or a daily load quietly stops
running. Nothing alerts anyone. The first sign of trouble is a manager
asking why the numbers look wrong — days later, after bad decisions
have already been made on stale or broken data.

This tool is the smoke alarm for that: it compares each table against a
stored baseline from a known-good run and flags the five most common
failure modes, each with a plain-English explanation of what broke and
why it matters.

## What it checks

| Check | What it catches |
|---|---|
| **Freshness** | A table that hasn't been updated within its expected schedule (stale data) |
| **Row-count change** | A sudden drop or spike in rows vs the baseline (failed load, duplicates) |
| **Null-rate spike** | A column that suddenly goes mostly missing (source stopped sending a field) |
| **Schema drift** | Columns added or removed vs the baseline (source changed its shape) |
| **Value drift** | A numeric column whose average moved far from normal (unit change, new population) |

Every failed check produces a plain-English explanation — not just a
metric. Example:

> **[CRITICAL] products.price null rate spiked from 2.0% to 62.0%**
> The price column in products went from 2.0% missing to 62.0% missing.
> That is not random noise — something upstream stopped sending this
> field, a source changed its format, or a transformation started
> blanking values. Any report, filter or model that uses price is now
> working with mostly empty data, so results will be incomplete or
> misleading.

## Architecture

```
pipeline-health-monitor/
├── src/pipeline_health_monitor/
│   ├── checks.py      # pure check functions (freshness, row count, nulls, schema, drift)
│   ├── monitor.py     # loads tables, runs checks, writes reports
│   ├── explain.py     # optional LLM rewrite of explanations (OpenAI, stdlib only)
│   └── cli.py         # `run` and `baseline` commands
├── demo/
│   ├── generate_data.py  # builds sample orders/customers/products tables
│   ├── inject_faults.py  # injects a stale table, a schema change, a null spike
│   └── run_demo.sh       # end-to-end demo with verification
├── tests/
│   └── test_checks.py    # unit tests for the check functions (unittest, no extra deps)
├── requirements.txt   # pandas only
└── README.md
```

Design choices:

- **Zero external services.** Inputs are SQLite databases and CSV files.
  The only dependency is pandas; everything else is the standard library.
- **Baseline comparison.** A baseline snapshot (row counts, null rates,
  schemas, numeric means/stds, last-updated times) is captured once on
  known-good data. Every later run is diffed against it.
- **Checks are pure functions.** Each check takes a snapshot and a
  baseline and returns a finding — easy to test, easy to extend.
- **Three outputs.** A console summary, a markdown report, and a
  JSON-lines alerts log (one line per finding, append-only).
- **Optional AI explanations.** If `OPENAI_API_KEY` is set, the
  `--enhance` flag asks an LLM to rewrite each explanation in shorter,
  manager-friendly language. Without a key the tool works fully on its
  built-in explanations — the AI layer is a convenience, not a
  requirement.

## How to run

```bash
pip install -r requirements.txt

# 1. Build a baseline from known-good data
python -m pipeline_health_monitor.cli baseline \
    --config demo/config.json --baseline demo/baseline.json

# 2. Run the checks
python -m pipeline_health_monitor.cli run \
    --config demo/config.json --baseline demo/baseline.json \
    --report reports/latest.md --alerts alerts.log

# Exit code is 1 when a critical finding exists, 0 otherwise.

# 3. (Optional) friendlier explanations via OpenAI
export OPENAI_API_KEY=sk-...
python -m pipeline_health_monitor.cli run --config demo/config.json \
    --baseline demo/baseline.json --enhance
```

Run from the project root with `PYTHONPATH=src`, or install the package
with `pip install -e .` (no `setup.py` is included yet — the demo uses
`PYTHONPATH=src`).

### Config format

`config.json` lists the tables to watch and their sources:

```json
{
  "metadata_path": "demo/metadata.json",
  "tables": [
    {
      "name": "orders",
      "source": {"type": "sqlite", "path": "demo/data/pipeline.db"},
      "expected_interval_hours": 24
    },
    {
      "name": "products",
      "source": {"type": "csv", "path": "demo/data/products.csv"},
      "expected_interval_hours": 24
    }
  ]
}
```

`metadata.json` maps each table to the ISO timestamp of its last update
— written by whatever loads the tables. The freshness check compares it
against `expected_interval_hours`.

## Demo

The demo builds a small simulated order pipeline (2,500 orders,
800 customers, 300 products), verifies a healthy run, then injects three
realistic faults and verifies the monitor catches all of them:

```bash
bash demo/run_demo.sh
```

What it does:

1. **Generate** sample tables and a baseline.
2. **Clean run** — the monitor reports healthy (exit 0).
3. **Inject faults:**
   - (a) the `orders` table goes stale — its last-updated time is
     backdated 3 days, as if the daily job stopped running;
   - (b) the `customers` table changes shape — the `email` column is
     dropped and a new `loyalty_tier` column appears;
   - (c) the `products` table gets a null spike — ~60% of prices are
     blanked, as if the source stopped sending the field.
4. **Faulted run** — the monitor reports all three (exit 1), and the
   script verifies each check type appears in the report.

Example faulted output (from `reports/faulted.md`):

```
## Result: 4 problem(s) (3 critical, 1 warning)

### [CRITICAL] orders is stale: last updated 3d 0h ago
### [CRITICAL] customers lost column(s): email
### [WARNING] customers gained new column(s): loyalty_tier
### [CRITICAL] products.price null rate spiked from 2.0% to 62.0%
```

(The schema change produces two findings — one critical for the dropped
column, one warning for the added column — from a single injected fault.)

## Tests

```bash
python -m unittest discover -s tests -v
```

14 unit tests cover the five check functions and the snapshot builder.
They use only the standard library.

## What this is not

- This is a portfolio project built on simulated data. It has not been
  used in production and makes no claim about real companies or real
  incidents.
- The sample data is randomly generated. The "faults" are deliberately
  injected for demonstration.
- The thresholds (20% row-count change, 10-point null-rate rise,
  2-standard-deviation value drift) are sensible defaults, not tuned
  values — a real deployment would tune them per table.
