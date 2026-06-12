# TaxFlow System Test Architecture & Feature Gap Report

## 1. Objective

Create a complete testing architecture to verify that all signed-in user functions, module loading, system features, workflows, APIs, reports, accounting controls, VAT logic, inventory logic, and missing features are working correctly.

The test scope covers:

* Login and authenticated access
* Dashboard loading
* Sidebar module loading
* Sales and invoices
* Purchases and vendor bills
* Inventory and stock mapping
* Accounting and journals
* VAT and tax reports
* Bank payments and receipts
* Payroll and staff
* Documents and evidence
* Reports
* Exception Center
* Audit logs
* Tenant isolation
* API stability
* Performance and loading behavior

## 2. Test Architecture

```text
User / Tester
   |
   v
Browser UI Test Layer
   |
   v
API Test Layer
   |
   v
Business Workflow Test Layer
   |
   v
Accounting / VAT / Inventory Validation Layer
   |
   v
Database Integrity Layer
   |
   v
Audit / Security / Tenant Isolation Layer
```

## 3. Testing Layers

### 3.1 UI Loading Tests

Verify:

* Login page loads correctly
* Dashboard loads after login
* Sidebar modules open without blank screen
* KPI cards load values
* Tables load records
* Modals open and close correctly
* Forms save correctly
* Page refresh does not break session
* Local storage and token handling work correctly

Priority modules:

```text
Dashboard
Sales & Invoices
Purchases
Inventory
Bank & Payments
Accounting
Reports
Staff
Payroll
Settings
Exception Center
```

## 4. Signed-In Function Test Matrix

| Area       | Test Case                 | Expected Result                             | Status  |
| ---------- | ------------------------- | ------------------------------------------- | ------- |
| Auth       | Login with valid user     | Dashboard opens                             | To Test |
| Auth       | Login with wrong password | Error shown                                 | To Test |
| Auth       | Refresh after login       | Session remains valid                       | To Test |
| Auth       | Expired token             | User redirected to login                    | To Test |
| Dashboard  | Load dashboard KPIs       | Revenue, VAT, invoice, purchase counts show | To Test |
| Sidebar    | Open each module          | Module loads without JS error               | To Test |
| Sales      | Create invoice            | Invoice saved and visible                   | To Test |
| Sales      | View invoice              | Modal opens with correct data               | To Test |
| Sales      | Download invoice PDF      | PDF/print view works                        | To Test |
| Sales      | Mark invoice paid         | Receipt modal opens pre-filled              | To Test |
| Purchases  | Create manual purchase    | Purchase saved                              | To Test |
| Purchases  | Upload purchase document  | AI/extraction flow works                    | To Test |
| Inventory  | Add item                  | Item appears in item master                 | To Test |
| Inventory  | Add duplicate item        | Blocked with clear error message            | To Test |
| Inventory  | Save mapping              | Status becomes Mapped                       | To Test |
| Inventory  | Stock level check         | Stock updates from purchase                 | To Test |
| Accounting | Create journal            | Balanced journal saved                      | To Test |
| Accounting | Unbalanced journal        | Save blocked                                | To Test |
| VAT        | VAT report load           | VAT 201 figures show                        | To Test |
| VAT        | FTA VAT 201 PDF export    | PDF downloads with correct fields           | To Test |
| Reports    | Trial Balance             | Debits equal credits                        | To Test |
| Reports    | Corporate Tax worksheet   | 9% CT, AED 375k threshold, SBR note        | To Test |
| Bank       | Create receipt            | Receipt saved and linked                    | To Test |
| Bank       | Bank reconciliation       | Statement lines match ledger entries        | To Test |
| Payroll    | Generate payroll          | Payroll run created                         | To Test |
| Payroll    | WPS SIF export            | SIF file downloads correctly                | To Test |
| Payroll    | EOSB calculator           | Gratuity calculated per UAE Labour Law      | To Test |
| Staff      | Leave calendar            | Monthly grid shows leave entries            | To Test |
| Documents  | Upload document           | Document stored and listed                  | To Test |
| Exceptions | Open Exception Center     | Exceptions listed                           | To Test |
| Audit      | Sensitive action          | Audit log created                           | To Test |

## 5. API Test Architecture

Test all live API endpoints:

```text
/api/v1/auth
/api/v1/companies/current
/api/v1/invoices
/api/v1/documents
/api/v1/source-transactions
/api/v1/accounts
/api/v1/journal
/api/v1/general-ledger
/api/v1/payments
/api/v1/receipts
/api/v1/purchases
/api/v1/items
/api/v1/units
/api/v1/settings
/api/v1/warehouses
/api/v1/inventory/mappings
/api/v1/inventory/stock-levels
/api/v1/tax
/api/v1/payroll
/api/v1/reports
/api/v1/audit/trail
/api/v1/exceptions
/api/v1/events
/api/v1/period-locks
/api/v1/bank-reconciliation/matches
```

For every API, test:

* Authorized request works
* Unauthorized request is blocked
* Wrong tenant data is blocked
* Invalid payload is rejected
* Valid payload is saved
* Delete/update rules are enforced
* Audit log is created where required

## 6. Core Workflow Tests

