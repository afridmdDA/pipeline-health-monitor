# Pipeline Health Report

Generated: 2026-11-26T20:28:53
Tables checked: orders, customers, products

## Result: 4 problem(s) (3 critical, 1 warning)

### [CRITICAL] orders is stale: last updated 3d 0h ago

- **Table:** orders
- **Check:** freshness
- **Detail:** Last updated 2026-11-23T20:28:52.687766, 72.0 hours ago; expected at least every 24 hours.

The orders table is stale. Its data is 72.0 hours old, but this pipeline is supposed to refresh it every 24 hours. Every report and dashboard built on this table is now showing old numbers, and anything that depends on fresh data (alerts, billing, inventory) is making decisions on yesterday's picture. The usual causes are a failed or stuck pipeline run, a source system outage, or a schedule that was changed or disabled.

### [CRITICAL] customers lost column(s): email

- **Table:** customers
- **Check:** schema_drift
- **Detail:** Baseline columns: ['country', 'customer_id', 'email', 'name', 'signup_year']. Current columns: ['country', 'customer_id', 'loyalty_tier', 'name', 'signup_year'].

The customers table lost the column(s) email. Any query, dashboard or downstream job that reads those columns will now fail outright or silently return nothing. This usually happens when a source system renames or drops a field, or when a pipeline change drops a column by mistake. Nothing downstream can be trusted until the missing columns are restored or the downstream code is updated to the new schema.

### [WARNING] customers gained new column(s): loyalty_tier

- **Table:** customers
- **Check:** schema_drift
- **Detail:** Baseline columns: ['country', 'customer_id', 'email', 'name', 'signup_year']. Current columns: ['country', 'customer_id', 'loyalty_tier', 'name', 'signup_year'].

The customers table gained new column(s): loyalty_tier. New columns are usually harmless, but they can mean the source changed its shape, and any strict schema validation or column-position logic downstream may break. Worth a quick look to confirm the new columns are expected.

### [CRITICAL] products.price null rate spiked from 1.3% to 64.0%

- **Table:** products
- **Check:** null_rate
- **Column:** price
- **Detail:** Baseline null rate 1.3%; current 64.0%; rise 62.7 percentage points; threshold 10 points.

The price column in products went from 1.3% missing to 64.0% missing. That is not random noise — something upstream stopped sending this field, a source changed its format, or a transformation started blanking values. Any report, filter or model that uses price is now working with mostly empty data, so results will be incomplete or misleading.
