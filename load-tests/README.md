# TaxFlow Load Tests

This folder contains k6 scripts for local performance smoke and pre-production load testing.

## Prerequisites

Run the deterministic QA seed first so the test login exists:

```bash
cd backend
python scripts/seed_testing_data.py
```

Start the API, then run:

```bash
k6 run load-tests/k6-posting-and-reports.js
```

Useful overrides:

```bash
k6 run \
  -e BASE_URL=http://127.0.0.1:8010/api/v1 \
  -e TAXFLOW_EMAIL=qa-admin@taxflowqa.com \
  -e TAXFLOW_PASSWORD=admin123 \
  -e REPORT_VUS=25 \
  -e POSTING_VUS=10 \
  -e REPORT_DURATION=1m \
  -e POSTING_DURATION=1m \
  load-tests/k6-posting-and-reports.js
```

The script validates:

- Auth login
- Report API latency
- Source transaction creation
- Source validation
- Posting approval/queueing
