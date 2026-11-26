#!/usr/bin/env bash
# End-to-end demo: generate clean data, verify a healthy run, inject
# three faults, and verify the monitor catches all three.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=src

echo "=== 1. Generate sample pipeline data ==="
python3 demo/generate_data.py

echo ""
echo "=== 2. Clean run (expect: healthy, exit 0) ==="
python3 -m pipeline_health_monitor.cli run \
    --config demo/config.json \
    --baseline demo/baseline.json \
    --report reports/clean.md \
    --alerts alerts.log

echo ""
echo "=== 3. Inject three faults ==="
python3 demo/inject_faults.py

echo ""
echo "=== 4. Faulted run (expect: 3 fault types caught, exit 1) ==="
set +e
python3 -m pipeline_health_monitor.cli run \
    --config demo/config.json \
    --baseline demo/baseline.json \
    --report reports/faulted.md \
    --alerts alerts.log
code=$?
set -e
if [ "$code" -ne 1 ]; then
    echo "FAIL: expected exit code 1 (critical findings), got $code"
    exit 1
fi

echo ""
echo "=== 5. Verify all three injected faults were caught ==="
for check in freshness schema_drift null_rate; do
    if grep -q "Check:\*\* $check" reports/faulted.md; then
        echo "  caught: $check"
    else
        echo "  MISSED: $check"
        exit 1
    fi
done
echo ""
echo "DEMO PASS: stale table, schema change, and null spike all caught."
