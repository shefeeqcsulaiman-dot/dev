# Accounting Testing Guide

## Scope

Validate posting engine correctness, journal integrity, retries, reversals, and period controls.

## Required Checks

- Journal entries are balanced
- Duplicate posting is prevented
- Failed posting retries safely
- Reversal journals are balanced and linked
- Locked periods reject posting
- Source transaction links to journal
- Tax lines are created atomically with posting
- Posting jobs are idempotent
- Audit logs capture posting, retry, reversal, and failure
- Reports read posted ledger data only

## Agent Prompt

```text
Test posting engine.

Validate:
- Journal entries are balanced.
- Duplicate posting prevented.
- Failed posting retried safely.
- Reversal journals created properly.
- Locked periods reject posting.
- Source transaction linked to journal.
- Tax lines created atomically.
```
