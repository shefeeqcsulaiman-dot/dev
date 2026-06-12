# TaxFlow UAE Full Application Architecture

TaxFlow is a UAE business management platform for sales, purchases, accounting, tax, eInvoicing, payroll, HR, rota planning, documents, reporting, approvals, and audit control.

The system is live in production at `https://e4cs.com` on DigitalOcean App Platform. The frontend is a vanilla JS single-page app served by FastAPI. The backend uses PostgreSQL with SQLAlchemy 2.0 and Redis for report caching. Local development uses SQLite. The production target is a modular, tenant-aware business system where source transactions, tax lines, accounting, audit, and reporting are controlled by backend services.

## 1. Current Structure

```text
frontend/                    React + Vite shell (local dev only)
frontend/index.html          Vite entry document (local dev only)
frontend/src/main.jsx        Login shell, legacy UI mount, backend bridge
frontend/src/api.js          Authenticated API client
frontend/.env.local          Local API base override when backend is not on port 8000
frontend/public/taxflow/     Main TaxFlow UI — served by FastAPI in production
frontend/public/taxflow/index.html       Single-page app HTML
frontend/public/taxflow/src/app.js       All frontend logic (~16,000 lines)
frontend/public/taxflow/src/styles.css   UI styles
backend/                     FastAPI API, SQLAlchemy models, routers, storage
backend/app/main.py          API app, router registration, startup migrations
backend/app/models.py        SQLAlchemy ORM models
backend/app/routers/         Auth, invoices, documents, reports, app data, modules, events, exceptions
backend/app/cache.py         Redis report cache with company-scoped invalidation
backend/scripts/             Local data reset and seed scripts
backend/*.db                 Local SQLite databases for lightweight development
.do/app.yaml                 DigitalOcean App Platform deployment spec
docs/                        Architecture and roadmap
```

Local development note:

```text
backend/.env currently points local SQLite to taxflow-seed50-v2.db.
frontend/.env.local points Vite to http://127.0.0.1:8000/api/v1 for the default local backend.
backend/scripts/add_seed_50_real.py extends the local seed set for realistic module data.
SQLite journaling is disabled in local dev because this Windows workspace denies rollback-journal deletion.
Production uses PostgreSQL with normal transactional durability.
```

Verified local runtime:

```text
Frontend: http://127.0.0.1:5173/
Backend:  http://127.0.0.1:8000
Docs:     http://127.0.0.1:8000/docs
Login:    admin@taxflowapp.com / admin123
```

Production:

```text
Live URL:         https://e4cs.com
App name:         etaxflow
Platform:         DigitalOcean App Platform (nyc3)
Instances:        2–6 × professional-s (2 vCPU / 2 GB), autoscales at 70% CPU
Database:         DigitalOcean Managed PostgreSQL (nyc3)
Cache:            Redis (managed)
Worker:           Celery (1 × basic-s)
Deploy trigger:   Push to main branch → auto deploy
DB pool:          pool_size=10, max_overflow=15 per instance
Bootstrap cap:    5,000 records per collection bootstrap
```

## 1.1 Current Implemented State

```text
Frontend (frontend/public/taxflow/)
|-- Vanilla JS single-page app — no framework, no build step
|-- Login overlay shown immediately on page load before dashboard renders
|-- Bootstrap hydration: loads all app_data_records on login, stores in localStorage
|-- Cache-busting: ?v=YYYYMMDD[letter] on CSS/JS assets + _SNAP_VER in localStorage
|-- Sales & Invoices
|   |-- KPI cards (Total, Outstanding, Paid, Overdue)
|   |-- Two-column create form with live summary sidebar and PDF preview
|   |-- Invoice view modal: Print PDF, Online View, Edit, Mark Paid, Design, Share
|   |-- Mark Paid → Customer Receipt modal pre-filled with invoice details
|   |-- Share: Email, WhatsApp, Download PDF, Online View
|   |-- Invoice design settings (logo, color, layout) connected to view/print/download
|   |-- Row actions: View, Download, Share, Edit, Mark Paid, Delete
|   |-- Product suggestion: mapped products show taxflow_name + mapped price
|-- Purchases
|   |-- AI upload, extraction, validation, Save All
|   |-- Manual purchase entry with line items, tax, payment
|   |-- Vendor Bills table (bill_no, vendor, date, due, subtotal, VAT, total, status)
|   |-- Purchase dashboard card showing totals, paid/pending counts
|-- Inventory
|   |-- Item master (code, name, type, category, unit, VAT, cost, price, status)
|   |-- Duplicate detection on add: blocks same item code or item name
|   |-- Stock level table from purchase movements
|   |-- Stock mapping: source product → taxflow name, cost, markup, price
|   |-- Mapped products show taxflow_name in sales suggestions
|-- Staff
|   |-- Employee directory with WPS SIF export
|   |-- EOSB / Gratuity calculator (UAE Labour Law formula)
|   |-- Leave calendar (monthly grid view)
|   |-- Payroll runs
|-- Bank & Payments
|   |-- Bank accounts, receipts, payments
|   |-- Bank reconciliation: match statement lines to ledger entries
|-- Accounting
|   |-- Chart of accounts, journal entries, general ledger
|   |-- Period locking (lock fiscal months, block backdated entries)
|-- Reports
|   |-- VAT 201 report + FTA VAT 201 PDF export button
|   |-- Corporate Tax worksheet (9% UAE CT, AED 375,000 threshold, SBR)
|   |-- P&L, Trial Balance, Balance Sheet
|-- Settings
|   |-- Company registration, tax settings, invoice design
|   |-- Sales categories, units of measure
|   |-- Notification alert rules panel
|-- CSV export on Customer, Vendor, Employee, General Ledger tables
|-- Exception Center screen
|-- Dashboard: revenue, VAT, invoices, purchases, staff KPI cards
`-- Sidebar module badges from live dashboard API counts

Backend (backend/app/)
|-- Auth and company context (JWT, token expiry 1440 min)
|-- app_data_records: single table storing all module JSON as payload column
|-- Reports: dashboard, purchase summary, P&L, trial balance, VAT, debug endpoints
|-- Cache: Redis-backed report cache (app/cache.py)
|   |-- invalidate_company(company_id) clears all report keys for a tenant
|   `-- Triggered on save/delete when collection is in _REPORT_AFFECTING_COLLECTIONS
|-- DB indexes: composite (company_id, collection) and (company_id, collection, created_at)
|-- Invoices, source transactions, posting jobs, journal entries
|-- Tax codes, tax lines, VAT returns, corporate tax returns
|-- Inventory: warehouses, mappings, stock movements, valuation layers, adjustment approvals
|-- Payroll: employees, payroll runs, WPS batches, SIF export
|-- Audit logs, domain events, event outbox, exception events
|-- Reporting snapshot tables
|-- Period locks (accounting, VAT, payroll, inventory)
`-- Prototype app-data API retained as compatibility bridge
```

## 1.2 Runtime Architecture

### Local Development

```text
Browser
  |
  v
