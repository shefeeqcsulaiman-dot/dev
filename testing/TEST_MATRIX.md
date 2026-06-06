# TaxFlow Automated Test Matrix

| Priority | Module | Test Type | Core Validation | Status |
| --- | --- | --- | --- | --- |
| 1 | Accounting | Posting Engine | Balanced journals, duplicate posting, reversal, locked periods | Pending |
| 2 | VAT | Workflow | VAT output/input, reverse charge, exempt, return generation | Pending |
| 3 | Inventory | Integration | Unit conversion, valuation, COGS, stock movements | Pending |
| 4 | Security | API/Workflow | Tenant isolation, JWT, permissions, upload validation | Pending |
| 5 | Source Transactions | Integration | Creation, validation, approval, journal linkage | Pending |
| 6 | Sales | API/E2E | Invoice lifecycle, VAT, source transaction, posting, audit | Pending |
| 7 | Purchases | API/E2E | Bill lifecycle, input VAT, stock receipt, duplicate detection | Pending |
| 8 | Exceptions | Workflow | Failed posting, OCR failure, duplicate, VAT mismatch, retry | Pending |
| 9 | Audit | Integration | Sensitive action logs, before/after values, user/company context | Pending |
| 10 | Performance | Load | Concurrent postings, report speed, API latency | Pending |
| 11 | AI/OCR | Workflow | Extraction, confidence, duplicate detection, rejection | Pending |
| 12 | Payroll | Security/Workflow | Permission checks, payroll run approval, WPS generation | Pending |

## Priority Order

```text
1. Accounting posting
2. VAT engine
3. Inventory valuation
4. Tenant isolation
5. Source transactions
6. Approval workflow
7. Audit logging
8. Exception handling
9. Queue retry
10. Period locking
```
