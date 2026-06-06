# TaxFlow Testing Rules

TaxFlow testing must validate the full business chain, not only screen clicks or HTTP status codes.

## Test Layers

```text
1. Unit Testing
2. API Testing
3. Integration Testing
4. Workflow Testing
5. End-to-End Testing
```

## Every Module Must Validate

- Tenant isolation
- Audit logging
- Permission checks
- Duplicate prevention
- Accounting balance
- VAT calculation
- Source transaction creation
- Approval state transitions
- Exception handling
- Database integrity

## Never Allow

- Direct UI to ledger posting
- Negative inventory unless explicitly enabled
- Unbalanced journals
- Missing tax evidence for taxable transactions
- Posting without approval when approval is required
- Cross-tenant reads or writes
- Silent retry after a non-idempotent failure
- Reports reading from draft or unposted ledger data unless clearly marked

## Required Failure Report

For every failed test, the agent must report:

- Root cause
- Affected tables
- Affected APIs
- Affected workflow stage
- Severity
- Suggested fix
- Regression test to add

## Production Flow To Validate

```text
UI Action
    |
    v
API Validation
    |
    v
Source Transaction
    |
    v
Approval
    |
    v
Posting Queue
    |
    v
Journal Entry
    |
    v
Tax Lines
    |
    v
Audit Log
    |
    v
Reports
```

Agent rule:

```text
Validate every stage that exists for the module under test.
If a stage is not implemented yet, mark it as a gap instead of passing the test.
```

## Recommended Stack

| Area | Tool |
| --- | --- |
| Frontend | Playwright |
| API | Pytest + FastAPI TestClient |
| Backend logic | Pytest |
| Database | Transaction and constraint tests |
| E2E | Playwright |
| Queue | Celery integration tests |
| Load | k6 or Locust |
| Security | OWASP ZAP plus focused API authorization tests |

## Agent Master Prompt

```text
You are a senior QA architect for TaxFlow UAE.

Rules:
- Never bypass accounting controls.
- Validate tenant isolation.
- Verify audit trail.
- Ensure all journals balance.
- Ensure VAT calculations are correct.
- Validate stock unit conversions.
- Test retry and reversal behavior.
- Detect race conditions.
- Validate API permissions.

For every failed test:
- Explain root cause.
- Suggest fix.
- Provide affected tables.
- Provide affected APIs.
- Estimate severity.
```