React + Vite dev server  http://127.0.0.1:5173
  |
  | loads Vite shell (frontend/index.html, main.jsx, api.js)
  | mounts TaxFlow UI from frontend/public/taxflow/
  |
  v
FastAPI backend  http://127.0.0.1:8000/api/v1
  |
  v
SQLite  backend/taxflow-seed50-v2.db
```

Local responsibilities:

```text
Vite shell     — login, token storage, /auth/me check, sets TAXFLOW_API_BASE_URL
TaxFlow UI     — all screens; reads TAXFLOW_API_BASE_URL, falls back to port 8000
FastAPI        — auth, tenant context, module APIs, app-data bridge
```

Do not replace `frontend/index.html` with the TaxFlow HTML. The Vite shell must stay at root.

### Production (https://e4cs.com)

```text
Browser
  |
  v
DigitalOcean App Platform (nyc3)
  2–6 × professional-s instances  (autoscale at 70% CPU)
  FastAPI served by uvicorn (4 workers per instance)
  Static files: frontend/public/taxflow/ served at /taxflow/
  |
  v
DigitalOcean Managed PostgreSQL (nyc3)
  Connection pool: pool_size=10, max_overflow=15 per instance
  Indexes: (company_id, collection), (company_id, collection, created_at)
  |
  v
DigitalOcean Managed Redis
  Report cache: keys taxflow:report:{company_id}:*
  Invalidated on any write to report-affecting collections
```

Seeded local data (`taxflow-seed50-v2.db`, realistic UAE business records):

```text
app_data_records (customers)        21  — UAE company names, TRNs, Emirates
app_data_records (employees)        21  — designation, department, salary
app_data_records (salesInvoices)    21  — line items, VAT, status
app_data_records (products)         31  — SKU, price, VAT rate
app_data_records (suppliers)        20  — TRNs, contact details
app_data_records (purchaseInvoices) 20
app_data_records (purchaseDocuments) 73
app_data_records (salesCategories)  100
app_data_records (salesUnits)       101
employees (ORM)                     21  — mirrored from app_data_records
invoices (ORM)                      20+
source_transactions                 27
payroll_runs                         2
payroll_items                       21
payments / receipts (ORM)            6 each
exception_events                     7
bank_statement_lines                10+
stock_movements                     10+
```

Seed script: `backend/seed_test_data.py` — idempotent, safe to re-run.

## 2. Revised High-Level Architecture

```text
Frontend Web / Mobile
        |
        v
API Gateway
        |
        v
Auth + Tenant Context
        |
        v
Business Modules
        |
        v
Source Transaction Layer
        |
        v
Validation Engine
        |
        v
Approval Engine
        |
        v
Posting Queue
        |
        v
Accounting Posting Engine
        |
        v
General Ledger + Tax Lines
        |
        v
Reports
        |
        v
Audit + Documents + Notifications
```

Core rule:

```text
UI never posts directly to ledger.
Every module creates a source transaction first.
```

## 2.1 System Build Guidance

The architecture is intentionally broad, but the MVP must stay focused. Do not build all modules first.

MVP scope:

```text
Auth + Tenant
Customers / Suppliers / Items
Sales
Purchases
Accounting Posting Engine
Tax Lines / VAT
Documents
Basic Reports
```

Deferred until core accounting is stable:

```text
HR
Payroll
Rota
WPS
eInvoicing
Advanced mobile app
Advanced automation
```

Most important delivery rule:

```text
Finish accounting + VAT + inventory properly before adding payroll, rota, WPS, and eInvoicing.
```

Recommended production stack:

```text
Frontend: React + Vite
Backend: Python FastAPI
Database: PostgreSQL
Queue: Redis + Celery
Storage: AWS S3
Mobile: Flutter later
```

Production API rule:

```text
Do not use prototype catch-all APIs such as /api/v1/app-data?action=save as the final production write path.
The live dev app keeps app-data as a compatibility bridge while proper module APIs are added module by module.
```

Current module APIs already added:

```text
/api/v1/auth
/api/v1/companies/current
/api/v1/invoices
/api/v1/documents
/api/v1/jobs
/api/v1/source-transactions
/api/v1/accounts
/api/v1/journal
/api/v1/voucher-types
/api/v1/vouchers
/api/v1/general-ledger
/api/v1/posting-jobs
/api/v1/payments
/api/v1/receipts
/api/v1/bank-accounts
/api/v1/bank-statement-lines
/api/v1/bank-reconciliation/matches
/api/v1/period-locks
/api/v1/tax
/api/v1/purchases
/api/v1/items
/api/v1/units
/api/v1/settings
/api/v1/warehouses
/api/v1/inventory/mappings
/api/v1/exceptions
/api/v1/events
/api/v1/payroll
/api/v1/reports
/api/v1/audit/trail
/api/v1/corporate-accounting
/api/v1/item-units
/api/v1/item-unit-conversions
/api/v1/inventory/stock-levels
/api/v1/inventory/valuation-layers
/api/v1/inventory/adjustment-approvals
```

## 3. Main Modules

```text
TaxFlow
|-- Sales & Invoices
|-- eInvoicing
|-- Purchases
|-- Bills & Vendors
|-- Payments
|-- Bank Accounts
|-- Accounting
|-- Tax Engine / VAT
|-- Inventory
|-- Staff / HR
|-- Rota Planning
|-- Payroll
|-- WPS / SIF
|-- Reports
|-- Documents & Evidence
|-- Notifications
|-- Expert Review
|-- Mobile App
|-- AI Assistant
`-- Settings
```

## 4. Source Transaction Layer

Before validation, approval, tax, or posting, each business action creates a source record.

```text
Sales Invoice
Purchase Invoice
Expense
Payroll Run
Inventory Movement
Receipt
Payment
Rota Publish
Attendance Correction
        |
        v
Source Transaction
        |
        v
Validation
        |
        v
Approval
        |
        v
Posting Queue
        |
        v
Journal Entry / Tax Lines / Audit
```

Benefits:

- Prevents direct UI-to-ledger mistakes.
- Preserves the original business event.
- Supports approval before posting.
- Makes retry and reversal safer.
- Keeps audit trail clear.

Recommended source transaction tables:

```text
source_transactions
source_transaction_lines
source_transaction_status_history
source_transaction_links
```

Important fields:

```text
id
company_id
branch_id
source_module
source_type
source_id
transaction_date
amount
tax_amount
currency
status
validation_status
approval_status
posting_status
created_by
created_at
updated_by
updated_at
```