### 6.1 Sales Invoice Full Chain

```text
Create Invoice
   -> Source Transaction
   -> Validation
   -> Approval
   -> Posting Job
   -> Journal Entry
   -> Tax Lines
   -> Audit Log
   -> Reports Update
```

Expected:

* Invoice is saved
* VAT calculated correctly
* Journal is balanced
* Reports update
* Audit trail exists

### 6.2 Purchase to Stock Flow

```text
Create Purchase
   -> Product Mapping
   -> Stock Movement
   -> Valuation Layer
   -> VAT Input
   -> Accounting Posting
```

Expected:

* Purchase appears in records
* Stock quantity increases
* VAT input is recorded
* Inventory valuation updates

### 6.3 Mark Paid Flow

```text
Sales Invoice
   -> Mark Paid
   -> Receipt Modal
   -> Save Receipt
   -> Invoice Paid
   -> Accounting Entry
```

Expected:

* Receipt must exist before invoice becomes paid
* AR is reduced
* Bank/cash increases
* Audit log created

### 6.4 Product Mapping in Sales

```text
Purchase Record saved with product line
   -> stock_product_mappings row created
   -> Product suggestion in Sales invoice form
   -> Mapped product shows taxflow_name + mapped price
   -> Unmapped product shows original name + purchase price
```

Expected:

* Mapped product: displays taxflow_name, source hint "From mapping"
* Unmapped product: displays original name, source hint "From purchase"

## 7. Performance and Loading Tests

Test:

* Login load time
* Dashboard load time
* Large table loading
* Report generation time
* Stock dashboard loading
* VAT report loading
* API response under multiple users
* Redis report cache behavior (cache hit vs. cold load)
* PostgreSQL query speed with 1,500 tenants

Suggested targets:

| Test              | Target                          |
| ----------------- | ------------------------------- |
| Login             | Under 2 seconds                 |
| Dashboard         | Under 3 seconds                 |
| Module open       | Under 2 seconds                 |
| Reports           | Under 5 seconds                 |
| API response      | Under 500ms for normal requests |
| Heavy report      | Under 10 seconds with cache     |
| Bootstrap hydrate | Under 5 seconds (5,000 records) |

## 8. Security Tests

Required tests:

* User cannot access without login
* User cannot access another company's data
* API rejects arbitrary company_id
* Admin-only functions are protected
* Payroll and accounting functions require permission
* Period lock cannot be bypassed
* Posted journals cannot be deleted
* File upload validation works
* Sensitive actions create audit logs
* Login overlay appears immediately before dashboard loads

## 9. Missing / Not Fully Implemented Features

The following target workflow endpoints are missing or not fully implemented:

```text
POST /api/v1/source-transactions/{id}/submit
POST /api/v1/accounting/journals/{id}/reverse
POST /api/v1/tax/periods/{id}/close
POST /api/v1/einvoicing/generate
POST /api/v1/einvoicing/{id}/validate
POST /api/v1/einvoicing/{id}/transmit
POST /api/v1/einvoicing/{id}/retry
POST /api/v1/wps/generate-sif
POST /api/v1/wps/{id}/validate
POST /api/v1/wps/{id}/mark-uploaded
POST /api/v1/documents/extract
POST /api/v1/documents/{id}/link
```

Key missing or incomplete areas:

* Final production replacement for `/api/v1/app-data?action=save`
* Full eInvoicing generation and transmission
* WPS/SIF validation and upload tracking
* Formal journal reversal flow
* VAT period close/reopen workflow
* Full document extraction and evidence linking
* Full approval workflow for all modules
* Full role/permission enforcement across every API
* Complete audit coverage for all sensitive actions
* Production-level automated test suite
* Purchase dashboard card totals showing correct amounts from DB

## 10. Recommended Test Tools

| Area         | Tool                        |
| ------------ | --------------------------- |
| UI / E2E     | Playwright                  |
| Backend API  | Pytest + FastAPI TestClient |
| Load Testing | k6 or Locust                |
| Security     | OWASP ZAP                   |
| Database     | PostgreSQL constraint tests |
| Queue        | Celery integration tests    |
| Reports      | Snapshot comparison tests   |

## 11. Priority Test Order

```text
1. Login and tenant security
2. Dashboard and module loading
3. Sales invoice workflow
4. Purchase workflow
5. Inventory stock movement
6. Accounting journal balance
7. VAT tax lines and reports
8. Bank receipt/payment flow
9. Audit logs
10. Exception Center
11. Payroll and WPS
12. eInvoicing
13. Performance and load testing
```

## 12. Final Recommendation

Before adding more features, TaxFlow should complete a full regression test cycle for:

```text
Auth
Tenant isolation
Sales
Purchases
Inventory
Accounting
VAT
Reports
Audit
Exceptions
Performance
```

The highest-risk areas are:

```text
Accounting posting
VAT calculation
Inventory valuation
Tenant isolation
Approval workflow
Audit logging
Period locking
```

A feature should only be marked production-ready when:

* UI works
* API works
* Database record is correct
* Audit log exists
* Tenant isolation is verified
* Reports update correctly
* Error handling is tested
* Permission rules are enforced
