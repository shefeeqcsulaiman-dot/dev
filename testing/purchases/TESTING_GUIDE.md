# Purchases Module Testing Guide

## Scope

Validate purchase invoice, supplier bill, input VAT, inventory receipt, and duplicate prevention behavior.

## Required Checks

- Purchase invoice creation
- Supplier TRN validation
- Duplicate supplier invoice prevention
- VAT input calculation
- Source transaction creation
- Approval before posting where required
- Journal posting
- Inventory receipt for stock items
- Audit log creation
- Tenant isolation

## Expected Accounting

```text
Dr Inventory or Expense
Dr VAT Recoverable
Cr Accounts Payable
```

Debit must equal credit.