## 5. Accounting Core

Accounting is the financial center of the system.

```text
Approved Source Transaction
        |
        v
Posting Job
        |
        v
Accounting Posting Engine
        |
        v
Balanced Journal Entry
        |
        v
General Ledger
        |
        v
Financial Reports
```

Rules:

- Every posted business transaction must have journal impact.
- Debit must always equal credit.
- Posted journals cannot be deleted.
- Corrections use reversal journals.
- Reports read from the general ledger.
- Sub-ledgers must reconcile to GL control accounts.
- Every posting must store source module and source ID.

## 6. Posting Queue and Retry System

Accounting posting should be asynchronous and reliable.

```text
Approved Transaction
        |
        v
Posting Job
        |
        v
Queue Worker
        |
        v
Journal Created
        |
        v
Success Log / Failed Log
```

Required tables:

```text
posting_jobs
posting_errors
posting_retry_logs
auto_posting_logs
accounting_posting_rules
```

Posting job statuses:

```text
Pending
Processing
Posted
Failed
Retrying
Cancelled
```

Critical controls:

- Failed postings appear in an error log.
- Admin/accountant can retry failed postings.
- Duplicate posting prevention must use idempotency keys.
- Posting worker must lock the source transaction during processing.
- Journal creation and tax line creation must be atomic.

## 7. Tax Engine / VAT Architecture

VAT must be tax-line based, not only invoice-level.

Tax engine tables:

```text
tax_codes
tax_lines
tax_periods
vat_returns
tax_adjustments
tax_evidence_links
```

Tax code examples:

```text
VAT_OUTPUT_5
VAT_INPUT_5
ZERO_RATED
EXEMPT
OUT_OF_SCOPE
REVERSE_CHARGE_OUTPUT
REVERSE_CHARGE_INPUT
```

Tax line fields:

```text
id
company_id
source_module
source_id
source_line_id
tax_code_id
taxable_amount
tax_rate
tax_amount
recoverable_amount
non_recoverable_amount
tax_period_id
evidence_document_id
status
```

VAT flow:

```text
Source Transaction Line
        |
        v
Tax Code
        |
        v
Tax Line
        |
        v
VAT Period
        |
        v
VAT Return
```

VAT report sources:

```text
VAT Output  -> tax_lines
VAT Input   -> tax_lines
VAT Return  -> vat_returns + tax_adjustments
Evidence    -> documents + document_links
```

## 8. eInvoicing Module

Invoice PDF is not eInvoicing. UAE eInvoicing requires structured invoice data exchanged and reported electronically through the required model and providers. Unstructured PDFs, Word files, images, scans, and emails are not eInvoices.

```text
eInvoicing
|-- eInvoice Generator
|-- UAE Schema Validation
|-- Mandatory Field Validation
|-- ASP / Peppol Connector
|-- Transmission Log
|-- Message Level Status
|-- Error Retry
|-- eInvoice Archive
`-- eInvoice Audit Trail
```

eInvoice flow:

```text
Sales Invoice Source Record
        |
        v
Structured eInvoice Payload
        |
        v
UAE Schema / Mandatory Field Validation
        |
        v
ASP Connector
        |
        v
Transmit / Report
        |
        v
Success, Rejected, Retry, Archived
```

Recommended tables:

```text
einvoice_profiles
einvoice_payloads
einvoice_validations
einvoice_transmissions
einvoice_status_logs
einvoice_archives
einvoice_retry_logs
```

Important distinction:

```text
Invoice PDF       = human-readable document
Structured eInvoice = machine-readable regulated invoice data
```

## 9. Sales & Invoices

Responsibilities:

- Customers
- Invoice creation
- Sales invoice upload
- AI extraction
- AI extraction validation
- Duplicate detection against saved database invoices
- Duplicate detection inside the current AI upload batch
- Selective save from AI extraction into the invoice register
- PDF preview/export
- Email and WhatsApp sharing
- eInvoice generation handoff
- Customer ledger update
- Tax line creation
- Accounting source transaction

Sales invoice posting:

```text
Dr Accounts Receivable
    Cr Sales Income
    Cr VAT Payable
```

Required source records:

```text
sales_invoices
sales_invoice_lines
sales_invoice_tax_lines
invoice_share_logs
source_transactions
```

Current UI flow:

```text
Upload Invoices
        |
        v
AI Extraction
        |
        v
Validation
        |
        v
Invoices (KPI cards: Total / Outstanding / Paid / Overdue)
        |
        v
Create Invoice (two-column form + live summary sidebar)
        |
        v
View Invoice modal
        |-- Print PDF (popup with Google Fonts + light-mode CSS)
        |-- Online View (digital invoice URL with QR)
        |-- Edit (load back into create form)
        |-- Mark Paid (redirect to Customer Receipt pre-filled)
        |-- Design (shortcut to Invoice Design settings)
        `-- Share (Email / WhatsApp / Download PDF / Online View)
```

Invoice create form sidebar:

```text
Live Summary card (updates on every field/line change)
|-- Invoice No., Customer, Date, Due
|-- Subtotal, VAT 5%, Total Due
`-- Full PDF Preview button

Compliance tips
Customize Invoice Design shortcut
```

Mark Paid → Receipt redirect flow:

```text
Mark Paid clicked on invoice row or view modal
        |
        v
Navigate to Bank & Payments > Receipts tab
        |
        v
Open Customer Receipt modal with:
|-- Client = invoice customer
|-- Amount = invoice total
|-- Reference note = "Payment for INV-XXXXX"
|-- Comments = "Receipt for Invoice INV-XXXXX — Customer Name"
`-- Allocation table = one pre-checked row for this invoice
        |
        v
User confirms and saves receipt
        |
        v
Invoice status updated to Paid
```

Invoice design settings connection:

```text
Settings > Invoice Design Layout
        |
        v (getInvoiceLayout() reads #inv-layout-* fields)
        v
renderSalesInvoicePreview(inv) — modal view
        |
        v (innerHTML copied, wrapped with styles.css + Google Fonts + theme-light)
        v
Print/Download popup — same design, light mode, correct fonts
        |
        v
publicInvoiceUrl(inv) — Online View / QR code
```

Sales AI upload table behavior:

```text
Extracted row
        |
        v
Client validation
        |
        v
Valid / Review
        |
        v
Selected valid rows -> Save All -> sales invoice register + backend app data
```

Sales AI validation checks:

- Invoice number is required.
- Invoice number must not already exist in the database-backed invoice index.
- Invoice number must not be duplicated in the current AI upload batch.
- Customer is required.
- Date is required.
- Customer TRN must be 15 digits when present.
- Total must match subtotal plus VAT.
- Low-confidence extraction is held for review.

