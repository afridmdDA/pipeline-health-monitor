# Feature backlog — pipeline-health-monitor

One item per Wednesday. Pick the top unchecked item, implement it, run the
tests + demo, update the README, push to GitHub, and mark it done here.
Do not repeat a shipped feature. Keep every feature small, real, and
demo-verified — no fake metrics, no production claims.

## Backlog

- [ ] Email alert output — send the report summary by email (SMTP via stdlib) when a check fails
- [ ] Per-table thresholds in config.json — freshness window and null-rate limit set per table instead of global defaults
- [ ] `check` CLI command — run checks for a single table instead of the whole config
- [ ] Row-count trend in the report — show the last 7 runs of row counts next to the current value
- [ ] Slack webhook alert — post a short message to a Slack webhook on critical findings
- [ ] Anomaly check on row counts — flag a count outside mean ± 3 standard deviations of history
- [ ] CSV freshness check — freshness for CSV inputs based on file modification time
- [ ] HTML report option — render the markdown report as a simple standalone HTML page
- [ ] Duplicate-row check — flag duplicate primary keys in a table
- [ ] Config validation command — `validate` checks the config file for bad table names, missing files, and bad thresholds before a run

## Shipped

- (none yet — first feature ships Wednesday, Dec 2, 2026)
