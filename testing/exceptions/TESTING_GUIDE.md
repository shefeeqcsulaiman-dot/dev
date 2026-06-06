# Exception Center Testing Guide

## Scope

Validate creation, severity, retry, resolution, and audit handling for operational exceptions.

## Simulations

- Failed posting
- OCR extraction failure
- Duplicate invoice
- VAT mismatch
- Unmapped stock item
- Locked period posting attempt
- Missing tax evidence

## Required Checks

- Exception record is created
- Severity is assigned
- Source object is linked
- Retry works when the issue is recoverable
- Non-recoverable issue cannot be silently ignored
- Resolution is logged
- Audit trail includes user, company, action, and timestamp
- Tenant isolation is enforced