Sales invoice rows must show source:

```text
AI Upload
Manual
```

Sales invoice row actions:

```text
View
Share
Edit
Delete with dependency checks
```

## 10. Purchases, Bills, and Vendors

Responsibilities:

- Supplier records
- Purchase invoices
- Purchase orders
- Vendor bills
- OCR/document extraction
- Manual purchase entry
- Supplier TRN validation
- VAT evidence control
- AP sub-ledger

Purchase capture paths:

```text
Upload Documents -> AI Extraction -> Validation -> Purchase Records
Manual entry     -> item lines + tax + payment -> Purchase Records
```

Purchase AI upload table behavior mirrors Sales:

```text
Extracted row
        |
        v
Client validation
        |
        v
Valid / Review
        |
        v
Selected valid rows -> Save All -> purchase records + backend source transaction sync
```

Purchase AI validation checks:

- Invoice number is required.
- Invoice number must not already exist in Purchase Records.
- Invoice number must not be duplicated in the current AI upload batch.
- Supplier is required.
- Date is required.
- Supplier TRN must be 15 digits when present.
- Total must match subtotal plus VAT.
- Backend extraction error rows are held for review.
- Low-confidence extraction is held for review.

Manual purchase entry stores:

```text
Supplier
Reference No.
Purchase Date
Purchase Status
Business Location
Pay Term
Attached Document
Product Lines
Discount
Purchase Tax
Additional Notes
Shipping Details
Additional Shipping Charges
Payment Amount
Paid On
Payment Method
Payment Account
Payment Note
```

Manual purchase quantity rule:

```text
Purchase Quantity is the purchased item count.
Total Items = sum of purchase line quantities, not number of product rows.
Example: one purchase line with quantity 10 stores Items = 10 and creates stock quantity 10.
```

Purchase Settings owns shared purchase/sales item masters:

```text
Category Setup -> category, usage scope, default VAT
Unit Setup     -> unit code, unit name, type, decimal places
```

Purchase records should expose both extracted and manual purchases in one table:

```text
Ref No.
Product Name
Supplier
Date
Location
Items
Net Amount
Tax
Shipping
Total
Paid
Due
Source
Status
```

Purchase posting:

```text
Purchase Record Save
        |
        v
Source Transaction Sync
        |
        v
Stock Product Mapping lookup/create
        |
        v
stock_movements purchase quantity
        |
        v
inventory_valuation_layers receipt quantity
        |
        v
Current Stock Levels from DB totals
```

When a purchase record is saved through the development `/api/v1/app-data?action=save` compatibility bridge, the backend also syncs the purchase lines into stock tables. For each valid line:

- `quantity`, `qty`, `purchase_qty`, or `qty_invoiced` is read as the stock quantity.
- A `stock_product_mappings` row is found by SKU/code or product name, or created if missing.
- Existing purchase movements for the same reference are replaced before inserting new rows, so edits update stock instead of duplicating it.
- A `stock_movements` row is written with `movement_type = purchase`.
- An `inventory_valuation_layers` row is written with `quantity_in` and `quantity_remaining` equal to the purchase quantity.
- Deleting the purchase record removes the linked source transaction lines, stock movements, and valuation layers for that reference.

```text
Dr Purchase / Inventory / Expense
Dr VAT Input
    Cr Accounts Payable
```

## 11. Document Evidence Control

VAT and audit claims must be linked to evidence.

```text
Purchase VAT Claim
        |
        v
Supplier Invoice Document
        |
        v
TRN Validation
        |
        v
Tax Line
        |
        v
VAT Report
```

Required tables:

```text
documents
document_links
document_extractions
document_audit_logs
document_validation_results
```

Document links should connect files to:

- Sales invoices
- Purchase invoices
- Bills
- Payments
- Receipts
- Payroll runs
- WPS/SIF exports
- Journal entries
- VAT returns
- Bank reconciliations

## 12. Payments and Receipts

Receipts:

```text
Customer Receipt
Cash Receipt
Bank Receipt
Online Payment
Card / Tabby / Payment Link
```

Payments:

```text
Supplier Payment
Expense Payment
Payroll Payment
Cash Payment
Bank Payment
```

Customer receipt posting:

```text
Dr Bank / Cash
    Cr Accounts Receivable
```

Supplier payment posting:

```text
Dr Accounts Payable
    Cr Bank / Cash
```

Invoice-to-receipt shortcut:

```text
Mark Paid on any sales invoice
        |
        v
openReceiptForInvoice(inv)
        |
        v
Customer Receipt modal pre-filled:
|-- Client dropdown set to invoice customer
|-- Amount set to invoice total
|-- Reference note: "Payment for INV-XXXXX"
|-- Comments: "Receipt for Invoice INV-XXXXX — Customer Name"
`-- Allocation row: invoice_no, date, amount (pre-checked)
        |
        v
User saves receipt → markPaymentDocumentPaid() → invoice row status → Paid
```

This enforces proper accounting: a payment receipt must exist before an invoice is marked paid. Invoices are not silently flipped to Paid status without a receipt record.

## 12.1 Sidebar Module Counters

The sidebar badge for each nav item comes from `module_counts` in `GET /api/v1/reports/dashboard`. Each badge shows the count most relevant to that module only.

```text
Nav Item          Badge Value
─────────────────────────────────────────────────────────
Sales & Invoices  invoice_count (sales invoices only)
Quotations        quotation_count (warn color)
Purchases         purchase_record_count
                  + purchase_invoice_count (purchaseInvoices + purchaseDocuments)
                  + bill_count (warn color)
Inventory         inventory_mapping_count
Bank & Payments   account_count + payment_count + receipt_count
Accounting        journal_count
Reports           tax_line_count
Staff             employee_count (warn color, ORM table only — not doubled)
Payroll           payroll_run_count
Notifications     job_count
Expert Review     audit_count (red badge when > 0)
Exception Center  exception_count (warn color, set from dashboard on startup)
Settings          sales_category_count + sales_unit_count + account_count
```

Rules:
- Each nav item gets exactly one `setNavBadge` call with its own data key.
- `staff` uses `Employee` ORM table count only — `app_data_records` employees collection mirrors the same records and must not be added.
- `rota` has no badge (no rota schedule records in database yet).
- `exception` badge is also updated live when the Exception Center page is visited via `loadExceptionCenter()`.
- Dead badge calls (`bills`, `payments`, `documents`) were removed — no matching nav items exist.

Backend additions required for correct counts:

```text
exception_count       — count(ExceptionEvent, company_id)
receipt_count         — count(Receipt, company_id)
payment_receipt_count — count(Payment, company_id) + count(Receipt, company_id)
purchase_invoice_count — app_counts["purchaseInvoices"] + app_counts["purchaseDocuments"]
```

## 13. Bank Reconciliation

```text
Bank Statement Upload / Bank Feed
        |
        v
