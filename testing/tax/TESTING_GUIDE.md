# VAT Testing Guide

## Scope

Validate UAE VAT treatment, tax lines, VAT returns, evidence links, and period locking.

## Required Checks

- VAT_OUTPUT_5
- VAT_INPUT_5
- Reverse charge
- Exempt transactions
- Zero-rated transactions where supported
- VAT return generation
- VAT period locking
- Tax evidence links
- VAT mismatch exception creation
- Tenant isolation

## Reporting Rule

```text
VAT reports must read from tax_lines only.
They must not recalculate from raw invoice rows as the source of truth.
```

## Agent Prompt

```text
Test VAT engine.

Validate:
- VAT_OUTPUT_5
- VAT_INPUT_5
- Reverse charge
- Exempt transactions
- VAT return generation
- VAT period locking
- Tax evidence links

Ensure VAT report reads from tax_lines table only.
```
