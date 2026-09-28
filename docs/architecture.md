# TaxFlow UAE Full Application Architecture

TaxFlow is a UAE business management platform for sales, purchases, accounting, tax, eInvoicing, payroll, HR, rota planning, documents, reporting, approvals, and audit control.

The system is live in production at `https://dev.etaxflow.com` on DigitalOcean App Platform. The frontend is a vanilla JS single-page app served by FastAPI. The backend uses PostgreSQL with SQLAlchemy 2.0 and Redis for report caching. Local development uses SQLite. The production target is a modular, tenant-aware business system where source transactions, tax lines, accounting, audit, and reporting are controlled by backend services.

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
frontend/public/taxflow/hrms.html        Standalone HRMS portal — served at /hrms
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
Live URL:         https://dev.etaxflow.com
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
|-- Sidebar module badges from live dashboard API counts
`-- HRMS Portal (/hrms → hrms.html)
    |-- Standalone HR management portal with own sidebar and topbar
    |-- Employees, Attendance, Rota & Shift, Leave, Payroll, Overtime
    |-- Loans & Advances, Recruitment, Performance, Training, Assets
    |-- KPI cards, progress bars, tbl tables — design from styles.css
    |-- Role-based module access via HR Settings > Roles
    `-- Weekly Rota: Repeat Schedule option (1 Year / Indefinitely) auto-creates
        Draft assignments for all future weeks from the current week's template

Backend (backend/app/)
|-- Auth and company context (JWT, token expiry 60 min)
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

### Production (https://dev.etaxflow.com)

```text
Browser
  |
  v
DigitalOcean App Platform (nyc3)
  3–6 × professional-s instances  (autoscale at 70% CPU — floor raised from
  2 to 3 during the 2026-08-14 scaling pass, since CPU-based autoscaling
  reacts over minutes and can't respond to a concurrency burst that
  resolves in seconds; see §29)
  FastAPI served by uvicorn (4 workers per instance)
  Named routes: / → index.html, /hrms → hrms.html
  Static files: frontend/public/taxflow/ served at /taxflow/
  |
  v
DigitalOcean Managed PostgreSQL (nyc3)
  Connection pool: pool_size=10, max_overflow=15 per instance (web tier)
  Celery worker: pool_size=3, max_overflow=2 (explicitly capped — previously
  unset, silently inheriting a larger code default x 4 prefork processes,
  competing with the web tier for the same DB connection budget; see §29)
  Indexes: (company_id, collection), (company_id, collection, created_at),
  plus TaxLine(company_id, direction), GeneralLedgerEntry(company_id,
  account_id, entry_date), InvoiceLine(invoice_id) added 2026-08-14 (§29)
  |
  v
DigitalOcean Managed Redis
  Report cache: keys taxflow:report:{company_id}:*
  dashboard/summary: TTL-only (60s/120s) since 2026-08-14 — previously
  invalidated on every write anywhere in the company, which meant a live
  company with any regular activity almost never got a cache hit on its
  two heaviest endpoints (§29). Other report keys (trial_balance,
  vat_return, bootstrap) still invalidate on write.
  ** As of 2026-08-14, REDIS_URL's actual production value was unconfirmed
  — if it's still unset, every caching/fallback mechanism above is a no-op
  regardless of the code being correct. Verify before relying on any of
  this (§29). **
```

**⚠ `.do/app.yaml`'s instance-floor and worker-pool values above describe what the file in this repo specifies, not necessarily what's live** — DigitalOcean App Platform does not auto-apply a spec change just because it's committed; it needs an explicit `doctl apps update <app-id> --spec .do/app.yaml` or the equivalent dashboard action. As of 2026-08-14 this had not yet been applied (§29).

Seeded local data — two options depending on what you're testing:

**Small, realistic** (`taxflow-seed50-v2.db`):

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

**Large-scale, synthetic** (`backend/taxflow-bulk100.db`, local-only, git-ignored):
100 companies × 10 branches × 95 employees × 5,000 sales + 5,000 purchase
invoices each × 5 years of history — 20.5M+ rows total. Built for load
testing at realistic scale; see §29.4 for how it's generated and why it
replicates the accounting pipeline instead of calling it.

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

**Reality check, as implemented**: posting is fully synchronous, inside the
same request that creates the source transaction (`create_invoice()` →
`sync_sales_invoice_accounting()` → ... → `post_source_transaction()`,
`module_integration.py`/`accounting_posting.py`) — not the async queue
described in §6 below. One invoice creation does roughly 14-15 sequential
DB round trips as of 2026-08-14 (down from ~22-23 before that date's
optimization pass — see §29). `create_gl_entries_from_journal()`
(`accounting_posting.py`) used to run 2 queries *per journal line* (an
`Account` lookup and a `SUM(debit-credit)` running-balance aggregate) — for
a 5-line journal, 11 queries where 3 now suffice: it fetches every account
the journal touches in one query, and the running balance's pre-existing
total for every distinct account in one grouped query, then accumulates in
memory as lines are processed. This holds a DB connection for
proportionally less time per write, which matters directly for how many
concurrent writes the shared connection pool can sustain (§29) — a
synchronous write holds its connection far longer than a read does, so
under concurrent load, writes exhaust the pool much faster than reads did.

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

`GET /api/v1/invoices` (`invoices.py::list_invoices()`) is paginated
(`limit`, default 500, max 2000; `offset`) since 2026-08-14 — previously
unbounded, which made this endpoint hang for 180+ seconds and never return
for a company with 5,000 invoices. The dominant cost turned out to be a
missing index, not just the missing pagination: `InvoiceLine.invoice_id`
had none, so `joinedload(Invoice.lines)` required a full scan of the
entire `invoice_lines` table (1.5M+ rows at real multi-tenant scale) on
every single call, regardless of how few of the requesting company's own
invoices were being fetched. Both fixed together bring the endpoint to a
consistent ~2-2.5s regardless of limit/offset (§29). No frontend code
calls this endpoint as of this writing — Sales UI reads through the
`salesInvoices` `AppDataRecord` collection via the bootstrap endpoint
instead (§22), deduped against real `Invoice` rows by
`app_sales_invoice_records()` (`reports.py`).

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

## 9.1 Point of Sale (POS) (shipped)

POS (`frontend/public/taxflow/pos.html`) is a standalone terminal page, not part of the main `index.html` SPA — its own login form (`posLogin()`, same admin/employee/branch fallback chain as `login.html`), its own `api()` wrapper, and its own copies of shared concepts like the receipt/print flow. It writes into the same `AppDataRecord` collections everything else uses (`products`, `customers`, `posSales`), so a POS sale is a normal citizen of the reporting/branch-isolation system described elsewhere in this doc, not a parallel one.

```text
Add items to cart (cartTotals() computes VAT on the DISCOUNTED subtotal —
discount is allocated proportionally across cart lines before VAT, matching
FTA guidance that a discount reduces the taxable value before tax)
        |
        v
Complete Sale (cash/card/split/credit)
        |
        v
saveRec('posSales', sale)              -- id/receipt_no = genRcptNo():
                                           full millisecond timestamp + a
                                           random suffix, not just the last
                                           5 digits of the timestamp (the
                                           old scheme recurred every ~100s
                                           and, since posSales isn't in
                                           record_key()'s explicit map, a
                                           collision silently overwrote an
                                           earlier sale's row AND its stock
                                           movements)
        |
        v
saveRec('salesInvoices', posToInvoice(sale,'Paid'))   -- mirror, non-blocking
        |                                                 but surfaced on
        v                                                 failure (toast)
sync_pos_stock()  -- writes negative StockMovement rows (movement_type
                     'pos_sale'), keyed by reference=receipt_no
```

**Customer picker** — a `<datalist>`-backed autocomplete on the customer-name field (`loadPosCustomers()`), reading `GET /app-data/records/customers`. `"customers"` is in `_BRANCH_FILTERED_COLLECTIONS` (`app_data.py`), so a Branch/Employee POS session only sees customers it created (plus any with no `branch_id` — admin-created or pre-dating Branch Management); an admin session has no `branch_id` and always sees every customer, unfiltered. Still name-only — `customer_id` is not yet captured on a sale (`posToInvoice()` hardcodes empty `customer_trn`/`customer_address` even when the matched customer record has them — a known gap, not yet closed).

**Held orders** ("Hold Order" quick action, `saveAs('draft')`) can be resumed: `openRecent()`'s completed-transactions view now has a "Held Orders" section with a **Resume** action (`resumeSale()`) that loads the draft's items back into the live cart and deletes the draft record so it can't be resumed twice. Held/suspended records are explicitly skipped by `sync_pos_stock()` — they used to decrement real stock the moment an order was held (before it had actually sold anything), with nothing ever reversing it if the hold was abandoned or resumed; `sync_domain_delete()` also gained a `posSales` branch so deleting a held order cleans up any stock movements it had already accumulated before that fix.

### Sales Return / Refund (v1: full-sale only, shipped)

A refund is triggered per past sale from `openRecent()`'s completed-sales table (`openRefund()` → a confirm modal requiring a reason → `confirmRefund()`). It writes a **second, independent, negative-amount `posSales` record** rather than mutating the original sale:

```text
{
  id: 'RET-'+original.receipt_no,   -- deterministic: a duplicate refund
                                        attempt upserts instead of creating
                                        a second row (record_key() falls
                                        back to id for posSales)
  type: 'refund', status: 'refunded',
  original_receipt_no: original.receipt_no,   -- the link back
  refund_reason, items (qty stays POSITIVE — "2 units returned"),
  subtotal/vat/discount/total: all NEGATED
}
```

Before writing, `confirmRefund()` re-checks for an existing refund against the same `original_receipt_no` (closes the two-tills-at-once race); `openRecent()` also hides the Refund button and shows a "Refunded" label once one exists, computed client-side from the same `posSales` fetch (no server-side "refunded" flag on the original record).

**Why the refund bypasses the normal ledger/Invoice pipeline — the key architectural decision.** Two backend facts, not a preference: `build_journal()` (`accounting_posting.py`) rejects any negative `subtotal`/`vat`/`total` (`PostingError("Source transaction has invalid posting amounts")`); and creating a real `Invoice` row for the refund's `salesInvoices` mirror would make `app_sales_invoice_records()` (`reports.py`) dedupe the app-data record out of every report in favor of that `Invoice` row, whose own status filtering excludes `"return"` anyway. So `sync_domain_model()`'s `salesInvoices` branch now gates `sync_sales_invoice()` behind a sign-aware check:

```text
_is_credit_note_record(record):
  "return" in (document_type + source + status).lower()  AND  total < 0
        |
        v
  True  -> skip sync_sales_invoice() entirely. No Invoice row, no
           SourceTransaction, no PostingJob, no TaxLine. Reports read the
           refund straight from the app-data payload instead.
  False -> normal path, unchanged — this is exactly how the MAIN Sales
           module's own pre-existing hand-keyed "Sales Return" feature
           (document_type:'Sales Return', but stored POSITIVE) already
           behaved before this change, and continues to behave identically
           after it, because it never satisfies "total < 0".
```

`posToInvoice()`'s mirror sets `document_type:'Sales Return'`, `source:'POS Return'` for a refund — matching the main Sales module's own existing convention (`isSalesReturn()` string-sniff in `app.js`) exactly, so a POS refund lands in that module's Sales Returns register with no changes there.

**Reports correctness** — `reports.py` gained `_is_credit_note(row)`, the same sign-aware check, and `_build_summary()`'s revenue/VAT calculation now recognizes a row via `_is_recognized_revenue_status(status) OR _is_credit_note(row)` — one list feeds revenue, output-taxable, output-VAT, and net-VAT-payable simultaneously, so a refund correctly reduces all four. `receivables_aging` also excludes credit notes from the open-receivable count. Gating on **sign**, not status/text alone, is deliberate: it means the pre-existing positive-amount hand-keyed Sales Returns in the main module are completely unaffected by this change — still excluded from `_build_summary`, still (pre-existing, separately-flagged) counted as positive revenue in `_build_dashboard`. Fixing that historical inconsistency would mean rewriting historical data, a distinct decision not made as part of this change.

**Stock reversal** reuses `sync_pos_stock()` with a sign-aware branch rather than a near-duplicate sibling function — a refund's `reference` (`'RET-'+original`) is its own namespace, so the existing delete-by-reference cleanup can't touch the original sale's movements:

```text
is_return = record.type == 'refund'  OR  record.total < 0
movement_type = 'pos_return' if is_return else 'pos_sale'
quantity = (+1 if is_return else -1) * quantity
```

`list_stock_levels()` sums all movement types unfiltered, so no downstream inventory code needed to change.

**Out of scope for v1**: partial/line-item refunds, manager PIN/approval gating on refunds, refund-method choice (mirrors the original sale's payment method), a real FTA-numbered credit-note document/PDF (v1 relabels the existing thermal receipt), and any GL/journal posting for the refund (deliberately bypassed, per above — a proper reversing credit-note journal would mean relaxing `build_journal()`'s negative-amount guard, a separate accounting change).

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

**Backfill-disabled marker:** the backfill step above is skipped whenever an `inventorySettings` / `stock_backfill_disabled` `AppDataRecord` marker is set for the company (checked via `inventory_backfill_disabled()`). A new purchase's stock movement is created directly at save time by `sync_purchase_stock()`, independent of backfill — backfill exists only to catch up legacy/orphaned purchase records that predate that sync. `DELETE /api/v1/inventory/stock-levels` ("Clear Stock Data") sets this marker rather than clearing it, precisely so the wipe stays cleared: previously it removed the marker instead, which re-enabled backfill and had it immediately resurrect every historical purchase's stock on the very next `GET /inventory/stock-levels` or `/inventory/stock-movements` call, undoing the wipe. All three read endpoints must check the marker before backfilling — `list_mappings()` always did; `list_stock_levels()`/`list_stock_movements()` were missing the check and have since been fixed.

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

**See `docs/hrms-architecture.md` for the authoritative, current-state HR/HRMS reference** — module-by-module Tier 1 (real table) vs Tier 2 (JSON bridge) status, the full RBAC/GPS-attendance data model and API surface (both now built, not just planned), and HRMS-specific security findings. This section stays as a high-level summary only.

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

## 15.1 Biometric Attendance Connections