Normalize Transactions
        |
        v
Match by Amount, Date, Reference, Party
        |
        v
Matched / Suggested / Unmatched
        |
        v
User Confirmation
        |
        v
Reconciliation Report
```

Match confidence:

```text
100% = exact amount + reference
80%  = same amount + close date
50%  = same amount only
```

## 14. Stock / Inventory Mapping Architecture

Stock mapping connects:

```text
Item -> Warehouse -> Purchase / Sales -> Stock Movement -> Inventory Value -> Accounting Posting
```

Main purchase flow:

```text
Purchase Invoice / Stock Receipt
        |
        v
Item Mapping
        |
        v
Warehouse Mapping
        |
        v
Stock Movement
        |
        v
Inventory Valuation
        |
        v
Accounting Entry
```

Main sales flow:

```text
Sales Invoice / Delivery
        |
        v
Item Mapping
        |
        v
Warehouse Mapping
        |
        v
Stock Deduction
        |
        v
COGS Calculation
        |
        v
Accounting Entry
```

Item master must include stock and accounting settings:

```text
Item Code
Item Name
Item Type
Category
Unit
Sales Account
Purchase Account
Inventory Account
COGS Account
VAT Code
Default Warehouse
Reorder Level
Opening Stock
Opening Value
Stock Tracking
```

Item type behavior:

| Item Type | Stock Impact | Accounting Impact |
| --- | --- | --- |
| Stock Item | Yes | Inventory + COGS |
| Service Item | No | Income / Expense only |
| Consumable | Optional | Expense or Inventory |
| Fixed Asset | No stock | Fixed Asset Account |
| Raw Material | Yes | Inventory |
| Finished Goods | Yes | Inventory + COGS |

Required mapping tables:

```text
stock_product_mappings
id
company_id
source_product_name
generated_product_name
warehouse_id
unit_id
mapping_status
unit_cost
markup_percent
fixed_price_enabled
unit_price
vat_rate_id
price_including_vat
service_fees_enabled
created_by
created_at
updated_by
updated_at

item_account_mappings
id
company_id
item_id
sales_account_id
purchase_account_id
inventory_account_id
cogs_account_id
vat_code_id
default_warehouse_id
valuation_method
status

warehouses
id
company_id
warehouse_code
warehouse_name
branch_id
location
stock_account_id
status

stock_movements
id
company_id
mapping_id
warehouse_id
movement_type
quantity
unit_cost
reference
created_at
updated_at

units
id
unit_name
unit_code
base_unit
conversion_factor
status

stock_reservations
id
company_id
item_id
warehouse_id
source_module
source_id
reserved_qty
status

item_units
id
company_id
item_id
unit_id
conversion_factor
is_base_unit
purchase_default
sales_default
status

item_unit_conversions
id
company_id
item_id
from_unit_id
to_unit_id
conversion_factor
status

purchase_invoice_lines
qty
unit_id
conversion_factor
base_qty
base_unit_id

inventory_valuation_layers
id
company_id
item_code
warehouse_id
source_module
source_id
quantity_in
quantity_remaining
unit_cost
valuation_method
created_at
updated_at

stock_adjustment_approvals
id
company_id
stock_adjustment_id
requested_by
approved_by
reason
old_qty
new_qty
status
approved_at
```

`item_units` is required for box, packet, carton, and piece conversions. `item_unit_conversions` stores explicit conversions such as BOX to PCS = 12 and PACK to PCS = 6. `inventory_valuation_layers` supports FIFO and weighted average costing. `stock_adjustment_approvals` controls manual stock changes before they affect inventory value.

Multi-unit inventory rule:

```text
Purchase unit
        ↓
Convert to base stock unit
        ↓
Store stock movement in base unit only
        ↓
Sell in any allowed unit
        ↓
Convert again internally before costing/posting
```

Every item must have one base stock unit. Example:

```text
Item: Coca Cola Can
Base Unit: PCS

Allowed units:
PCS  = 1 PCS
PACK = 6 PCS
BOX  = 12 PCS
```

Purchase example:

```text
Purchase line: 1 BOX @ AED 120
Conversion: 1 BOX × 12 = 12 PCS
Stock movement: qty_in = 12 PCS
Unit cost: AED 10 per PCS

Dr Inventory 120
    Cr Accounts Payable 120
```

Sales example:

```text
Sell 2 PCS
Stock deduction: 2 PCS
Remaining from 12 PCS = 10 PCS
COGS: 2 × AED 10 = AED 20

Dr Accounts Receivable
    Cr Sales

Dr Cost of Goods Sold 20
    Cr Inventory 20
```

Stock should never store mixed units. Purchase and sales lines may display BOX, PACK, PCS, litre, ml, kg, or gram, but `stock_movements`, valuation layers, and COGS calculations must store and cost the base unit.

Stock mapping screen behavior:

```text
Complete Stock Mapping List
        |
        v
Search by Product Name or Generated Name
        |
        v
Sort by Product Name, Generated Name, or Status
        |
        v
Map selected row
        |
        v
Edit Product Name + Generated Name + Pricing
        |
        v
Save Mapping
```

The mapping list columns are:

```text
Product Name
Generated Name
Warehouse
Unit
Status
Action
```

The mapping panel fields are:

```text
Product Name
Generated Name
Cost
Markup (%)
Fixed
Price
Tax Rate
Inc. VAT
Service Fees
```

Generated name logic:

```text
Product Name from upload / purchase / sales document
        |
        v
Normalize spelling, spacing, and common unit words
        |
        v
Generated Name
        |
        v
User review and save
```

Example:

```text
Product Name: Industrial Oil 5 Litre
Generated Name: Industrial Oil 5L
```

The mapping module intentionally does not require a separate TaxFlow Name field. The generated name is the clean internal stock-mapping name used for matching and review.

Pricing calculation:

```text
If Fixed = No:
Price = Cost x (1 + Markup %)

If Fixed = Yes:
Price is entered manually

