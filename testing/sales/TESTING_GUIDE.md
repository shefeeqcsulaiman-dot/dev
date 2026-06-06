# Sales Module Testing Guide

## Scope

Validate the sales invoice workflow from UI/API creation through accounting, VAT, audit, and reports.

## Required Checks

- Invoice creation
- Duplicate invoice prevention
- Customer TRN validation
- VAT calculation
- Source transaction creation
- Approval and posting state transitions
- Journal posting
- Audit log creation
- Tenant isolation
- Share functionality
- PDF generation

## Expected Accounting

```text
Dr Accounts Receivable
Cr Sales
Cr VAT Payable
```

Debit must equal credit.

## Agent Prompt

```text
Test Sales Invoice workflow.

Validate:
- Invoice creation
- Duplicate invoice prevention
- TRN validation
- VAT calculation
- Source transaction creation
- Journal posting
- Audit log creation
- Tenant isolation
- Share functionality
- PDF generation

Expected accounting:
Dr Accounts Receivable
Cr Sales
Cr VAT Payable

Ensure debit equals credit.
```