**See `docs/biometric-architecture.md` for the authoritative reference.** Four connection methods are shipped — `zk_bridge.py` (on-prem script → TaxFlow, for direct ZKTeco/Anviz TCP/IP devices), ZKTeco ADMS/HTTP Push, a BioTime 9.5 pull connection (`BiometricDevice.device_type == "ZKTeco BioTime Server"`), and Manual/CSV — all converging on the same `attendance_punches` table. A fifth, the **TaxFlow Biometric Agent**, is additive (nothing shipped is replaced): its Agent-to-local-BioTime mode shipped as a v1 script (`backend/biotime_agent.py`, for customers whose BioTime server isn't internet-reachable); Agent-to-device mode and the operational polish (Windows service packaging, auto-update, remote diagnostics, LAN discovery) are still proposed.

## 16. Rota Planning

Rota is a separate module.

```text
Rota Planning
|-- Shift Setup
|-- Weekly Rota
|   `-- Repeat Schedule: 1 Year (52 weeks) or Indefinitely (~260 weeks)
|       Copies current week's assignments forward as Draft records
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

**Current real schema and known-fixed bug:** `PayrollItem.allowances/.overtime/.deductions` exist only on the payroll item (this run's own output) — `Employee` itself has no such columns. Payroll generation previously read `employee.allowances`/`.overtime`/`.deductions` directly, which raised an uncaught `AttributeError` and 500'd on every single call, for every company, unconditionally (found by generating a full year of payroll against a production account with 100 real employees). Fixed: these now default to `0` per employee at generation time. See `docs/hrms-architecture.md` §5 for the full current `Employee`/`PayrollRun`/`PayrollItem` schema and §9 for the incident writeup.

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

**Client-side rendering must be chunked for real data volumes.** `hydrateFromServer()`'s per-collection render loop (`renderRecordList()` in `app.js`) and the Ledger's journal-entry render loop both used to run as one uninterrupted synchronous pass over the full collection. Invisible at demo scale (dozens–hundreds of rows), but with genuinely large data (1000+ sales invoices, 2000+ journal entries — reproduced by seeding a production account to that volume) this blocked the browser's main thread long enough to trip Chrome's "Page Unresponsive" watchdog, even though the server had already answered and the data was correct. Both loops now yield every ~60 records via a microtask break (`await new Promise(r=>setTimeout(r,0))`) instead of running straight through. Any new render loop over a server-fetched collection should do the same once the collection can plausibly exceed a few hundred rows for a real company — the "instant" fix is pagination (previous section); the "even if paginated, rendering one page must not freeze the tab" fix is chunked yielding, and both are needed for a genuinely large dataset.

**`/reports/dashboard` and `/reports/summary` are the two heaviest, most-frequently-hit endpoints in the app**, and were the starting point of a 2026-08-14 scaling investigation (full writeup in §29). As found then: `dashboard()` (`_build_dashboard`, `reports.py`) issued ~46 sequential DB round trips per uncached call before that date, mostly ~20 separate single-table `count()` queries and an `invoice_status()` loop running 2 queries per status bucket; `report_summary()` (`_build_summary`) issued 60-80+ (the estimate — the real number turned out closer to ~39, since a SQLAlchemy subquery some call sites looked like "2 queries" was actually composed into 1 by the ORM). Both are now consolidated: `module_counts`'s ~20 counts became one `UNION ALL` (`_company_table_counts()`), `invoice_status()`'s per-bucket loop became one `GROUP BY`, `tax_line_breakdown()`'s two per-direction calls became one grouped query for both directions at once (`tax_line_breakdown_both_directions()`). Result: dashboard ~46→~21 queries, summary ~39→~37 (most of that function's helpers were already efficient on inspection — see §29 for what was and wasn't worth touching).

Both endpoints share a helper, `_cached_or_build()` (`reports.py`), that does two things beyond a plain cache-or-compute: (1) cache entries carry a `{data, cached_at}` envelope with a 24h safety-net TTL (`cache.set_with_staleness()`/`get_with_staleness()`) on top of their normal freshness window (60s dashboard, 120s summary) — a "fresh" hit returns immediately as before, but a cache-miss that then fails with a DB-overload-shaped exception (`sqlalchemy.exc.TimeoutError` or `OperationalError`) falls back to whatever was last cached, even if stale (marked `"stale": true` in the response), instead of a hard error — for any company that's loaded the report at all in the last day. (2) Only when there's truly nothing cached does the exception propagate, where `main.py`'s global exception handler turns it into a `503` + `Retry-After` instead of a generic `500` — semantically distinguishing "server is momentarily overloaded, safe to retry" from a real bug. `app.js`'s `syncDashboardFromDatabase()`/`syncReportsFromDatabase()` add a matching client-side layer: bounded retry (2 attempts, 400ms/1200ms backoff) specifically on `500`/`503`/`504`, plus a 15s `AbortController` timeout, before falling through to the existing stale-localStorage-snapshot UI.

Cache invalidation for these two keys changed from instant (busted on every write anywhere in the company, via the `cache_invalidation` middleware in `main.py`) to TTL-only — see the Production section above for why.

**None of this actually engages without Redis configured** — `cache.py` silently no-ops (`_redis()` returns `None`) when `REDIS_URL` is unset or `memory://`, at which point every request pays full recompute cost regardless of the work above, and the stale-fallback has nothing to ever fall back to. Confirm this is set before assuming any of the above is helping in a given environment.

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

## 21.1 Branch Management (shipped)

Multi-branch support is built and live — not the aspirational `branches` table implied elsewhere in this doc, but a real, tested feature. A company can register multiple branches, each staffed by employees who log in with their own credentials and see only their own branch's data, while the company admin always sees everything consolidated.

```text
Branch (models.py)
id, company_id, name, code, city, address, status
```

A `Branch` has no login of its own — it is purely an administrative entity (name/code/city/address/status), managed via Settings > Departments & Branches (`POST/GET/PUT/DELETE /api/v1/branches`).

Login and identity:

```text
Employee.branch_id (nullable FK to Branch)
        |
        v
Employee gets Portal Access (HR Settings > Users & Roles > Grant Portal Access)
  -> username + bcrypt password_hash + role_id, PUT /hr/admin/employees/{id}/portal-access
        |
        v
POST /hr/login -> "emp:"-prefixed JWT
        |
        v
Principal.branch_id set from Employee.branch_id (auth_principal.py)
```

No new auth surface was added for this part of the feature — a branch employee is just an `Employee` (the existing HR RBAC identity, see `docs/hrms-architecture.md`) assigned to a `Branch` and granted the existing Portal Access flow. `Principal.is_admin` is always `True` for a `User` (admin) token and `False` for an `Employee` token; `Principal.branch_id` is `None` for admins and for any employee without a branch assignment, which is the backward-compatibility mechanism — every branch filter below is a no-op unless a real `branch_id` is set. (A genuine third auth surface — the Branch entity logging in directly, with no Employee involved — was added later; see §21.2.)

Data isolation, two mechanisms:

```text
AppDataRecord (JSON-blob collections: POS, Purchases, Sales invoices)
  branch_id column + _BRANCH_FILTERED_COLLECTIONS allowlist (app_data.py)
  = purchaseRecords, posSales, salesInvoices
  Admin can opt in to view one branch's data via ?branch_id= on
  GET /app-data/records/{collection} (any collection, not just the allowlist).

ORM tables (real ledger/relational tables), branch_id column stamped by the
posting pipeline all the way down:
  Invoice, JournalEntry, GeneralLedgerEntry, SourceTransaction, StockMovement,
  AttendanceSession, CompanyLocation, Employee, AppDataRecord
  A branch-scoped principal's queries add:
    (X.branch_id == principal.branch_id) | (X.branch_id.is_(None))
  NULL branch_id (legacy data, or entries a module never stamped) stays
  visible to a branch-scoped viewer rather than vanishing — same rule
  applied consistently everywhere this filter is used.
```

Endpoints currently branch-scoped this way: `/hr/dashboard` and `/hr/live-locations` (attendance), `/hr/company-locations` (GPS geofences, filtered against the employee's own branch + any `EmployeeBranchAccess` grants), `/reports/trial-balance`, `/inventory/stock-levels` and `/inventory/stock-movements`, `/journal` and `/general-ledger` (accounting.py), `/invoices` (both the `GET` list and, since the 2026-08-14 branch-isolation-gaps fix, the `POST` create — a branch-scoped principal supplying an explicit `branch_id` outside their `accessible_branch_ids` now gets a 403 instead of the write silently landing wherever they asked), `/payroll/employees` and `/payroll/runs`, and the three `_BRANCH_FILTERED_COLLECTIONS` above.

`/attendance/today`, `/attendance/trend`, `/attendance/punches`, and `/attendance/summary` are also branch-scoped, but via a different mechanism than the `X.branch_id == principal.branch_id` filter above: `AttendancePunch.employee_id` is a plain string holding the employee's business-facing `employee_no`, not a FK, and the table has no `branch_id` column of its own. `attendance.py::_branch_scope_punches()` instead joins `AttendancePunch` to `Employee` on `Employee.employee_no == AttendancePunch.employee_id` and filters on `Employee.branch_id` — the same NULL-stays-visible rule applies. Any future table that records an employee by `employee_no` string rather than an `Employee` FK needs this join pattern, not the direct-column filter.

**Deliberately NOT branch-scoped** (company-wide by design, not a gap): `/reports/dashboard` and `/reports/summary` (several data sources — `TaxLine`, `AuditLog`, `CorporateTaxRecord`, `BudgetRecord` — have no `branch_id` and may never need one; `PayrollRun` does have a `branch_id` now and is scoped at `/payroll/runs`, just not re-filtered again inside the dashboard aggregate); VAT/Corporate Tax returns (filed once per company per period under UAE law, not per branch); `/inventory/mappings` and the chart of accounts (shared master data). Vouchers, Payments, Receipts, BankAccounts, and every Corporate Accounting `*Record` model have no `branch_id` column at all yet — extending scoping there needs a schema migration, not just a query change.

Cascade-delete completeness: `superadmin.py::delete_company()` must clean up every branch-scoped join table before deleting the `Employee`/`Branch` rows they point to, or the delete fails on an FK violation. `EmployeeBranchAccess` (no `company_id` column of its own — FKs only to `employees.id`/`branches.id`) was missing from this cascade until the 2026-08-14 fix; it's now cleaned up via an `emp_ids` subquery alongside `EmployeeLocation`, the existing reference pattern for any table that hangs off `Employee` without its own tenant column.

**Superadmin console visibility**: `GET /superadmin/companies` returns each company's branches (name, code, city, status) and up to 200 employees (name, employee_no, department/designation, status, resolved branch_name) alongside the existing per-company user list — added 2026-08-14, mirroring the existing "Users" toggle-panel UI pattern in `superadmin.html` with matching "Branches"/"Employees" toggles. Employees are capped (200) since a roster can run into the hundreds/thousands unlike users or branches; `employee_count` (already returned) still reflects the true total past the cap.

**Main Dashboard Access for branch employees**: a branch employee's HRMS login (`/hrms`) can also reach the main business dashboard (`/`, same app as the admin uses) if their assigned Role grants at least one `<module>:view` permission (`sales:view`, `pos:view`, `purchase:view`, `inventory:view`, `accounting:view`, `corporate:view`, `reports:view`, etc. — `_PERMISSION_CATALOG` in `hr_access.py`). A reciprocal "Main Dashboard" link appears on `hrms.html` when applicable; the sidebar on `index.html` shows only the modules the Role permits (`applyMainDashboardPermissionNav()` in `app.js`), and every widened endpoint enforces the same permission server-side via `require_principal_permission("module:view")` — a permission-less employee gets a session-preserving 403, never a forced logout (this was the actual bug being fixed: `authenticatedFetch()` treats 401 as "force logout," 403 as "valid session, no access to this call"). This module-visibility gate is independent of the branch data-scoping above: a branch employee with `accounting:view` sees the Accounting module, and within it, only their own branch's data.

Testing: `backend/tests/test_branch_isolation.py` and `backend/tests/test_main_dashboard_access.py` — the reference pattern for any new branch-scoped or permission-gated endpoint (grant a role/permission, log in as an Employee, assert branch/permission isolation, assert an admin token and a branch-less employee both see everything unchanged).

## 21.2 Branch Login (shipped)

Two independently shippable phases on top of §21.1, both live: a module-level restriction a `Branch` can carry on its own, and a genuine third login identity — the `Branch` entity itself, with its own username/password, separate from any `Employee`.

**Phase 1 — module toggle + inheritance.** `Branch.modules_enabled` (JSON array column, same `NULL`/empty-means-unrestricted convention as `Company.modules_enabled`) is an additional restriction layer, not a replacement for an employee's Role: `require_module()` (`dependencies.py`) and `assert_collection_module_enabled()` (`app_data.py`, the actual write choke point for POS/Sales/Purchase/Inventory) both now check company-level *and* branch-level module enablement whenever `principal.branch_id` is set — applies equally to a branch-assigned `Employee` and the Branch identity below. Two inheritance rules, both server-enforced: a company can only enable, for a branch, a module the company itself has enabled (`branches.py::_validate_branch_modules`); an HR Role can only be granted a permission for a module the company has enabled (`hr_access.py::_company_allowed_catalog_keys`, wired into `admin_list_permissions`/`admin_create_role`/`admin_update_role` — a hard 400, checked before any `Role`/`RolePermission` row is written). `BRANCH_ELIGIBLE_MODULES` (`app/module_catalog.py`) is the restrictable subset of `ALL_MODULES` — Main-Dashboard business modules only (`sales`, `pos`, `purchase`, `inventory`, `accounting`, `reports`, etc.); `hrms`/`ess`/`settings` are deliberately excluded, so HR/attendance access stays governed purely by an employee's own Role, never by which branch they're assigned to.

**Phase 2 — the Branch entity's own login.** `Branch` gained `username`/`password_hash` (mirrors `Employee`'s portal-access columns; no `employee_no`-style password fallback — a real password must always be set explicitly). `POST /api/v1/branches/login` is a third login endpoint alongside `/auth/login` (User admin) and `/hr/login` (Employee); the shared `/login` page tries all three in sequence (only falling through on a 401, not a 403) and auto-routes based on which one succeeds. Token subject is prefixed `"branch:"` (mirrors `"emp:"` for Employee tokens), resolved by `_principal_from_branch_token()` in `auth_principal.py`, wired into `get_current_principal()`'s existing try-in-sequence chain alongside the Employee and User resolvers.

Critically, a Branch principal has **no Role of its own** — per the "module toggle alone = full access" design decision, its `permissions` frozenset is synthesized directly at login/request time as `"<module>:view"` for every module in the intersection of company-enabled and branch-enabled modules, which is sufficient to satisfy every existing `require_principal_permission()` check with no new permission model. `accessible_branch_ids` is always exactly `{self.id}` — a Branch identity never switches branches, unlike a multi-branch `Employee` (§21 Tenant Isolation's branch-switcher mechanism doesn't apply here); it lands on `/` (never `/hrms` — zero HRMS access, since its permissions never contain an HR-suite key) and is otherwise subject to the exact same branch data-isolation and module-visibility mechanisms described in §21.1/above, just as a non-Employee principal kind (`Principal.kind == "branch"`, `Principal.branch` holds the `Branch` row instead of `.user`/`.employee`). `AuditLog.branch_actor_id` records a Branch principal's actions (a fourth, mutually-exclusive-with-`user_id`/`employee_id` actor column).

Frontend: `hrms.html`'s Branch modal gained a Module Access grid (`buildBranchModGrid()` in `app.js`, filtered to `BRANCH_ELIGIBLE_MODULES ∩ window.COMPANY_ALLOWED_MODULES`) and an inline "Branch Login" username/password section with the same omit-if-blank-on-edit semantics as the Employee Portal Access modal. `login.html`/`pos.html` each carry their own independent copy of the three-way login fallback chain (not shared code) — this drifted for a while: `posLogin()` set `taxflow_token` but never `taxflow_principal_kind`, so a Branch/Employee session that logged in through POS was silently treated as an unset/admin session everywhere else (see §24's 2026-08-12 entry for the consequence and fix). The four `taxflow_principal_kind` guards in `app.js` (`ensureBackendSession`, `loginLocalBackend`, two init-block checks) that already excluded `'employee'` now also exclude `'branch'`, for the identical reason — a non-admin session must never silently auto-relog as the dev seed admin on a 401.

Testing: `backend/tests/test_branch_role_module_inheritance.py` (Phase 1) and `backend/tests/test_branch_login.py` (Phase 2).

## 22. Data Architecture

Recommended database groups:

```text
Identity
|-- users
|-- roles
|-- permissions
|-- role_permissions
`-- user_sessions
    (still not built for TaxFlow Users/admins — a separate, already-built
    RBAC system exists today, but scoped to HR sub-users only: Role,
    Permission, RolePermission in models.py, module:action keyed
    permissions like "leave:edit", managed via /api/v1/hr/admin/*. See
    docs/hrms-architecture.md §5/§7. It does not cover User/admin
    permissions at all — a User is always full-access by construction
    (Principal.is_admin=True bypasses every permission check).)

Tenant
|-- companies
|-- branches      (real, shipped — see §21.1 Branch Management)
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
GET/POST /api/v1/journal   (GET is paginated: ?limit&offset, returns
                            {records, total, limit, offset, has_more} —
                            see pagination pattern note below)
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
POST     /api/v1/payroll/runs/{run_id}/approve   (drains loan/advance balances; audited)
DELETE   /api/v1/payroll/runs/{run_id}           (draft runs only)
GET      /api/v1/payroll/runs/{run_id}/sif       (CBUAE EHR/SCR/ETR from stored items)
GET      /api/v1/attendance/overtime-eligibility (?date_from&date_to; days worked past standard, cool-off aware)
GET      /api/v1/ess/overtime-eligibility        (caller only, last 31 days)
POST     /api/v1/companies/current/users         (company admin adds a login)
GET      /api/v1/reports/dashboard
GET      /api/v1/reports/trial-balance
GET      /api/v1/reports/summary
GET      /api/v1/audit/trail
GET      /api/v1/ai/workbench
POST     /api/v1/ai/assist                -- real LLM call (OpenAI preferred,
                                              Anthropic fallback; app/ai_client.py,
                                              shared with the HR AI features),
                                              grounded in the company's live
                                              VAT/invoice/exception snapshot plus
                                              recent open exceptions. Falls back
                                              to the original rule-based
                                              keyword-matched answers if no key
                                              is configured or the call errors —
                                              never a 500 or a blank reply.
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
POST     /api/v1/corporate-accounting/fixed-assets/{id}/depreciate
POST     /api/v1/corporate-accounting/accruals-prepayments/{id}/release
GET/POST /api/v1/item-units
GET/POST /api/v1/item-unit-conversions
GET      /api/v1/inventory/stock-levels
GET      /api/v1/inventory/valuation-layers
GET/POST /api/v1/inventory/adjustment-approvals
GET      /api/v1/app-data
POST     /api/v1/app-data
GET      /api/v1/app-data/records/{collection}   (paginated: ?limit&offset)
GET      /api/v1/auth/whoami   (resolves a User, Employee/HR sub-user, or
                                 Branch bearer token into one shape —
                                 {kind: "user"|"employee"|"branch", id,
                                 company_id, display_name, is_admin,
                                 permissions, role_name})
GET/POST /api/v1/branches      (also PUT/DELETE by id — see §21.1 Branch
                                 Management for the full feature)
POST     /api/v1/branches/login   (Branch entity's own login — see §21.2
                                    Branch Login)
```

**List-endpoint pagination pattern:** any endpoint returning a collection that can grow past a few hundred rows for a real company (journal entries, purchase records, sales invoices, etc.) should paginate: `limit`/`offset` query params (bounded, e.g. `Query(default=100, ge=1, le=500)`), response envelope `{records, total, limit, offset, has_more}`. This was retrofitted onto `/journal` after it was measured at ~500ms server time alone with ~2000 rows and no cap at all (growing unbounded as a company accumulates transaction history) — `/app-data/records/{collection}` already had this pattern from the start and should be the template for any new list endpoint, not the unbounded style `/journal` used to have. The frontend pairs this with page-by-page Previous/Next navigation (not an accumulating "Load More") so the DOM never holds more than one page's worth of rows regardless of how many pages a user clicks through — see `goToJournalPage()`/`goToPurchaseRecordsPage()` in `app.js` for the reference implementation, and the "Page Unresponsive" note under §18 for why unbounded client-side rendering is the other half of this problem.

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

**See `docs/hrms-architecture.md` §9 for current, concrete HRMS/RBAC security findings** (a Tier 2 write-path RBAC gap, a privilege-escalation path via `hr_settings:edit`, a weak default-credential fallback, and others) — these are specific, reproduced issues in the code as it stands today, not the general aspirational controls below.

**Recent app-wide security fixes (2026-08-07)**: a superadmin-only `DELETE /superadmin/companies/{id}` now requires the acting superadmin's own account password, verified server-side (previously gated only by two hardcoded strings checked client-side in JS); `companies.py`'s `_resolve_company()` used to silently re-link a user with an orphaned `company_id` to whichever company sorted first in the table — an arbitrary unrelated tenant — now fails closed (404) instead; superadmin's `reset-password`/`delete-company`/`impersonate` endpoints gained rate limits (20/minute, defense-in-depth against a leaked token); production startup now also rejects `CORS_ORIGINS="*"` (combined with `allow_credentials=True`, a wildcard origin lets any site make credentialed cross-origin requests). See `backend/tests/test_security_hardening.py` and `backend/tests/test_companies_isolation.py`.

**Branch Login (2026-08-08/09, §21.2)**: a new, genuinely separate auth surface — `POST /api/v1/branches/login` authenticates a `Branch` entity directly (no `Employee` in the loop). Follows the same timing-safe pattern as every other login endpoint (`hr_login`, `authenticate_user`): a constant-time dummy-hash `bcrypt` verify runs even when the username doesn't match, so response timing can't be used to enumerate valid branch usernames. `Branch.username` is globally unique (partial unique index, `WHERE username IS NOT NULL`) and case-insensitively matched, same convention as `Employee.username`. A Branch principal has no Role and no password-reset/employee_no fallback — a real password must be set explicitly by a company admin before the login works at all. Deploy note: this and the preceding module-toggle phase both changed `app.js` substantially; the cache-busting `?v=` query string on `<script src="src/app.js?v=...">` (`hrms.html`, `index.html`) must be bumped on any app.js-touching deploy, or browsers that visited before the deploy keep serving stale JS indefinitely — missed once for this exact feature (fixed same day) and confirmed as the root cause of an early "branch login doesn't work" report even though the backend was already correct end-to-end.

**Force-logout bug class + session key leakage (2026-08-12)**: `authenticatedFetch()` (`app.js`) treated *every* 401 as "invalid session — wipe the token and force re-login," which is only correct for a genuinely expired/invalid token. `get_current_user()` (`dependencies.py`, the User-only backend dependency) already returns a distinguishable `detail` for the other case — `"User no longer exists"` — whenever a Branch/Employee token's JWT subject (never a real `User.id`, by design) hits a User-only endpoint; a real expired/malformed token instead produces `"Invalid access token"` (any `JWTError`, including expiry). The frontend never looked at that distinction, so any unguarded call site touching one of the **93** `get_current_user`-only endpoints (vs. 12 accepting any `Principal`) force-logged-out an otherwise-perfectly-valid Branch/Employee session — reproduced twice: once via an AI Workbench bootstrap call (fixed same day with a call-site guard), once via `loadExceptionCenter()` (`"exception"` has no `data-module` nav gate, and the async permission-nav check fails open before it resolves). `authenticatedFetch()` now only force-logs-out when the 401 *isn't* the "User no longer exists" detail for a `taxflow_principal_kind` of `'employee'`/`'branch'` — the same principle already applied to 403 two lines above (a scoped rejection must not nuke the whole session) — fixing the bug class at its root instead of requiring a guard at every call site, present and future. Two adjacent, independently-real issues found in the same pass: `posLogin()` (`pos.html`) never set `taxflow_principal_kind` at all (see §21.2's note); and `logout()` didn't clear `taxflow_principal_kind`, `taxflow_active_branch_id`, `taxflow_superadmin_token`, `ess_token`, or `hr_access_token` — the `taxflow_active_branch_id` gap in particular meant a branch-scoped session logging out on a shared browser left the *next* login on that browser silently filtered to the previous session's branch (`_withActiveBranchParam()` appends it to every branch-aware call), a real cross-session data-scoping leak, not cosmetic staleness. All three fixed together; no backend change needed — `get_current_user()`'s existing error shape already carried the signal.

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

**Session timeout, as implemented**: a flat JWT expiry, not a sliding/idle
timeout — `create_access_token()` (`security.py`) stamps `exp` at
`now + ACCESS_TOKEN_EXPIRE_MINUTES` on issue and never renews it; there is
no refresh-token flow. `ACCESS_TOKEN_EXPIRE_MINUTES` is **1440 (24h) in
production** (`.do/app.yaml`) vs. a **60-minute default** in
`config.py`/local `.env` — every login (admin `User`, HR `Employee`, and
`Branch` tokens alike) gets the same fixed lifetime regardless of activity
level; a session open and actively used for 23 hours expires at the same
wall-clock instant as one left idle the whole time. No client-side idle
timer exists either — `app.js` has no inactivity-based auto-logout; the
only session-expiry UI (`"Session expired — please sign in again"`, line
~2196) is reactive, shown only after a request actually comes back `401`.
Expiry is enforced purely server-side via JWT signature+`exp` validation
(`user_id_from_token()`, `security.py`) — a client can't extend its own
session by any means short of a fresh `/auth/login` (or `/hr/login` /
`/branches/login`) call. See §24's Force-logout entry above for the
separate, already-fixed concern of *what the client does* when a 401
arrives (was: always force-logout; now: only for a genuinely expired/
invalid token, not a merely under-permissioned one).

## 25. Testing Architecture

Current state (2026-09-25): 470+ backend tests (`backend/tests/`); the full suite runs in ~9 minutes on SQLite and has one known weekday-dependent failure (`test_team_today_shows_present_absent_and_leave_within_the_same_department`). Live QA is done with Playwright-driven Chromium tracks (see `testing/TEST_RUN_2026-09-25.md`). Tests that assert exact counts share one company via the `auth_headers` fixture and can be polluted by other files — use `_register(client, suffix)` (tests/test_registration.py) for an isolated company, and clean up global tables (e.g. `products`) you write to.

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

## 29. Performance & Scaling (2026-08-14)

A production load test — read-only GET bursts against /reports/dashboard using the live admin account, ramping concurrency from 1 to 100 — found the app starting to fail at just **20 concurrent requests** (10% error rate, climbing to 20-30% at 30-100), with median latency growing from 2.7s to 5.3s under load, and even a single uncontended request already taking 1.4-2.5s. This section is the full writeup: what was found, what was fixed in code, and what's still open (infrastructure-only, not code) as of this date. Individual sections above cross-reference back here (§1.2, §5, §9, §18, §21.1) rather than duplicating detail.

### 29.1 Root causes found

1. **DB connection-pool budget smaller than it looks, unevenly shared.** Web tier: pool_size=10/max_overflow=15 per instance x 2 baseline instances (pre-fix) = 50 cluster-wide, before DigitalOcean's CPU-based autoscaling can react (minutes, not the seconds a concurrency burst lasts). The Celery worker set no pool env vars at all, silently inheriting the *larger* code default (pool_size=20/max_overflow=40) x its 4 prefork processes — a background queue competing harder for the same budget than the user-facing web tier. Postgres refusing a connection outright (its own max_connections ceiling, still unconfirmed as of this date) surfaces as a fast, generic 500.
2. **Report endpoints chatty independent of concurrency** — see §18's dashboard/summary paragraphs for the exact before/after query counts. This alone explained the "slow even alone" symptom; it's serial chattiness, not contention.
3. **Cache invalidated on every write** — see §18. Meant the "cold, full-cost" path was the common case for any company with regular activity, not the exception.
4. **Intermittent 401s under load using a single valid, unexpired token** — investigated and ruled out as pool exhaustion (verified no code path converts a DB error to 401; it would surface as 500) and ruled out as rate-limiting (429, not 401; keyed per-IP not per-token). Leading hypothesis was a SECRET_KEY mismatch across instances/workers from a stale prior deploy; diagnostic logging was added at every token-validation failure point (security.py::user_id_from_token(), auth_principal.py::get_current_principal(), dependencies.py::get_current_user()) including a safe SHA-256 fingerprint of the resolved secret key (never the key itself) for cross-instance correlation — **not conclusively resolved**, needs live log data to confirm.
5. **GL-posting per-line queries** — see §5's Accounting Core paragraph.
6. **GET /invoices completely unpaginated, plus a genuinely missing index** — see §9.
7. **Exception handling only caught the client-side pool-wait timeout, not the (found to be at least as common) server-side connection refusal** — main.py's global handler now catches both sqlalchemy.exc.TimeoutError and sqlalchemy.exc.OperationalError, routed through the same stale-cache-fallback / 503+Retry-After path (§18).

### 29.2 Fixes shipped (code, all deployed)

- reports.py: dashboard()/report_summary() query consolidation (§18)
- New indexes: TaxLine(company_id, direction), GeneralLedgerEntry(company_id, account_id, entry_date), InvoiceLine(invoice_id) — all via the established self-migrating CREATE INDEX IF NOT EXISTS startup pattern in main.py, plus matching Index(...) model declarations
- cache.py: TTL-only invalidation for dashboard/summary + stale-cache-serve-on-overload (§18)
- main.py: 503+Retry-After for TimeoutError/OperationalError instead of a generic 500
- app.js: bounded client-side retry-with-backoff + timeout for the two report loaders (§18)
- accounting_posting.py: create_gl_entries_from_journal() batches account lookups and the running-balance aggregate per journal instead of per line — 11→3 queries for a typical 5-line journal (§5)
- invoices.py: GET /invoices pagination (§9)
- .do/app.yaml: web tier instance floor 2→3, Celery worker's DB pool explicitly capped (pool_size=3/max_overflow=2 vs. the previous unset-and-inheriting-a-larger-default) (§1.2)

Every fix above was verified against a purpose-built 100-company/1M-invoice local dataset (§29.4) with a before/after correctness diff (byte-identical output except where a fix deliberately changes behavior, e.g. bounding a previously-unbounded report) before being trusted, plus the full backend/tests/ suite (217 tests) passing after each change.

### 29.3 Still open — infrastructure/DO-dashboard actions, not code

None of these can be applied by editing this repo; each needs direct action on the DigitalOcean dashboard (or doctl) and re-measurement afterward, not just an assumption that it helped:

1. **Apply .do/app.yaml** — committing it does not change the live app's running config. Needs `doctl apps update <app-id> --spec .do/app.yaml` or the dashboard equivalent.
2. **Confirm/set REDIS_URL** — as of this date, unconfirmed whether it's actually configured. Without it, every caching/stale-fallback mechanism in §29.2 is a silent no-op — this is a *prerequisite* for measuring whether anything else here is working, not an independent nice-to-have.
3. **Confirm the managed Postgres plan's actual max_connections** (DO dashboard → Databases → cluster → Settings) — determines whether the pool rebalancing in .do/app.yaml is sufficient or the plan itself needs upgrading.
4. **Set up DigitalOcean's connection pooler (PgBouncer)**, transaction-pooling mode, and point DATABASE_URL at the pooled port instead of the direct one (25060 today). Assessed as "worth considering" at a 200-concurrent-user target; assessed as *required* at a 500-user target — the connection math otherwise doesn't work regardless of how the app-side pool is tuned.
5. **Re-examine rate limits** (default_limits=["300/minute"] global, bootstrap 60/minute, dashboard/summary 120/minute, all keyed per-IP in limiter.py) once Redis status (item 2) is known — the in-memory fallback backend means each of the ~4 workers x N instances keeps its own counter, making the *effective* limit inconsistent depending on which process a request lands on; separately, several real users sharing one office/NAT IP legitimately tripping these is a real, previously-observed failure mode (dashboard's limit was raised 30→120/minute earlier this session for exactly this reason).
6. **Consider a Postgres read replica** for report/list traffic, separating it from the primary's write-path connection budget — a genuinely bigger architectural lever, proposed at the 500-user scale specifically.
7. **Consider decoupling GL posting from the synchronous request path** onto the existing Celery worker — the invoice/purchase save would return once the source record exists, with GL/TaxLine posting completing moments later. Shrinks the write path's connection hold time significantly, at the cost of a real product behavior change (a brief window where a new transaction hasn't yet hit reports/VAT) touching every read path that currently assumes synchronous posting — needs deliberate buy-in, not a silent performance tweak.
8. **Confirm CPU/instance size isn't the next bottleneck** once the DB-side items above land — everything diagnosed so far points at the database; check DO's instance-level CPU metrics before assuming professional-s (2 vCPU/2GB) needs to change.
9. **Move off manual load-test scripts as the verification method** — every number in this section came from one-off scripted bursts. Real observability (DO's built-in metrics at minimum, or a proper APM) is needed for ongoing confidence at this scale rather than re-running a script by hand each time the question comes up.

### 29.4 Bulk synthetic-data seed generator

backend/scripts/run_bulk_seed.py + backend/scripts/bulk_seed/{tenants,ledger,verify}.py — built specifically to make the load testing above possible against realistic data volumes, and to make future performance verification repeatable. **Local/dev only, never production** — refuses to run (_check_target_is_safe()) against anything other than SQLite or a localhost/127.0.0.1 Postgres unless --allow-remote-host is also passed, in addition to always requiring --yes.

Generates N companies (default 100), each with real branches/employees/customers/suppliers/products and a configurable volume of sales+purchase transactions (default 5,000 each per company = 1M total) spanning N years of history (default 5) — through the real accounting invariants (balanced debit=credit journals, correct control-account mapping, one TaxLine per transaction), not just raw inserted rows.

**Why it replicates the posting pipeline instead of calling it**: create_gl_entries_from_journal()'s per-line running-balance aggregate (§5) makes calling the real per-invoice service functions in a loop non-viable at 1M-invoice scale — it's realistic but super-linearly slow as the ledger grows. The generator instead builds the same table shapes build_journal()/create_gl_entries_from_journal()/ensure_tax_line() would produce, but computes the GL running balance **in Python, incrementally, in chronological order per company** — provably equivalent given every seeded account's opening balance is 0 and every generated transaction is company-wide (branch_id=None, a deliberate simplification documented in ledger.py's module docstring, sidestepping the real function's per-branch running-balance partitioning). verify.py re-checks the result afterward: global debit=credit per company, and a spot-check of GeneralLedgerEntry.balance values against a fresh SQL recomputation.

Measured full-run performance (100 companies, 1M invoices, local SQLite, this machine): 85.7 minutes, producing an 8.16 GB database with 20,576,802 total rows. Per-company throughput degrades as the file grows (8.6→1.2 companies/min) — SQLite-specific overhead (index maintenance, WAL/page-cache pressure on a multi-GB file), not the O(1)-per-row generation logic; untested against a local Postgres instance, which may behave differently. This dataset (backend/taxflow-bulk100.db, git-ignored) is also what every fix in §29.2 was verified against before being trusted.

Admin login for any seeded company: admin{NNNN}@bulk.demo.taxflowapp.com (0001-0100) / Demo@12345.

## 30. HRMS Page Load Performance (2026-09-01)

A separate, narrower performance pass from §29 — specifically "HRMS feels slow to load," diagnosed live against the production admin account rather than a synthetic load test. Measured before/after with a Playwright probe hitting the deployed app directly.

### 30.1 Root cause

`hydrateFromServer()` (app.js) is shared verbatim between `index.html` (main dashboard) and `hrms.html` (HRMS portal) — it unconditionally fetched the full `GET /app-data` bootstrap blob on every page load, including hrms.html, which never renders products/sales invoices/quotations/accounts/purchase documents/bills/vendors/payments/expenses/ledger. Measured live: **972KB and ~4.1s** for that one fetch+parse, before any HR data even started loading.

Separately, `rotaAssignments` was the one HR-relevant AppDataRecord collection with **no cap at all** in `_BOOTSTRAP_COLLECTION_CAPS` (app_data.py) — every sibling collection (loans, leave, overtime, candidates) is capped at 500 — and had grown to 2,508 rows (~950KB) on the live account, alone accounting for >95% of even the HR-only portion of the payload.

A third, unrelated contributor: two separate `DOMContentLoaded` handlers had accumulated in hrms.html from different edits, both independently scheduling `go('hrms')` + `refreshHrmsKpis()` (a DOM table scan + an `/attendance/today` call each time) on overlapping timers — `refreshHrmsKpis()` alone fired up to 5x within the first 2.5s of every single page load.

### 30.2 Fixes shipped

- **`GET /app-data?scope=hrms`** (app_data.py) — new opt-in query param restricting the bootstrap response to HR-relevant collections only (`_hrms_scope_collections()`: employees, leave, attendance, rota, overtime, loans, recruitment, payroll, bank, notifications), intersected with — never widening — whatever an Employee principal's own role permissions already allow via the existing `_allowed_bootstrap_collections()`. Response cache key is scope-aware (`bootstrap:{company_id}:{scope}`) so a scoped and unscoped response can never cross-serve. `hydrateFromServer()` passes it when `window.HRMS_STANDALONE`, and skips the matching Sales/Purchase/Accounting render passes client-side too (not just relying on the server returning empty arrays). `login.html`'s employee-kind login redirect (`preloadAndRedirect('/hrms', t, 'hrms')`) passes the same scope for its bootstrap prefetch.
- **`rotaAssignments` added to `_BOOTSTRAP_COLLECTION_CAPS`, capped at 500** — same pattern and same "most recently created" ordering as every sibling collection. Documented caveat inline: unlike leave/loan requests (created close to the date they're about), rota assignments can be bulk-created for future weeks in one batch, so this cap could in theory make an older month's rota look sparse on the bootstrap-only path — the real long-term fix is a dedicated `GET /rota/assignments?month=YYYY-MM` endpoint mirroring how `leaveRequests` already moved off this blob (§7's Loans/Recruitment/Performance/Training/Assets Tier 2→Tier 1 migration note in hrms-architecture.md applies equally here). This cap benefits index.html too, since it isn't scope-gated.
- **hrms.html**: removed the duplicate `DOMContentLoaded` handler; the survivor's `refreshHrmsKpis()` schedule trimmed from 3 calls (900/2000/2500ms) to 2 (900/2500ms).

### 30.3 Measured result (same account, before/after)

| | Before | After |
|---|---|---|
| Bootstrap payload (HRMS page) | 972 KB | 187 KB |
| Bootstrap fetch time | ~4.1s | ~1.2s |
| Bootstrap payload (main dashboard, unscoped) | 972 KB | 280 KB (rotaAssignments cap alone) |
| `refreshHrmsKpis()` calls per HRMS page load | up to 5 | 2 |

Verified via a Playwright probe (`page.evaluate` timing a direct `fetch('/api/v1/app-data?scope=hrms')` call from within the loaded page) rather than trusting local reasoning alone — the actual bottleneck (`rotaAssignments`, not Sales/Purchase data as first assumed) only became clear after measuring the scoped response's own collection-by-collection byte breakdown.

## 31. "backup" module + Super Admin redesign (2026-09-21)

**`backup` module.** `ALL_MODULES` (`app/module_catalog.py`) gained `"backup"` — the company owner's own Download Backup. It is enforced server-side on `GET /app-data/db-dump` (`require_module("backup")`) and hides the Download Backup card in Settings > Backup & Audit (`data-module-gate="backup"`, same mechanism as AI/Exception Center). It is *not* in `BRANCH_ELIGIBLE_MODULES`. Super admin's own per-company / all-companies backup downloads never look at it. Toggle it per company in Superadmin > Module Permissions (listed as "DB Backup"), or for every company at once with `PUT /superadmin/modules/bulk` (`{module, enabled, company_ids?}`, other modules of each company untouched, audited per company; UI: "Module for all companies").

**Deploy backfill.** Existing companies already had explicit `modules_enabled` lists without `backup`, which would now read as "switched off". `ensure_schema_updates()` adds `backup` once to every explicit list, guarded by a row in the new `schema_flags` table so a later deliberate switch-off is never undone at restart (`ON CONFLICT DO NOTHING` — safe with several instances starting together). NULL/empty lists (= everything on) need nothing.

**Super Admin UI** now uses the main dashboard's design tokens (light default, dark toggle remembered in `localStorage.sa_theme`), a full-height sidebar with the logo, a topbar with the page title/theme toggle, tinted KPI cards, and an off-canvas sidebar under 860px. Also fixed while testing: modals opened from the company drawer (Edit User, confirms) sat *behind* it (`.overlay` z-index 200 < drawer 210/211 → now 230); the open drawer kept the stale user list after Disable/Edit/Remove; Client Errors / Trial Requests were only read at page load. `openResetPassword()` and `openSetExpiry()` (and their modals) have no caller — dead code; password reset is done via Edit User, expiry via Edit Company or "+7 Days / +1 Year".


## 32. Live QA audit fixes (2026-09-22 → 09-25)

A six-track Playwright audit (Sales/Purchases/Inventory, POS/Bank/Accounting, Corporate/Reports/Tax, HRMS+Biometric, ESS/Settings/Exceptions, Super Admin) produced ~45 findings; all but Related Party records (no backing table) are fixed (commits `327286b`, `9b1b3df`). Design consequences worth knowing:

**Access control (`app_data.py`).** `assert_collection_read_permission()` / `assert_collection_write_permission()` block roleless employees; `users` and `invoiceLayout` are admin-only (`_ADMIN_ONLY_COLLECTIONS`); HR collections need the matching module `:view`. `_guard_employee_write()` keeps stored values for sensitive employee fields (salary, IBAN…) unless the caller has `employees:edit`, and only `employees:edit` can create employees. Employee sync validates IBAN and rejects negative salary.

**Posting pipeline.**
- POS paid sales save a `payments` receipt `RCT-<invoice_no>` (`_record_pos_receipt`); credit sales don't.
- Sales invoices create `StockMovement` rows (`SALE-<no>`, types `sales_invoice`/`sales_return`) and consume valuation layers; POS-source invoices are skipped (POS has its own `sync_pos_stock`). Deleting the invoice deletes them.
- **Negative-stock gate:** `inventorySettings` record `key="config"`, `allow_negative_stock=false` (Inventory > Adjustments checkbox) makes both stock syncs raise 409 on insufficient tracked stock. Default is permissive.
- `lockedPeriods` saves upsert `PeriodLock`, so UI period locks are enforced server-side.
- Fixed-asset depreciation and accrual/prepayment release post balanced journals: `POST /corporate-accounting/fixed-assets/{id}/depreciate` (Dr 6100 / Cr 1510, capped at cost) and `POST /corporate-accounting/accruals-prepayments/{id}/release` (Dr 6200 / Cr 1300 or 2400, capped at total). Prepayment "remaining" = total − released.

**Reports.** Balance sheet includes a "Current period earnings" equity row (so it balances); P&L revenue/purchases are ex-VAT and include manual voucher expenses (excluding `PAY-JE` payroll entries); cash flow reads ledger cash; corporate-tax profit is recomputed from live net profit; summary text swaps "AED" for the company currency.

**Payroll.** Generation is read-only for loan/advance balances; **approval** (`POST /payroll/runs/{id}/approve`) drains them and audits. Future periods rejected; draft runs deletable; Bonus/Overtime/Deduction/Advance adjustments (with `employee_id` + `period_ym`) feed the run; `GET /payroll/runs/{id}/sif` builds the CBUAE EHR/SCR/ETR file from stored items. ESS payslips show only approved/paid runs.

**Companies / Super Admin.** Duplicate TRN → 409; `POST /companies/current/users` (admin only); superadmin create-company email is `EmailStr`; `/audit-logs` merges platform `AuditLog` rows; DB diagnostics reports `supported:false` on non-Postgres. SQL backup (`build_company_sql_dump`) now also dumps branches, journal entries/lines, stock mappings/movements and period locks via model reflection (users deliberately excluded — password hashes).

**Frontend.** Hardcoded "AED" labels use `data-currency-label` (regex updater) or `currentCurrency()`; purchase-form totals show the company currency; `moduleApi` formats 422 array details; fake success toasts removed (Mark All Read, Role Duplicate, integration test, rota, job board); Inventory > Adjustments tab.

**Known unrelated flakes:** `test_team_today_shows_present_absent_and_leave_within_the_same_department` (weekday-dependent) and `test_attendance_trend_period`.

**Documentation surfaces.** `STORYBOARD.md` (feature-by-feature functionality), `docs/storyboard.md` (screen layouts), `frontend/public/taxflow/handbook.html` (user handbook served at `/handbook`; screens `M.*` main app, `H.*` HRMS, `E.*` ESS; HRMS sidebar has a Handbook entry opening `/handbook#part-hrms`), `docs/hrms-architecture.md` §11 (OT cool-off/eligibility). Update all of them when a user-visible screen changes.

**Frontend build.** After editing `app.js`/`ess.js`, run `cd frontend && npm run build:min && npm run inject-versions` — HTML pages load the `.min.js` bundles with version query strings.

## 33. Voice (Phase 1 — AI Assistant, 2026-09-27)

Voice is a thin input/output layer over the existing AI Assistant; it never writes data.

- **`src/voice.js`** (plain script, loaded by `index.html` before `app.js`; versioned by `inject-versions`, not minified) exposes `window.VoiceInput` / `window.VoiceOutput`. Input prefers the browser's `SpeechRecognition`; otherwise (or on a browser STT network error) it records with `MediaRecorder` (max 60 s) and calls a caller-supplied `transcribe(blob, lang)`. Output uses `speechSynthesis`. Language (`en-US`/`ar-AE`) and mute live in `localStorage` (try/catch).
- **AI panel wiring** (`app.js`, `toggleAIVoice()` and friends above `askSystemAI()`): transcript → `#system-ai-question` → `askSystemAI()`; a spoken question sets `answer_lang: "ar"` when asked in Arabic, and its answer is spoken.
- **`POST /ai/transcribe`** (`routers/ai.py`, under `require_module("ai")`, 15/min): multipart `file` + optional `lang`; 422 empty, 413 over 5 MB; forwards to OpenAI (`OPENAI_TRANSCRIBE_MODEL`, default `gpt-4o-mini-transcribe`) via `ai_client.transcribe_audio()`; 503 when no key or the provider fails. Audio is never stored.
- **`AIAssistRequest.answer_lang`** (`"en"`/`"ar"`) appends an "answer in Arabic" instruction to the `/ai/assist` prompt.
- Tests: `backend/tests/test_voice.py` (STT/LLM mocked).
- Not yet: voice data entry, ESS/POS voice, per-company voice settings and daily transcription cap (Phases 3–5 of the voice plan).

### 33.1 Voice commands (Phase 2)

- **Catalog, client side** (`buildVoiceCatalog()` in `app.js`): visible `.sb .nav` items (pages; `window.open` items skipped), the `.tabs .tab` / `.rep-nav-item` entries of pages reachable from a visible nav, and each such page's `META[page]` top action **only when its label starts with "+"** (blank create form). Ids are per-request (`p1`, `t7`, `a3`) mapped to closures that click the real element, so every existing guard (`_hrmsNavAllowed`, `_moduleNavAllowed`, `goHrmsTab`) still runs.
- **`POST /ai/voice-intent`** (`routers/ai.py` → `app/voice_intent.py`, principal auth so HRMS employee logins work, `require_module("ai")`, 30/min): `{transcript, lang, targets[≤400]}` → `{intent: navigate|open_form|search|answer|unknown, target, query, confidence, alternatives, source}`. A rule matcher (verb stripping + difflib/token overlap) answers alone at ≥90; otherwise `call_llm` (`OPENAI_VOICE_MODEL` / `ANTHROPIC_VOICE_MODEL`). `sanitize()` drops any LLM reply whose target isn't in the list, whose kind doesn't fit the intent, or that picks search/answer without search words / a question — then the rule result is used. Reads no company data.
- **Dispatcher** (`runVoiceCommand()`): confidence ≥60 → run target (search fills the first visible search/filter input on the page); `answer` → AI Assistant with the question (HRMS: message + page chips); otherwise "Did you mean…" chips. Confirmed commands are written to the local audit trail. Topbar `#vc-mic-btn` (index: `data-module-gate="ai"`; hrms: hidden in `applyModulePermissionNav`), Ctrl+Space / Esc.
- `/ai/transcribe` now also uses principal auth (HRMS server-STT fallback).

### 33.2 Voice data entry (Phase 3)

- **`POST /ai/voice-draft`** (`routers/ai.py` → `app/voice_draft.py`, principal auth, `require_module("ai")`, 15/min): `{form: expense|purchase|sales_invoice|customer|vendor, transcript, lang, choices, today}` → `{fields, lines, unmatched}`; 503 when no AI key. Writes nothing (test asserts no `AuditLog` rows).
- `FORMS` declares each form's fields and types. `sanitize_draft()` drops unknown keys and coerces: `money` (0 – 1e9, 2 dp), `date` (ISO), `trn` (exactly 15 digits), `email`, `phone`; `choice` fields must fuzzy-match a client-sent option (else reported in `unmatched`), `name` fields match when close and otherwise keep the spoken text. Lines (≤30) match products the same way and carry `matched: yes|no`.
- Only strict `choice` lists are sent to the LLM; names/products are matched server-side so an unknown item is never swapped for a lookalike.
- **Frontend** (`_VF_FORMS` / `voiceFill()` / `applyVoiceDraft()` in `app.js`): choices come from the form's `<select>` options and the `_AC_SOURCES` autocomplete lists; values are set with real `input`/`change` events so existing handlers (customer TRN/address fill, product price, totals) run; filled inputs get `.vf-filled` until the user edits them. Dictate buttons carry `data-module-gate="ai"`. No audit entry for drafts — the form's own Save logs the real action.

### 33.3 HRMS & ESS voice (Phase 4)

- **HRMS quick answers**: `_VC_HR_QUERIES` in `app.js` adds `kind: "query"` targets to the voice catalog (only when the matching `.sb .nav[data-staff-nav]` entry is visible). `/ai/voice-intent` gained intent `query` (must name a query target). Running one calls `goHrmsTab()` and answers from the endpoints that screen already uses — `/attendance/today` + `/leave/requests` (absent = active directory rows not checked in and not on approved leave), `/leave/requests` (pending), `/attendance/overtime-eligibility` for Monday→today (eligible, not yet requested) — so RBAC and department scoping are unchanged. Answers are spoken via `VoiceOutput`.
- **ESS**: `POST /ess/voice-intent` (ESS bearer token, company `ai` module required → 403, 20/min) → `app/ess_voice.py`: rules first (balance / next shift / page words answer with no LLM call), LLM only for request forms. Intents: `navigate{page}`, `apply_leave`, `request_overtime`, `request_correction`, `request_advance`, `request_loan`, `leave_balance`, `next_shift`, `unknown`. `sanitize()` enforces the same windows the submit endpoints use (OT date within 31 days and not future, corrections within 60, leave within a year, hours 0.25–12, HH:MM times, known leave / OT / loan types). No employee id is accepted — balance and next shift reuse `ess_leave_balance()` / `ess_rota()` for the token's own employee. `POST /ess/voice-transcribe` is the ESS twin of `/ai/transcribe`.
- ESS frontend (end of `ess.js`): `essVoice()` / `essRunVoice()` open `essOpenLeaveModal()` / `essOpenRequestModal()` pre-filled; the employee submits. The mic hides itself after a 403 (module off).

### 33.4 POS voice order (Phase 5)

- `/ai/voice-draft` form `pos_cart` (lines only; spoken prices are discarded server-side). `pos.html` loads `src/voice.js` (now versioned by `inject-versions` too) and sends `_products` names as the product choices.
- `posVoiceParse()` shows a confirm panel (`#pos-voice-modal`); `posVoiceConfirm()` adds each checked line through `addToCart()` — with `window._posRendered` pointed at the full product list for that call so a filtered grid can't shift the index — then `setQty(id, before + qty)`. Unmatched products can't be added.

### 33.5 Voice settings & cost guard

- `app/voice_settings.py`: per-company settings in `AppDataRecord` (`collection="voiceSettings"`, `record_key="company"`) — `enabled`, `default_lang` (`""|en-US|ar-AE`), `cloud_transcription`, `daily_transcriptions` (default 200) — and a per-day counter (`collection="voiceUsage"`, `record_key=<ISO date>`). No migration.
- `GET/PUT /ai/voice-settings` (PUT admin-only, audit-logged `voice_settings_saved`); `GET /ess/voice-settings` for the portal. `require_voice_enabled()` guards `/ai/voice-intent`, `/ai/voice-draft`, `/ess/voice-intent`; `reserve_transcription()` guards both transcribe endpoints (403 when voice or server STT is off, 429 over the daily cap) and counts the request before calling the provider.
- Frontend: `applyVoiceSettings()` (app.js, main + HRMS) sets `body.voice-off` (CSS hides `#vc-mic-btn`, `.vf-btn`, `#ai-voice-controls`) and `VoiceInput.setCompanyLang()`; POS and ESS hide their mic the same way. Settings tab `#set-voice` (`loadVoiceSettingsForm()` / `saveVoiceSettingsForm()`).
- Privacy: audio is forwarded to the STT provider and discarded; transcripts go to the LLM only to resolve the command/draft; voice drafts are not audit-logged (the user's own Save is), confirmed voice navigation/questions are logged locally with a 60-character excerpt.

## 34. ESS phone app layout (2026-09-27)

- Pure presentation layer over the existing ESS page, active at `max-width: 760px` only (`ess.css` "Mobile app layer"): `.m-only` sections in `ess.html` (`.m-home` in `#tb-dashboard`, `.m-att` in `#tb-attendance`, `.m-me` in `#tb-profile`, `.m-tabbar`), sidebar/top bar hidden, modals become bottom sheets.
- `ess.js` "Mobile app layer": `essLoadMobile()` (called from `showApp()`) fills the phone views from `/ess/me`, `/ess/leave-balance`, `/ess/requests`, `/ess/announcements`, `/ess/rota`, `/ess/attendance` and `/hr/dashboard`; `essSyncTabbar()` is called from `essTab()` so every navigation path keeps the tab bar in sync; `essMobileCheck()` wraps the existing `gpsCheckIn()` / `gpsCheckOut()` (check-out confirms first). Leave-type chips drive the real `#leave-type` select.
- `/hr/dashboard` now includes the caller's own `checked_in`, `check_in_time`, `check_in_at` (ISO UTC) and `today_sessions` for **every** role (`_own_session_status()` in `hr_access.py`) — previously Administrator / HR Manager / Manager dashboards omitted them, so their GPS check-in page always showed "Not checked in". Tests: `test_ess_mobile_status.py`.
- The ESS start-up block (`showApp()` for a remembered session) now sits at the very end of `ess.js` so everything it calls is defined first.
- PWA: `ess-manifest.json` (start `/ess`, standalone, theme `#2563eb`), `ess-icon-192/512.png`, iOS `apple-mobile-web-app-*` meta; the Me menu offers `beforeinstallprompt` on Android/Chrome and Share-sheet instructions on iOS. No service worker (not required for install on current Chrome; avoids stale-cache issues).

## 35. AI Assistant answers from live company data (2026-09-28)

- `app/ai_context.py` `build_ai_context()` builds a compact JSON snapshot for `/ai/assist` from the cached report builders (`reports._build_dashboard` / `_build_summary`, 60s / 120s cache): totals to date, P&L, monthly revenue/VAT (12 months), invoices by status field, `receivables_summary` (past due by **due date**, from AR aging — the status-field "overdue" bucket only counts invoices explicitly marked overdue), AR/AP aging top 10, top customers/suppliers, working capital, health score, VAT position + next return due date (calendar quarters, `voice_briefing.next_vat_due`), staff snapshot and by department, low stock (via `inventory.list_stock_levels`), open exceptions.
- The system prompt restricts answers to that data (no invented numbers; say which report has it otherwise), explains negative VAT = refund and monthly vs to-date figures, and asks for formatted amounts. Works for spoken and typed questions and Arabic (`answer_lang`).
- Without an AI key, `rule_answer()` answers common questions (profit, receivables/overdue, VAT, revenue, staff, stock, suppliers) directly from the snapshot. Tests: `test_ai_data_answers.py`.

### 35.1 Daily briefing

- `GET /ai/briefing?lang=en|ar` (admin user, `ai` module, 20/min) → `app/voice_briefing.build_briefing()`: `{intro, items:[{key,page,text}], text, lang, vat_due}` from the cached dashboard plus `ai_context` `receivables_summary` (past due by due date). VAT due = quarter end + 28 days (calendar quarters). No LLM.
- Frontend: `playDailyBriefing()` (app.js) shows the items in the voice popup (`#vc-pop-body`) with page chips and speaks `text`; `#dash-btn-briefing` on the dashboard header and a `Daily briefing` voice query target (main app only). Tests: `test_daily_briefing.py`.