Inc. VAT = Price x (1 + Tax Rate %)
```

Example:

```text
Cost: 25.00
Markup: 56%
Fixed: No
Price: 39.00
Tax Rate: VAT 5%
Inc. VAT: 40.95
Service Fees: No
```

Mapping statuses:

| Status | Meaning |
| --- | --- |
| Mapped | Product name and generated name are confirmed |
| Review | Suggested/generated name needs user confirmation |
| Unmapped | No saved mapping exists |

Validation for mapping save:

- Product Name is required.
- Generated Name is required.
- Cost, markup, price, and VAT values must be numeric when entered.
- Inc. VAT should be recalculated after cost, markup, fixed price, price, or tax rate changes.
- Saved mapping changes status to Mapped.
- Search and sort must operate on the current visible mapping list.

Movement types:

| Movement Type | Source | Qty Impact |
| --- | --- | --- |
| Purchase Receipt | Purchase | Qty In |
| Sales Delivery | Sales | Qty Out |
| Sales Return | Sales Return | Qty In |
| Purchase Return | Purchase Return | Qty Out |
| Stock Adjustment In | Manual | Qty In |
| Stock Adjustment Out | Manual | Qty Out |
| Transfer Out | Warehouse Transfer | Qty Out |
| Transfer In | Warehouse Transfer | Qty In |
| Opening Stock | Setup | Qty In |

Purchase stock accounting:

```text
Dr Inventory
Dr VAT Input
    Cr Accounts Payable
```

Sales stock accounting:

```text
Dr Accounts Receivable
    Cr Sales Income
    Cr VAT Payable

Dr Cost of Goods Sold
    Cr Inventory
```

Stock valuation methods:

```text
Weighted Average for MVP
FIFO as advanced option
Standard Cost as controlled option
```

Unit conversion flow:

```text
Purchase in supplier unit
        |
        v
Convert to base stock unit
        |
        v
Store stock in base unit
        |
        v
Convert for invoice display when sold
```

Opening stock accounting:

```text
Dr Inventory
    Cr Opening Balance Equity
```

Stock adjustment accounting:

```text
Increase:
Dr Inventory
    Cr Stock Adjustment Gain

Decrease:
Dr Stock Adjustment Loss
    Cr Inventory
```

Warehouse transfers create Transfer Out and Transfer In movements. If the source and destination warehouses use different inventory accounts, post:

```text
Dr Destination Inventory Account
    Cr Source Inventory Account
```

Availability:

```text
Available Stock = On Hand Stock - Reserved Stock
```

Current stock dashboard:

```text
GET /api/v1/inventory/stock-levels
        |
        v
Backfill missing purchase stock movements from purchaseRecords when needed
        |
        v
SUM(stock_movements.quantity) grouped by stock_product_mappings.id
        |
        v
Inventory -> Stock Dashboard -> Current Stock
```

The frontend may temporarily calculate stock from the local purchase cache for immediate feedback after saving a purchase, but the authoritative stock table is the database total returned by `/api/v1/inventory/stock-levels`.

Reorder alert:

```text
Available Qty <= Reorder Level
```

Critical validation rules:

- Cannot sell more than available stock unless negative stock is enabled in settings.
- Cannot post stock invoice without item mapping.
- Stock tracked items must have inventory account.
- COGS account is required for sales.
- Warehouse is required for stock items.
- Unit conversion is required before stock update.
- Stock adjustment requires approval.
- Posted stock movement cannot be deleted.
- Corrections must use reverse movement.

Reports must use stock movement, valuation, item, warehouse, reservation, and journal tables, not dashboard summaries.

## 15. HR and Attendance

Responsibilities:

- Employee master data
- Attendance check-in/check-out
- Attendance corrections
- Leave
- Overtime
- Biometric integration
- HR reports

Attendance flow:

```text
Published Rota
        |
        v
Check-in / Check-out
        |
        v
Compare Actual vs Planned
        |
        v
Late / Early Exit / Hours / Overtime
        |
        v
Approval
        |
        v
Payroll
```

## 16. Rota Planning

Rota is a separate module.

```text
Rota Planning
|-- Shift Setup
|-- Weekly Rota
|-- Monthly Rota
|-- Department Coverage
|-- Shift Swap Requests
|-- Rota Approval
|-- Rota Publishing
`-- Rota Reports
```

Rota flow:

```text
Create shifts
        |
        v
Assign department requirements
        |
        v
Build weekly/monthly rota
        |
        v
Check leave, conflicts, coverage
        |
        v
Supervisor review
        |
        v
HR publish
        |
        v
Employee notification
        |
        v
Attendance uses published rota
```

## 17. Payroll and WPS / SIF

Payroll responsibilities:

- Salary register
- Attendance and overtime integration
- Deductions
- Benefits and EOS/gratuity
- Payroll approval
- Payslips
- Accounting posting

Payroll accrual:

```text
Dr Salary Expense
    Cr Salary Payable
```

Salary payment:

```text
Dr Salary Payable
    Cr Bank
```

WPS / SIF module:

```text
WPS / SIF
|-- Salary File Generation
|-- Employee Bank Validation
|-- Payroll Approval
|-- SIF Export
|-- Bank / WPS Agent Upload Tracking
|-- Rejection Handling
`-- WPS Audit Trail
```

Recommended WPS/SIF tables:

```text
wps_batches
wps_sif_files
wps_employee_lines
wps_validation_errors
wps_upload_logs
wps_rejection_logs
```

## 18. Reporting Architecture

Reports must use authoritative sources:

```text
Financial Reports  -> General Ledger
VAT Reports        -> Tax Lines + VAT Returns
Payroll Reports    -> Approved Payroll Runs
Inventory Reports  -> Stock Movements + Valuation
HR Reports         -> Attendance / Rota / Leave
Rota Reports       -> Rota Assignments + Coverage
Document Reports   -> Documents + Evidence Links
```

Do not generate production reports from dashboard totals.

Core reports:

- Trial Balance
- Profit & Loss
- Balance Sheet
- General Ledger
- VAT Report
- Cash Flow
- Customer Aging
- Supplier Aging
- Payroll Posting Report
- WPS/SIF Status Report
- Inventory Valuation
- Rota Coverage
- Daily Attendance
- Audit Trail

Reporting snapshots:

```text
daily_gl_balances
inventory_balance_snapshots
customer_aging_snapshots
vat_return_snapshots
```

Use snapshots for expensive recurring reports and period-end views. Do not calculate every report live from raw transaction tables when the same summary is repeatedly requested. Snapshots must keep source period, generated time, source version, and tenant scope so users can trace back to the authoritative records.

## 19. Audit Architecture

Audit logs must be deep enough to reconstruct who changed what, when, why, and from where.

Required audit fields:

```text
id
company_id
branch_id
user_id
module
record_type
record_id
action_type
old_value
new_value
reason
ip_address
device
user_agent
timestamp
correlation_id
```

High-risk actions:

- Journal posting
- Journal reversal
- Payroll approval
- WPS/SIF export
- VAT setting change
- VAT return submission
- User permission change
- Attendance correction
- Rota change after publish
- Period lock/reopen
- eInvoice retry/cancellation

Sensitive actions must record:

```text
old value
new value
reason
user
IP address
device
time
correlation ID
```

## 20. Period Locking

Use separate period locks because different business areas close at different times.

```text
Accounting Period Lock
VAT Period Lock
Payroll Period Lock
Inventory Period Lock
Rota / Attendance Lock
```

Recommended tables:

```text
period_locks
period_lock_history
period_reopen_requests
```

Rules:

- Locked accounting period cannot accept new journals except approved reversals.
- Locked VAT period cannot change tax lines without adjustment.
- Locked payroll period cannot change payroll items.
- Locked inventory period cannot change stock movements.
- Reopening requires admin approval, reason, and audit log.

## 20.1 Exception Center

TaxFlow should have one screen for operational and accounting exceptions.

Exception Center sources:

```text
Failed postings
Duplicate invoices
Unmapped stock items
VAT mismatches
OCR failures
Bank unmatched transactions
Payroll errors
eInvoice failures
```

Exception Center behavior:

```text
Exception raised
        |
        v
Classify by module, severity, tenant, and source record
        |
        v
Assign owner / suggested fix
        |
        v
Resolve, retry, skip, or escalate
        |
        v
Audit resolution
```

Recommended tables:

```text
exceptions
exception_events
exception_assignments
exception_resolution_logs
```

This gives users and admins a single place to fix blocked postings, duplicate uploads, OCR mistakes, VAT issues, stock mapping gaps, and integration failures.

## 20.2 Event System

Use domain events to decouple business actions from side effects.

Example events:

```text
InvoiceApproved
PurchasePosted
PaymentReceived
PayrollApproved
RotaPublished
```

Event consumers trigger:

```text
Accounting posting
Tax line creation
Notifications
Audit logs
Reports refresh
```

Recommended tables:

```text
domain_events
event_outbox
event_consumers
event_processing_logs
```

Use the outbox pattern so database changes and emitted events stay consistent. Queue workers should process events idempotently using event IDs and tenant scope.

## 21. Tenant Isolation

Every business table must include:

```text
company_id
branch_id optional
created_by
created_at
updated_by
updated_at
status
```

Tenant isolation must be enforced on the backend:

- API middleware loads tenant context.
- Queries are always scoped by `company_id`.
- Users cannot supply arbitrary `company_id` to access another tenant.
- Object storage keys include tenant scope.
- Queue workers validate tenant context before processing.
- Reports and exports are tenant-filtered.

## 22. Data Architecture

Recommended database groups:

```text
Identity
|-- users
|-- roles
|-- permissions
|-- role_permissions
`-- user_sessions

Tenant
|-- companies
|-- branches
|-- departments
|-- designations
`-- tenant_settings

Source Transactions
|-- source_transactions
|-- source_transaction_lines
|-- source_transaction_links
`-- source_transaction_status_history

Sales and eInvoicing
|-- customers
|-- products
|-- sales_invoices
|-- sales_invoice_lines
|-- invoice_share_logs
|-- einvoice_payloads
|-- einvoice_transmissions
`-- einvoice_status_logs

Tax
|-- tax_codes
|-- tax_lines
|-- tax_periods
|-- vat_returns
`-- tax_adjustments

Accounting
|-- accounts
|-- journal_entries
|-- journal_entry_lines
|-- posting_jobs
|-- posting_errors
|-- posting_retry_logs
|-- posting_rules
|-- payments
|-- receipts
|-- bank_reconciliations
`-- period_locks

Documents
|-- documents
|-- document_links
|-- document_extractions
`-- document_audit_logs

HR / Rota / Payroll / WPS
|-- employees
|-- attendance
|-- leave_requests
|-- overtime_requests
|-- shifts
|-- rota_periods
|-- rota_assignments
|-- payroll_runs
|-- payroll_items
|-- payslips
|-- wps_batches
`-- wps_sif_files

System
|-- notifications
|-- exception_events
|-- domain_events
|-- event_outbox
|-- event_processing_logs
|-- approval_workflows
|-- approval_actions
|-- audit_logs
|-- audit_log_details
`-- integration_logs
```

Reporting Snapshots
|-- daily_gl_balances
|-- inventory_balance_snapshots
|-- customer_aging_snapshots
`-- vat_return_snapshots

## 23. API Architecture

Recommended API areas:

```text
/api/v1/auth
/api/v1/tenant
/api/v1/settings
/api/v1/source-transactions
/api/v1/sales
/api/v1/einvoicing
/api/v1/purchases
/api/v1/items
/api/v1/units
/api/v1/payments
/api/v1/accounting
/api/v1/tax
/api/v1/bank
/api/v1/inventory
/api/v1/hr
/api/v1/rota
/api/v1/payroll
/api/v1/wps
/api/v1/reports
/api/v1/documents
/api/v1/audit
/api/v1/exceptions
/api/v1/events
```

Production must remove:

```text
/api/v1/app-data?action=save
```

Current development status:

```text
The compatibility /api/v1/app-data bridge still exists for legacy UI tables and demo extraction.
The following proper module APIs are now live and should become the preferred write paths.
```

Live module APIs:

```text
POST     /api/v1/auth/login
GET      /api/v1/auth/me
GET      /api/v1/companies/current
GET/POST /api/v1/invoices
GET/POST /api/v1/documents
GET      /api/v1/jobs
POST     /api/v1/jobs/vat-summary
GET/POST /api/v1/source-transactions
POST     /api/v1/source-transactions/{source_id}/validate
POST     /api/v1/source-transactions/{source_id}/approve
GET/POST /api/v1/accounts
DELETE   /api/v1/accounts/{account_id}
GET/POST /api/v1/journal
GET/POST /api/v1/voucher-types
GET/POST /api/v1/vouchers
POST     /api/v1/vouchers/{voucher_id}/approve
GET      /api/v1/general-ledger
GET      /api/v1/posting-jobs
POST     /api/v1/posting-jobs/{job_id}/retry
GET/POST /api/v1/payments
GET/POST /api/v1/receipts
GET/POST /api/v1/bank-accounts
POST     /api/v1/bank-statement-lines
POST     /api/v1/bank-reconciliation/matches
POST     /api/v1/period-locks
GET/POST /api/v1/tax/codes
GET      /api/v1/tax/lines
GET      /api/v1/tax/vat-return
GET/POST /api/v1/tax/vat-returns
GET/POST /api/v1/tax/corporate-tax-returns
GET/POST /api/v1/purchases
GET/POST /api/v1/items
GET/POST /api/v1/units
GET/POST /api/v1/settings
GET/POST /api/v1/warehouses
GET/POST /api/v1/inventory/mappings
PUT      /api/v1/inventory/mappings/{mapping_id}
DELETE   /api/v1/inventory/mappings/{mapping_id}
DELETE   /api/v1/inventory/stock-levels
GET/POST /api/v1/events
GET/POST /api/v1/exceptions
GET      /api/v1/payroll/employees
GET      /api/v1/payroll/runs
POST     /api/v1/payroll/generate
POST     /api/v1/payroll/runs/{run_id}/wps-batch
GET      /api/v1/reports/dashboard
GET      /api/v1/reports/trial-balance
GET      /api/v1/reports/summary
GET      /api/v1/audit/trail
GET      /api/v1/ai/workbench
POST     /api/v1/ai/assist
POST     /api/v1/ai/validate-transaction
POST     /api/v1/ai/explain-exception
GET      /api/v1/corporate-accounting/summary
GET      /api/v1/corporate-accounting/corporate-tax
GET      /api/v1/corporate-accounting/fixed-assets
GET      /api/v1/corporate-accounting/accruals-prepayments
GET      /api/v1/corporate-accounting/cost-centers
GET      /api/v1/corporate-accounting/budgets
GET      /api/v1/corporate-accounting/cash-flow
GET      /api/v1/corporate-accounting/credit-control
GET      /api/v1/corporate-accounting/month-end
GET      /api/v1/corporate-accounting/consolidation
GET      /api/v1/corporate-accounting/approval-matrix
GET/POST /api/v1/item-units
GET/POST /api/v1/item-unit-conversions
GET      /api/v1/inventory/stock-levels
GET      /api/v1/inventory/valuation-layers
GET/POST /api/v1/inventory/adjustment-approvals
GET      /api/v1/app-data
POST     /api/v1/app-data
GET      /api/v1/app-data/records/{collection}
```

Target workflow endpoints not yet fully implemented:

```http
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

## 24. Security Architecture

Required controls:

- Backend tenant enforcement
- MFA for admin, accounting, payroll, and approvers
- Permission checks on every API
- Server-side validation
- Audit logging for sensitive actions
- File upload scanning
- Encrypted object storage
- Encrypted secrets
- Session timeout
- Queue worker tenant validation
- Period reopen approvals

## 25. Testing Architecture

TaxFlow testing must prove business correctness across UI, API, accounting, VAT, inventory, security, audit, tenant isolation, posting, exception handling, and database integrity.

The repository testing guidance lives under:

```text
testing/TESTING_RULES.md
testing/TEST_MATRIX.md
testing/sales/TESTING_GUIDE.md
testing/purchases/TESTING_GUIDE.md
testing/inventory/TESTING_GUIDE.md
testing/accounting/TESTING_GUIDE.md
testing/tax/TESTING_GUIDE.md
testing/security/TESTING_GUIDE.md
testing/exceptions/TESTING_GUIDE.md
testing/performance/TESTING_GUIDE.md
testing/ai-ocr/TESTING_GUIDE.md
testing/payroll/TESTING_GUIDE.md
```

Testing layers:

```text
1. Unit Testing
2. API Testing
3. Integration Testing
4. Workflow Testing
5. End-to-End Testing
```

Every module must validate:

```text
Tenant isolation
Audit logging
Permission checks
Duplicate prevention
Accounting balance
VAT calculation
Source transaction creation
Approval state transitions
Exception handling
Database integrity
```

Never allow:

```text
Direct UI to ledger posting
Negative inventory unless enabled
Unbalanced journals
Missing tax evidence
Posting without approval
Cross-tenant reads or writes
```

Recommended tools:

| Area | Tool |
| --- | --- |
| Frontend and E2E | Playwright |
| API and backend logic | Pytest + FastAPI TestClient |
| Load testing | k6 or Locust |
| Security | OWASP ZAP plus focused authorization tests |
| Queue | Celery integration tests |
| Database | PostgreSQL transaction and constraint tests |

Highest-priority test areas:

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

Production-level tests must validate the full chain:

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

## 26. Implementation Priorities

| Priority | Improvement |
| --- | --- |
| 1 | Auth + tenant foundation |
| 2 | Customers, suppliers, and items |
| 3 | Sales and purchases |
| 4 | Source transaction layer |
| 5 | General Ledger + posting engine |
| 6 | Tax lines + VAT engine |
| 7 | Inventory mapping, unit conversion, and valuation |
| 8 | Documents and evidence |
| 9 | Exception Center |
| 10 | Basic reports and reporting snapshots |
| 11 | Audit logging and event outbox |
| 12 | Payroll, Rota, WPS, and eInvoicing after accounting/VAT/inventory are stable |

## 27. Implementation Phases

### Phase 1: Stabilize Prototype

- Keep existing UI.
- Split `src/app.js` by module.
- Remove inline event handlers gradually.
- Keep checks passing.

### Phase 2: Backend Foundation

- Add PostgreSQL.
- Add tenant/auth middleware.
- Add role and permission model.
- Add document storage.
- Add audit logging.
- Replace prototype app-data writes with module APIs.

### Phase 3: Source Transactions and Accounting

- Add source transaction layer.
- Add chart of accounts.
- Add journal entries and general ledger.
- Add posting rules.
- Add posting queue and retry logs.

### Phase 4: Tax and Evidence

- Add tax codes.
- Add tax lines.
- Add VAT periods and VAT returns.
- Link VAT claims to source documents.
- Add VAT period locks.

### Phase 5: HR, Rota, Payroll, WPS

Start this only after accounting, VAT, documents, and inventory are stable.

- Persist employees, attendance, leave, overtime.
- Persist rota periods and assignments.
- Generate payroll from approved attendance and overtime.
- Add WPS/SIF generation, validation, export, and rejection handling.

### Phase 6: eInvoicing and Integrations

- Add structured eInvoice generation.
- Add UAE schema and mandatory field validation.
- Add ASP/Peppol connector boundary.
- Add eInvoice transmission logs and archive.
- Add retry handling.

### Phase 7: Reporting and Automation

- Build reports from authoritative sources.
- Add auto posting.
- Add auto reconciliation.
- Add alerting and exception dashboards.

## 28. Final Architecture Recommendation

Use this production pattern:

```text
Business Modules
        |
        v
Source Transactions
        |
        v
Validation + Approval
        |
        v
Posting Queue
        |
        v
Accounting Posting Engine
        |
        v
General Ledger + Tax Lines
        |
        v
Reports
        |
        v
Audit + Documents + Notifications
```

Use this implementation stack:

```text
Frontend: React + Vite
Backend: Python FastAPI
Database: PostgreSQL
Queue: Redis + Celery
Storage: AWS S3
Mobile: Flutter later
```

Build order:

```text
Accounting + VAT + inventory first
        |
        v
Payroll, rota, WPS, and eInvoicing after the financial core is stable
```

This makes TaxFlow production-ready for ledger-centered accounting, tax-line VAT, UAE eInvoicing readiness, WPS payroll support, strong audit controls, period locking, and evidence-backed compliance.
