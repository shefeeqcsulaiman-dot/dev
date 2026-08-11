# TaxFlow Functional Specification

Companion to `docs/design-system.md` (visual design) and `STORYBOARD.md` /
`docs/storyboard.md` (screen-by-screen walkthrough). This document goes one
level deeper: **every field**, **every action/function**, **every business
rule/condition**, and **the step-by-step workflow** for each module — enough
for a developer to rebuild the same application's behavior, not just its look.

Organized by module, in the same order as `STORYBOARD.md`.

> **Honesty note:** this document reports what the code actually does, not
> what the marketing copy or `STORYBOARD.md` claims — several real gaps
> between the two are called out explicitly below (e.g. Payroll's admin UI
> not being wired to the real payroll API, ESS having only 3 tabs not 6,
> several HRMS-extension pages being UI-only placeholders). A rebuild should
> treat these as decisions to make, not bugs to silently replicate.

---

## 0. Architecture Note (applies to every financial/operations module below)

Almost all business data (invoices, purchases, expenses, journal drafts, bank
accounts, customers, vendors, stock mappings, etc.) is persisted through one
generic endpoint implemented by `backend/app/routers/app_data.py`:
```
POST /api/v1/app-data?action=save        body: {collection, record}
POST /api/v1/app-data?action=bulk-save   body: {collection, records[]}
POST /api/v1/app-data?action=delete      body: {collection, record}
GET  /api/v1/app-data                    (bootstrap — loads all collections)
GET  /api/v1/app-data/records/{collection}
```
Frontend wrapper: `saveServer(collection, record)` (`app.js:3530`) /
`deleteServer` (`3562`). Each save also runs `sync_domain_model()`
(`app_data.py:880`), which mirrors certain collections into strongly-typed
relational tables (`Invoice`, `Account`, `Employee`, `StockProductMapping`,
`SourceTransaction`) — this is how a JSON blob saved from the UI becomes a
real accounting/inventory posting.

A **separate, fully-typed REST API** exists for accounting-grade objects
(`/accounts`, `/journal`, `/vouchers`, `/payments`, `/receipts`,
`/bank-accounts`, `/tax/codes`, `/tax/vat-returns`,
`/tax/corporate-tax-returns`, `/inventory/*`) with real Pydantic validation,
409/422 conflict handling, and period-lock enforcement. Some of these (e.g.
`TaxCode`, `StockAdjustmentApproval`, `Warehouse`, `ItemUnit`) are
**backend-only — not yet wired to any UI control** in the current build;
called out per module below.

Record-key uniqueness per collection (used for de-dup/upsert),
`app_data.py:114`:

| Collection | Unique key field |
|---|---|
| salesInvoices | invoice_no |
| quotations | quote_no |
| purchaseRecords | ref |
| bills | bill_no |
| customers / vendors | name |
| accounts | code |
| bankAccounts | iban |
| expenses / payments | ref |
| corporateTax | period |

---

## 1. Dashboard

**HTML:** `index.html:158-884` (`#page-dashboard`). **JS:**
`renderFullDashboardFromDatabase`, `renderDashboardHero`, `renderDashVatRing`,
`renderMonthlyRevenueVat`, `renderRecentActivity`, `renderTopCustomers`,
`renderInvoiceStatus`, `renderCfoRecommendations` (`app.js:2412-2969`).
**Backend:** `GET /api/v1/reports/dashboard` (`reports.py:51`, built by
`_build_dashboard` at line 63).

Read-only KPI/analytics screen — no form fields. Key elements: Revenue card
(2-slide flip incl./excl. VAT), Purchases card (2-slide flip), Net Profit
card (2-slide flip), Liquid Position card (Cash & Bank / Receivables /
Payables), CFO Recommendations (narrative text generated from the same
`_build_dashboard` numbers, not a live LLM call on this particular card),
Revenue vs VAT monthly bar chart.

**Business rules:**
- All totals are computed **server-side** from real DB tables (`Invoice`,
  purchase records, `GeneralLedgerEntry`), not by re-summing client arrays.
- "Paid" detection treats any status containing "paid" as collected.
- Response is cached and invalidated whenever a record in
  `_REPORT_AFFECTING_COLLECTIONS` is saved/deleted.

**Workflow:** Load Dashboard → `GET /reports/dashboard` → server aggregates
Invoice/purchase/GL/TaxLine data → JSON KPI payload →
`renderFullDashboardFromDatabase()` populates every card/chart → user clicks
any quick-link to jump into the underlying module.

---

## 2. Sales & Invoices

**HTML:** `index.html:955-1245` (`#page-sales`), modals `#m-sales-view`,
`#m-invoice-share`, `#m-customer`. **JS:** invoice-flow functions throughout
`app.js:7527-9970`. **Backend:** `invoices.py` (typed API),
`app_data.py sync_sales_invoice()` (line 1178) for the JSON-collection path
actually used by the UI.

**Tabs:** Upload Invoices → AI Extraction → Validation → Invoices (register)
→ Add Sales (opens a choice modal, not a tab body) → Customers.

### 2.1 Create Invoice fields

| Field | Type | Required | Validation |
|---|---|---|---|
| Invoice No. | text (mono), HTML `required` | **required** | auto-generated `INV-<year>-<5 digits>` if blank; re-checked non-empty at save |
| Invoice Date | date, HTML `required` | required | drives due-date calc and **period-lock check** |
| Due Date | date | optional | manual |
| PO Number / Delivery Note No. / Reference No. | text (mono) | optional | free text |
| Customer | text + datalist, HTML `required` | **required** | rejects empty or literal `'Customer'`; selecting a saved customer auto-fills TRN/address |
| Customer TRN | text (mono) | optional | placeholder "15-digit TRN"; **no live digit-counter on this field** (unlike Settings TRN) |
| Billing Address | text | optional | — |
| Line items | product(datalist)/unit/qty(number `min=0.01 step=0.01`)/price(number `min=0 step=0.01`)/amount(readonly) | **≥1 valid line** | needs non-blank description, `qty>0`, `price>=0`, `amount>0` |
| Payment Terms | select | optional | Net 30/15/60/Due on Receipt |
| Currency | select | optional | AED/USD/EUR |
| Notes/Terms | textarea | optional | — |

Live calc: `calcLine()` recomputes line amount = qty×price → subtotal →
**VAT = subtotal × 5% (hard-coded)** → total, mirroring every field into a
live-preview sidebar in real time via `updateSalesInvPreview()`.

**Add Customer modal:** Customer Name* (required), TRN (`maxlength=15`,
format hint only), Emirate (select), Address, Email, Phone.

### 2.2 Actions

| Action | Function | Effect |
|---|---|---|
| Upload & Extract | `salesUpload(input)` → `documents.extract`/`invoices.import` action | Background OCR/LLM extraction, auto-navigates to AI Extraction tab |
| Save All Selected | `validateSalesAiInvoice()` per row | Moves valid rows to Validation tab |
| **Save & Send** | `saveAndSendDraftInvoice()` → `saveDraftInvoice()` | Validates → period-lock check → persists → opens Share modal |
| Full Preview | `openDraftInvoicePreview()` | Renders using the active Invoice Design layout |
| Mark Paid | `openReceiptForInvoice()` | Opens Bank→Receipts modal pre-filled with invoice amount/customer |
| Share | Email/WhatsApp (`wa.me` link)/Download PDF/Copy Link | — |
| Edit / Delete | row buttons | Delete cascades `sync_domain_delete`, also removing the mirrored `Invoice` row |

### 2.3 Business rules
- **Invoice number uniqueness is enforced at the typed-API layer**: `POST /api/v1/invoices` → 409 if duplicate. The JSON-collection save path used by the UI only shows a soft warning, no hard block.
- **VAT is always 5%, hard-coded** in the Create Invoice line calc (the typed `InvoiceLineIn` schema defaults `vat_rate=5` but accepts any `ge=0` value if posted directly).
- **Period lock**: `isPeriodLocked(date)` blocks save client-side before any server call if the invoice's month is locked.
- **Invoice total must be > 0** — an invoice with only zero-amount lines cannot be saved.
- Sales Return documents reuse the exact same form, swapping labels/prefix (`SR-` vs `INV-`).

### 2.4 Workflow — Create & Save Invoice
1. Sales & Invoices → Add Sales → Sales Invoice.
2. Fill fields/lines — every keystroke live-updates the summary sidebar; selecting a saved Customer auto-fills TRN + Address.
3. **Save and Send** → client validation → period-lock check → writes to the on-screen table + POSTs to the generic store → backend creates/updates the relational `Invoice`+`InvoiceLine` rows and a linked `SourceTransaction`.
4. Success toast → Share modal opens automatically.
5. Invoice appears in the list with a status badge and row actions (View/PDF/Share/Edit/Mark Paid/Delete).

---

## 3. Quotations

**HTML:** `index.html:1245-1299`. **JS:** `saveQuotationDraft`,
`sendDraftQuotation`, `convertQuotation` (`app.js:20934-21006`).

| Field | Required | Notes |
|---|---|---|
| Quotation No. | auto-generated | `quotationNumber()` |
| Customer | soft default `'New Customer'` | auto-fills on selection |
| Date / Valid Until | soft defaults (today / +15 days) | — |
| Subject | optional | — |
| Line items | implied by totals calc | same structure as invoice lines |

**Actions:** Save Draft · Preview · Share · Send (status→'Sent') ·
**Convert to Invoice** — marks the quotation `Converted`, generates a new
invoice number (`INV-<suffix from quote_no>`), copies customer/subtotal/VAT/
total/lines into a fresh **Draft** sales invoice — one-click promotion, no
re-entry, and the quotation's own data is never mutated beyond its status.

**Business rules:** Quotation and invoice share the same line-item structure
and layout engine (Quotation Design can "inherit" Invoice branding, on by
default). `record_key` for quotations is `quote_no` — saving the same number
again **overwrites** the previous quotation (upsert), it does not duplicate;
there's no hard 409 uniqueness check like invoices have.

---

## 4. Point of Sale (POS)

Separate single-page app (`pos.html`) — own auth gate, no dependency on the
main `app.js`. Opens in a new tab from the topbar "POS Terminal" button.

| Field | Type | Required | Validation |
|---|---|---|---|
| Customer name | text | default `'Walk-In Customer'` | Credit sale requires a real (non-default) name |
| Service Type / Table / Staff | select | optional | — |
| Cart qty (per line) | number `min=1` | — | qty→0 auto-removes the line |
| Discount / Order Tax / Shipping / Packing | number `min=0 step=0.01` | optional | feed `cartTotals()` |
| Cash Tendered | number | required for Cash | **Complete Sale disabled until tendered ≥ total** |
| Split cash/card | number | must sum to total | **Complete Sale disabled until remaining ≤ 0** |
| Add Customer / Add Product modals | text/number | Name (+Code+Price for products) required | — |

**Actions:** tap product tile → add to cart; Cash/Card/Split → payment
modal; **Complete Sale** → `completeSale()` validates tender → builds sale →
saves to `posSales` **and** creates a mirrored Tax Invoice in `salesInvoices`
**and** posts a `StockMovement` (negative qty) per cart line → printable
receipt. **Credit** → same but `Pending` invoice status, requires a real
customer name, no tender check. Save as Draft/Quotation/Hold; Cancel/Clear;
Refresh products; on-screen Calculator.

**Business rules:**
- Cash payment cannot complete unless `tendered ≥ total − 0.001` (float-safe).
- Split payment cannot complete unless `cash+card ≥ total`.
- Credit sale requires a customer name other than the Walk-In default.
- VAT per line derives from the product's own rate; mixed-rate carts show a combined VAT label.
- **Every completed/credit sale writes both a Sales Invoice record and stock movement rows** — POS sales flow into Sales *and* Inventory automatically, unlike manual invoices which don't touch inventory directly.
- POS products are the exact same `products` collection as Inventory's Item Master — no separate POS catalog.

**Workflow:** Open POS → auth check → load products → build cart → choose
payment method → (Cash) enter tender or quick-amount chip → Complete Sale →
`posSales` + mirrored `salesInvoices` + `stockMovements` all update → receipt
modal → Print/WhatsApp/Email or start a new sale.

---

## 5. Purchasing

**HTML:** `index.html:1299-1635`, tabs: Upload Documents → AI Extraction →
Purchase Records → Local PO → Foreign PO → Vendors → Purchase Settings.
**JS:** `saveManualPurchase` (`12823`), `calcManualPurchase` (`12735`).
**Backend:** `app_data.py sync_purchase_stock`/`purchase_line_stock_mapping`
(935-1086), `inventory.py backfill_purchase_stock_movements`.

### 5.1 Manual Entry fields

| Field | Type | Required | Validation |
|---|---|---|---|
| Supplier | select, HTML `required` | **required** | toast + abort if empty |
| Reference No. | text (mono) | auto-generated (`PUR-`/`PRET-`/`LPO-`/`FPO-` per doc type) | — |
| Purchase Date | `datetime-local`, HTML `required` | required | — |
| Address | text | optional | auto-filled from supplier |
| Pay Term | select | optional | Due on receipt/Net 15/30/45 |
| Attach Document | file | optional | `.pdf,.csv,.zip,.doc,.docx,.jpeg,.jpg,.png` |
| Line items | qty/cost/discount%/before-tax/line-total | **≥1 valid line** | rejects if no product name, `quantity<=0`, or `unit_cost<0` |
| Discount Type/Amount | select + number | optional | feeds `calcManualPurchase()` |
| Purchase Tax | select (None/VAT 5%/Reverse Charge 5%/Exempt) | — | 5% applied only if selected and not exempt |
| Shipping | number | optional | added to total |
| Payment Amount / Paid On / Method / Account / Note | mixed | optional | drives Paid/Due split → status `Paid` if due≤0 else `Pending Payment` |

**Vendor Directory:** Vendor Name* (required), TRN (`maxlength=15`),
Category (select), Email/Phone, Address (textarea).

### 5.2 Actions
Upload & Extract (background OCR/LLM — Amazon-tax-invoice parser, columnar
PDF parser, OCR fallback, OpenAI/Claude vision parser); Save All Selected
(validates + flags `already_in_db` duplicates); Save (Manual) → creates a
`SourceTransaction` **and** posts `StockMovement`+`InventoryValuationLayer`
rows per line; Edit; View original document image; Delete (cascades linked
`SourceTransaction`/`JournalEntry`/`GeneralLedgerEntry`/`TaxLine`/
`StockMovement`/`InventoryValuationLayer`).

### 5.3 Business rules
- **Supplier and Purchase Date are hard-required.**
- **Every line needs** a non-blank product name, `quantity > 0`, `unit_cost >= 0`.
- **Duplicate reference detection**: an AI-extracted invoice number matching an existing purchase record key is flagged `already_in_db: true`; surfaces later in the Exception Center as "Duplicate invoice" (high severity) if it slips through.
- **Purchase posting cascade**: saving a purchase record creates/replaces both the `SourceTransaction` (accounting) and `StockMovement`/`InventoryValuationLayer` (inventory) in the same operation — deleting the reference cleans up both sides.
- **Auto product-mapping creation**: an unrecognized SKU/name on a purchase line auto-creates a `StockProductMapping` on the fly — this is how "unmapped stock item" exceptions get resolved once real mapping data (accounts, tax code) is filled in.
- **Weighted-average cost** recomputed across all purchase movements for a mapping on backfill.
- **Demo-data detection**: purchase records matching known seed-data patterns are excluded from stock backfill, so a fresh company's inventory isn't polluted by demo data.

---

## 6. Inventory

**HTML:** `index.html:2143-2214` — only **3 tabs in the current build**:
Stock Dashboard, Item Master, Stock Mapping.

> ⚠️ This differs from `STORYBOARD.md`'s described Warehouses/Stock
> Movements/Adjustment Approvals/Valuation/Units & Conversions sub-pages —
> those exist only as **backend models + REST endpoints**
> (`/warehouses`, `/item-units`, `/item-unit-conversions`,
> `/inventory/adjustment-approvals`, `/inventory/valuation-layers`) that are
> **not currently exposed by any button/form** in the frontend.

**Item Master fields:** Item Name* (required), Item Code (auto-generated,
duplicate-checked), Description, Type (Stock/Service/Consumable/Fixed
Asset/Raw/Finished), Category* (required, from DB list), Unit* (required,
from DB list), Cost Price, Selling Price, VAT (Standard 5%/Zero/Exempt),
Stock Tracking, Opening Date, Reorder Level/Min/Max Stock, Supplier, Status.

**Stock Mapping fields:** Display Name (with a "Suggest Name" auto-cleanup
heuristic, not AI), Supplier Name, TaxFlow Name (canonical), Units/Outer
(pack-size conversion), Cost/Markup %/Tax Rate/Exclude VAT/Price per Outer
(live-computed selling price from cost×(1+markup%) + VAT).

**Actions:** Add/Save Item; **Clear Table** (`DELETE /inventory/stock-levels`
— wipes all stock movements/valuation layers/mappings/products and the
"backfill disabled" marker so future purchases regenerate movements); Add/
Save Mapping (typed API, upsert by SKU).

**Business rules:**
- **Backend-only, not exposed in UI**: `StockAdjustmentApprovalIn` enforces `quantity_delta != 0` and a non-blank reason (matches `STORYBOARD.md`'s adjustment-approval rule) — fully implemented server-side, but no frontend button calls it yet.
- **`ItemUnitConversionIn.conversion_factor` must be `> 0`** — same story, backend-only.
- Item name/code uniqueness is enforced **client-side only** (table comparison), not a server constraint.
- **FIFO valuation** layers are created per purchase line (quantity_in/remaining/unit_cost) — the structure supports FIFO costing but no Valuation Report tab exists yet.
- Low-stock detection compares current stock against each mapping's `reorder_level`.

---

## 7. Expenses

**HTML:** `index.html:2217-2359`, tabs: AI Upload → New Expense → Approvals
→ Expense List. **JS:** `calcExpenseTotal`, `saveExpense`,
`setExpenseApprovalStatus`.

| Field | Type | Required | Validation |
|---|---|---|---|
| Date | date | defaults to today | — |
| Category | select | — | Supplies/Transport/Utilities/Travel/Entertainment/Communication/Maintenance |
| Vendor | text + datalist | optional | populated from `GET /vendors` |
| Description | text | **required** | toast + abort if blank |
| Amount (AED) | number (mono) | **required, must be > 0** | blocks save if `<=0` |
| VAT Amount | number (mono) | optional | live-sums into Total |
| Total Amount | readonly | computed = amount + vat |
| Receipt upload (AI Upload tab) | file, multiple | `.pdf,.jpg,.jpeg,.png` |

**Actions:** Extract Pending (per-file background OCR, progress bar,
extracted rows as review cards); Save (validates → **period-lock check** →
persists → switches to Expense List); Save as Draft; Approve/Reject
(Approvals tab, only for `Pending` status) updates badge + totals everywhere.

**Business rules:** Description and Amount>0 are hard-required; period-lock
blocks save exactly like Sales/Journal/Manual Purchase; statuses flow
Draft → Pending → Approved/Rejected; only Pending expenses appear in the
Approvals queue.

---

## 8. Bank & Payments

**HTML:** `index.html:1635-1722` — the separate Payments page (Received/
Paid) is **merged into the Bank page at runtime**
(`mergeBankAndPaymentsModule()`), appending Receipts/Payments tabs onto the
Bank tab bar. **Backend:** `accounting.py`.

**Add Bank Account:** Bank Name (select, 8 UAE banks), Account Holder Name*
(required), IBAN (used as the record's id if present), Account Type
(Current/Savings), Currency, Opening Balance, Swift Code/Branch. Typed
backend equivalent requires `account_id` reference an existing company
Account (422 if not found).

**Payment/Receipt modal (shared):** Client/Contact* (required to allocate),
Date/Time, Method (Cash/Bank), Amount (**must be > 0**, backend
`Field(gt=0)`), Reference, Bank Account (required if Bank), Comments,
allocation table (checkboxes per open invoice/bill with a live Balance/
Allocated/✓-Balanced indicator).

**Actions:** Add Account; Upload statement/Add line (prompt-based entry);
Match / **Auto-Reconcile** (exact-amount pairing) / Unmatch; **Finish
Reconciliation** (blocked if unmatched difference > AED 0.01); Mark Paid
(from Invoice/Bill, pre-fills the shared modal); Save Payment/Receipt
(typed API auto-creates a matching Voucher and **immediately posts it to
the GL** if `post:true`).

**Business rules:**
- Payment/Receipt amount must be `> 0` (422 otherwise).
- Bank ledger account must belong to the company (422 otherwise).
- **Period lock applies to payments/receipts too** — HTTP 423 if the period is locked.
- Reconciliation completion blocked if difference exceeds AED 0.01.
- Auto-numbering: `PAY-00001`, `RCT-00001`.
- WPS/SIF lives under the **Payroll** module's own tab, not here (despite the storyboard describing it under Bank & Payments).

---

## 9. Accounting

**HTML:** `index.html:2359-2774`, tabs: Chart of Accounts → Voucher Type →
General Ledger → Payments/Receipts → Statutory Filing → Bank Reconciliation
→ Recurring Journals → Period Lock. **Backend:** `accounting.py` (full
typed API).

**Chart of Accounts fields:** Account Code* (client: must not already exist
for new accounts), Account Name*, Type (Asset/Liability/Equity/Revenue/
Expense — drives auto normal-balance), Parent Group (**required unless
creating a Group**), Opening Balance (forced 0 for Groups), Opening Balance
Type (DR/CR), Normal Balance (auto-derived, editable), Tax/VAT applicable,
Is Bank/Cash.

**Journal Entry fields:** Date* (required), Voucher No.* (auto-suggested but
must be non-empty), Voucher Type, Narration* (required), Journal Lines
(**≥2 lines**, every line needs an account, **no line may have both debit
and credit**, **total debit must equal total credit ±0.01**) — 4 explicit
client checks before `POST /journal`, which re-validates exact balance +
that every account belongs to the company + period-lock.

**Actions:** ✨ AI Suggest (client-side keyword heuristic, **not a backend
LLM call** — see §16.6); Post to Ledger; Save Draft (unposted, no GL
impact, still respects period-lock); **Reverse** (server creates a
mirror-image `REV-<original>` entry, blocks double-reversal); Delete
Journal (cascades linked GL entries); Add Account/Sub-Ledger/Group; Delete
Account (409 if it has children or is used in a journal line); Clear All
Accounts (deletes every account not referenced by any journal line);
Lock/Unlock Period.

**Business rules:**
- **Double-entry balance rule**: every voucher/journal must have `sum(debit)==sum(credit)` — 422 otherwise.
- A single line cannot carry both a debit and a credit amount.
- Minimum 2 lines per journal entry.
- Every account on a line must belong to the current company.
- **Account hierarchy max depth = 4** (Primary Group → Secondary Group → Sub-Group → Ledger); level-4 accounts are always posting ledgers.
- Cannot post under a non-group parent (400).
- Cannot delete an account with children or one used in a journal line (409 either way).
- **Period lock**: any voucher/payment/receipt/journal dated in a locked period raises **HTTP 423**.
- Voucher approval workflow: `approval_required=True` vouchers start `pending_approval`; approving re-validates balance+period then posts the real journal+GL rows.
- Cannot reverse a reversal, or reverse the same journal twice.
- **AI ledger generation** (`/accounts/ai-generate`/`/accounts/ai-approve`) — LLM or rule-based fallback proposes a chart-of-accounts tree from a prompt; approval re-validates duplicate codes/missing name-or-code/max depth before persisting.

---

## 10. Corporate Accounting

**HTML:** `index.html:2774-2817` — thin shell; most sub-sections (Fixed
Assets, Accruals, Cost Centers, Budgets, Cash Flow, Credit Control,
Consolidation, Approvals) are **DOM nodes physically relocated at runtime**
from the Accounting page (`separateCorporateAccountingModule()`).
**Backend:** `corporate_accounting.py` (read-only summaries) +
`tax.py /corporate-tax-returns` (the actual writable CT return).

**Corporate Tax Return fields:** Accounting Net Profit (pre-filled from the
latest P&L if blank), Non-Deductible Expenses (added back), Exempt/
Adjustments (deducted), Taxable Income (computed), CT Liability (computed).
Typed backend adds `tax_period` (upsert key — **no duplicate CT return per
period**), `tax_loss_adjustment`, `tax_rate` (default 9.00), `filing_status`,
`reference_no`, `attachment`.

> ⚠️ **Real inconsistency worth flagging:** the client-side preview
> (`calcCorporateTax()`) applies UAE Small Business Relief — **0% CT if
> taxable income ≤ AED 375,000**, else 9% only on the amount above that
> threshold. The **backend's persisted calculation** applies the flat
> `tax_rate` to the **entire** taxable income with no threshold. The number
> a user sees in the form and the number actually saved can differ.

Other Corporate Accounting sub-modules are backed by dedicated ORM tables
with read-only GET endpoints; writes currently go through the generic
`saveCorporateRecord()` → JSON-collection path, not the typed tables
directly.

---

## 11. Reports

**HTML:** `index.html:3017-3418` — sidebar+content layout with **~24 report
panels**: Dashboard (KPI Dashboard, AI Financial Health, CFO
Recommendations), Accounting Reports (P&L, Balance Sheet, Cash Flow, Trial
Balance, General Ledger, Customer/Supplier Ledger, AR/AP Aging, VAT Reports,
Inventory Reports, Bank Reconciliation, Fixed Assets), Business Intelligence
(Revenue Intelligence, Profitability Analytics, Working Capital, Growth
Trends), UAE Compliance (VAT 201, Corporate Tax, E-Invoicing Readiness,
Arabic/English bilingual). **Backend:** `reports.py` (1,239 lines, pure
aggregation, no writes).

**Controls:** Period select (Current/This Month/Last Month/This Quarter/
This Year), Language (English/عربي), Refresh, ⬇ Excel/⬇ PDF export of the
active panel.

**Business rules:**
- All reports source from `general_ledger`, `tax_lines`, `stock_movements`, `payroll_runs` directly — not cached dashboard totals.
- Aging buckets: standard Current/1-30/31-60/61-90/90+ day buckets by days overdue.
- **AI Financial Health Score** blends Liquidity/Profitability/Debt/Cash Reserve/Payment Behavior into a 0-100 score (Healthy 70-100 / Watch 40-69 / Risk 0-39).
- Balance Sheet includes an `Assets == Liabilities + Equity` check; Trial Balance must sum to zero difference.
- The Corporate Tax **report row** and the CT **return calculation** (§10) are two separate code paths and could diverge — same threshold-inconsistency risk noted above.

---

## 12. HRMS Portal

**Entry:** Sidebar → HRMS (`/hrms`), shares the main app's JWT. Almost all HR
data is persisted as opaque JSON via one **generic** endpoint
(`POST /api/v1/app-data?action=save|bulk-save|delete|bulk-delete`, a
collection name + record dict — see `backend/app/routers/app_data.py`).
Only a small subset of fields is mirrored into real relational columns for
reporting/payroll/attendance joins (`sync_domain_model()`): `Employee(employee_no,
full_name, department, designation, basic_salary, iban, status)`. Two areas
have dedicated, fully custom backend routers with real business logic —
**Payroll** (`backend/app/routers/payroll.py`) and **Attendance/Biometrics**
(`backend/app/routers/attendance.py`) — plus **ESS** (§13). Everything else
(Recruitment, Performance, Training, Assets, most of HR Settings, Rota,
Loans) is a rich frontend built on the generic JSON store, and several of
those buttons are literal `toast('...coming soon')` placeholders, not wired
to real create flows — called out per section below.

### 12.1 Employee Directory + Employee Form

**Location:** `page-staff` → tab `hr-emp` (directory) + modal `#m-emp`.

> ⚠️ **Deviation from `STORYBOARD.md`:** the Employee Form is **not tabbed**.
> It's one long modal with section headers: *Personal Information →
> Employment Details → Insurance Details → Bank Details (WPS Salary) →
> Documents & Expiry Tracking → Portal Access (ESS)*. There is no separate
> "Loans & Advances" sub-tab inside the form — loans/advances are a
> different page tab, linked only by employee name.

**Directory table** (`#employee-tbody`): ID · Name (+ contract type/location
subtitle) · Nick Name · Department · Designation · Supervisor · Shift Hours ·
Salary (AED) · Status · Actions (View / Edit / Delete).
Actions: **+ Add Employee**, **⬇ CSV** export, row **View** (read-only
profile with an **Edit Profile** button), **Edit**, **Delete** (`confirm()`
then deletes).

**Employee Form fields:**

*Personal Information*
| Field | Type | Required | Validation |
|---|---|---|---|
| Full Name | text | **Yes — the only genuinely enforced field in the whole form** | `if(!employee.name){toast('Enter employee name','warn');return;}` |
| Nick Name | text | No | — |
| Nationality | select (15 countries + Other) | No | — |
| Date of Birth | date | No | — |
| Gender | select (Male/Female) | No | — |
| Marital Status | select | No | Single/Married/Divorced/Widowed |
| Mobile Number | text (mono) | No | placeholder format only, no regex |
| Personal Email | email | No | native HTML5 type only |
| Residential Address | textarea | No | — |
| Photo | file (image/*) | No | client-side preview only, never uploaded |

*Employment Details*
| Field | Type | Required | Validation |
|---|---|---|---|
| Employee ID | text (mono) | Auto-generated if blank | scans directory for max numeric suffix → `EMP-###` |
| Employment Type | select — **12 options** | No | Full-Time · Part-Time · Contract · Probation · Intern/Trainee · Daily Wage · Hourly · Weekly · Short-Term · Freelance/Consultant · Seasonal · Project-Based |
| Join Date | date | No | — |
| Department | select, marked `*` | Visually required, **not JS-enforced** | defaults `'Management'` if blank |
| Designation | text | No | defaults `'Employee'` |
| Branch | select, marked `*` | Visually required, **not JS-enforced** | defaults `'Dubai HQ'` |
| Role | select, marked `*` | Not enforced | filtered dynamically by Branch+Department |
| Total Salary (AED) | text (mono, not `type=number`) | **Not hard-enforced** despite storyboard claim of "required, ≥0" | defaults to 0 |
| OT Policy | select | No | System Default/Fixed/Monthly-Based/Other + named OT rules from HR Settings |
| Leave Policy | select (grouped) | No | UAE 30 Calendar / UAE Standard 21 / Internal 22 / Executive 30 / Contractor 14 / Part-Time Pro-Rata / Custom |
| Shift Hours (type + number) | select + number `min=0` | No | — |
| Work Location | select (from Branches) | No | — |
| Work Permit No. / Visa Expiry | text / date | No | Visa Expiry feeds Expiry Alerts |
| Cost Center | text | No | — |
| Emergency Contact Name/Mobile | text | No | — |

*Insurance Details*: Type (Company Provided/Self-Paid/None), Policy Number, Insurance Expiry (date, feeds Expiry Alerts).

*Bank Details (WPS Salary)*: Bank (select of 8 UAE banks), IBAN (text, **no format validation**, presence drives WPS-blocking downstream), Personal Code, Routing Code.

*Documents & Expiry Tracking*: Emirates ID + Expiry, Passport No. + Expiry, Labor Card No., Driving License No. + Expiry, plus 3 file uploads (Passport / Visa-Work Permit / Other) — **filenames only are stored, no actual file persistence**.

*Portal Access (ESS)*: Username, Password, Confirm Password (`if(pwd&&pwd!==pwdConfirm){toast('Passwords do not match');return;}`).

> ⚠️ **Critical backend gap:** these username/password fields are cosmetic
> only. The real `Employee` SQL model has no `password_hash` column, so ESS
> login **always** falls back to "password = employee number" regardless of
> what's typed here — there is no code path that actually sets a custom ESS
> password. See §13.

**Functions:** `saveEmployee()` (validates name + password match → upserts
directory row + mirrors into Payroll Salary Register → `saveServer('employees', …)`
→ backend upserts the 6-column `Employee` row) · `editEmployeeFromRow`/`openEmpEdit`
(pre-fills every field) · `deleteEmployeeFromRow` (confirm → delete) ·
`openEmployeeProfile` (read-only profile view with Edit/Export-Documents-toast buttons).

**Workflow — Add Employee:**
1. **+ Add Employee** → modal opens, dept/branch/role selects populated from HR Settings.
2. Fill Personal → Employment → Insurance → Bank → Documents → Portal Access (single scroll).
3. **Add Employee** → validates name + password match → writes to Directory AND Payroll Salary Register tables → POSTs to the generic store → backend upserts 6 real `Employee` columns → toast + audit log.
4. Employee now appears in Directory, Payroll, Leave/OT/Loan dropdowns, Expiry Alerts (if dates set), and Attendance.

### 12.2 Payroll

**Location:** `page-payroll`, 7 tabs. Backend: `backend/app/routers/payroll.py`.

> ⚠️ **Major gap:** the visible Payroll UI is entirely client-side —
> computed and rendered from Employee Directory rows (`recalcPayroll()`,
> `runPayroll()`). It does **not** call the real `POST /api/v1/payroll/generate`
> endpoint; it saves to the generic store's `payrollRuns` collection instead
> of the dedicated `payroll_runs`/`payroll_items` SQL tables. Since ESS's
> Payslips tab (§13) reads from those SQL tables, **payslips generated via
> the visible Payroll UI never show up in ESS** — the two are disconnected.
> This is the single most important integration gap to resolve in a rebuild.

**Run Payroll tab:** KPI tiles (Gross/Deductions/Net/Exceptions). Table:
Employee · Basic · Allowances · OT · Deductions · Net Pay · WPS · Status.
- **Recalculate** → sums basic+allowances+ot−deductions, flags a row as an
  exception if its WPS status isn't ok.
- **Run Payroll** → recalculates, sets status Calculated (or Review if WPS not ok).
- **Approve** → blocks (warn toast) if any row's WPS isn't ok; approves the rest.
- **Quick Adjustments**: Employee*, Type (Overtime/Deduction/Advance/Bonus),
  Amount*, Reason. Validation: `if(!employee||!amount){toast(...);return;}`
  — **reason is not actually required** despite the storyboard claim, and
  amount is only checked for non-empty, **not `>0`**.

**Salary Register tab:** mirror table populated whenever an employee is saved.

**Benefits/EOS tab — EOSB (gratuity) calculator**, per UAE Federal
Decree-Law No. 33/2021:
- Inputs: Contract Type (unlimited/limited), Termination Reason
  (dismissal/resignation), Basic Monthly Salary, Years of Service, Additional Months.
- Formula: daily rate = basic/30; eligible days = `min(years,5)×21 + max(years−5,0)×30`;
  resignation-under-unlimited-contract reduces this (0× if <1yr, 1/3 at
  1-3yr, 2/3 at 3-5yr, full at ≥5yr); capped at 2 years' total salary (`basic×24`).

**WPS/SIF tab:** static mock fields (Employer MOL ID, Routing Bank, File
Sequence, Salary Month) + Validate/Generate/Download buttons demonstrating
the WPS-blocked-if-IBAN-missing rule with a hardcoded example row.

**Payslips / Approvals / Accounting tabs:** Publish All; static approval
workflow table (Prepared → Finance Review → Management Approval → Bank
Upload); Post Journal (posts a Salary Expense/Deductions Payable/Salaries
Payable journal preview).

**Real backend API** (not wired to the above UI):
- `GET /payroll/employees`, `GET /payroll/runs`.
- `POST /payroll/generate {period: "YYYY-MM"}` — Pydantic-validated
  `^\d{4}-\d{2}$` pattern. **409** if a run already exists for that period
  (confirms the uniqueness rule). **422** if no active employees. Per
  employee: `allowances = basic×0.15`; `overtime = 350 AED flat` if
  department ∈ {Sales, Operations} else 0; `deductions = 300 AED flat` if
  IBAN missing else 0; `net = basic+allowances+overtime−deductions`.
- `POST /payroll/runs/{id}/wps-batch` — builds a SIF CSV; `status="blocked"`
  if any employee lacks IBAN.

**Workflow — Generate Payroll (real backend flow, not the visible UI):**
1. `POST /payroll/generate {period}` → 409 if duplicate period, 422 if no active employees.
2. Backend computes basic+15% allowance+flat dept-based OT−flat IBAN-based deduction per employee → creates `PayrollRun` (draft) + `PayrollItem`s.
3. `POST /payroll/runs/{id}/wps-batch` → generates SIF; blocked if any IBAN missing.

### 12.3 Leave Management

**Location:** `page-staff` tab `hr-leave` + modal `#m-leave`.

| Field | Type | Required |
|---|---|---|
| Employee | select | Yes |
| Leave Type | select — **10 options: Annual, Sick, Casual, Emergency, Maternity, Paternity, Unpaid, Lieu Days, Hajj, Work From Home** | No |
| From / To Date | date | Yes |
| Reason | textarea | No |

> ⚠️ The leave-type list actually differs in **three** places in the app —
> this Apply-Leave modal, the HR Settings → Leave Policy table (10 different
> names), and `STORYBOARD.md`'s own list (10 different names again). None of
> the three match exactly; normalize to one list when rebuilding.

`saveLeaveRequest()`: requires employee+from+to; computes
`days = round((to−from)/day)+1`; status defaults "Pending". **Approve/Reject**
row buttons set status + badge color. **Leave Balance Summary**:
`annualDays = policy==='Executive'?30:21` (hardcoded, ignores the richer HR
Settings policy table), `sickDays=90` (hardcoded); remaining <5 shown red.
**Leave Calendar**: month grid built by scanning saved leave rows, color-coded
by type.

### 12.4 Attendance (incl. Biometric Integration)

**Location:** tab `hr-att` (general attendance) + tab `hr-bio` (device
management). Backend: `attendance.py` — the most fully production-real
router in HR.

**Attendance tab:** KPI tiles from `GET /attendance/today`. **Daily
Attendance Check** flow: auto-marks all active employees Present, asks
"any absences today?", opens an Absence Picker checkbox list, **Confirm &
Save** re-renders the table. **+ Correction** → `#m-att-correction`
(Employee, Date, Requested Check-in/out, Reason) → separate Corrections tab
with Approve/Reject. **↑ Import CSV** → `POST /attendance/import-csv`
(multipart), rejects non-.csv client-side. **30-Day Trend** chart →
`GET /attendance/trend?days=30`, inline SVG line/area chart with a dashed average.

**Biometric Integration tab:** KPI tiles + **Biometric Devices** table
(Guide/Test/Remove per row) + **Sync Activity Log** (last 50 punches,
colour/icon-coded by direction and source).

**Add Device modal:**
| Field | Type | Notes |
|---|---|---|
| Device Name | text | Required |
| Device Model | select, grouped | ZKTeco TCP/IP (F/K/iClock/X Face Pro/SpeedFace/ProFace/G/UA/IN/MB Series) + Anviz — pull mode; ZKTeco ADMS — cloud push; Suprema/Hikvision — HTTP push; Manual — CSV only |
| IP Address / Port | text / number | Shown only for TCP/IP types; port defaults 4370 (5010 for Anviz) |
| Location | text | Optional |

`onBioDevTypeChange()` swaps the inline hint + shows/hides IP/Port per type
— exactly mirroring the backend's `_TCP_TYPES`/`_PUSH_TYPES` sets.
**Add Device & Get API Key** → `POST /attendance/devices` → backend
generates a random key, stores only its bcrypt hash, **returns the raw key
once** → opens the **Device Setup Guide** (branches by type: bridge-script +
`zk_bridge.conf` snippet for TCP/IP; server-URL/header table for HTTP push;
single CSV-import step for Manual — full detail already in `STORYBOARD.md` §11.5
and this session's chat history). **Test** → `POST /devices/{id}/test`
(real TCP socket check for pull devices; last-sync-age report for push
devices; CSV-count report for Manual). **Remove** → soft-delete
(`status='deleted'`, row kept, excluded from queries).

**Backend rules:** punches rejected if >5 min in the future or (device-sourced)
>90 days old; device-sourced punches deduplicated by exact
`(company_id, employee_id, punch_time, device_id)`; CSV import requires
`employee_id`+`punch_time` columns; `/punch` rate-limited 60/min; API key
never retrievable after creation.

### 12.5 Overtime

**Location:** tab `hr-ot` + modal `#m-ot`.

| Field | Type | Notes |
|---|---|---|
| Employee | select | Required (only enforced field) |
| Department | text | — |
| Date | date | — |
| Shift Time | text (mono) | default `09:00-18:00` |
| OT Type | select | Normal/Ramadan/Weekend/Public Holiday |
| Rate Multiplier | readonly, auto-set | see below |
| OT Start/End | time | defaults 18:00/19:30 |
| Overtime Hours | text (mono) | auto-fills from start/end diff |
| Reason | textarea | **not actually required** despite workflow-card claim |

**Multiplier logic (hardcoded in `updateOtMultiplier()`):** Weekend/Holiday
→ **1.5×**; Ramadan or Normal → **1.25×**. There is **no 2× multiplier
anywhere in the runtime code** — HR Settings exposes editable defaults
(1.25/1.50/1.50/1.25) but they're **not actually re-read** by this function.
Approve/Reject: Reject uses a browser `prompt()` for the reason, not a form field.

### 12.6 Weekly Rota (incl. PDF/Excel export)

**Location:** `page-rota`, 6 tabs (Shift Setup / Weekly / Monthly /
Department / Swap Requests / Rota Approval).

**Weekly Rota tab:** filters (Department/Week Start/Location/Search).
- **Copy Previous** — copies each filtered staff's assignments from `date−7` into the current week.
- **Auto Fill 4 Staff** — rotates Morning/Evening/Night across the first 4 filtered staff with 1 rest day.
- **Save Draft / Submit Approval / Publish Rota** — status transitions Draft → Pending Supervisor Review → Published.
- **Repeat rota** — mode (None/1 Year/Indefinitely ≈260 weeks) + employee filter (All or one) → **Apply Repeat** projects the current week forward, showing a live created-count.
- **Edit Shift Cell modal**: Shift Type, Start/End, Break Minutes, **Mark As** (Shift/Off/Leave/OT/Holiday/Training), Notes.

**Monthly/Department tabs:** Auto Fill Month (up to 28 days); Department
Coverage cards against fixed required headcounts (Morning 5/Evening
3/Night 2/Overtime 1) with Covered/Shortage/Missing badges.
**Copy Previous Month is currently just a toast — not a real copy.**

**PDF/Excel export — colour-coding (built this session):** public holidays
auto-detected from the HR Settings Holiday Calendar table and flagged even
on otherwise-"OFF" cells. Colour scheme: Working Day green (`#dcfce7`/`#14532d`),
Day Off grey (`#f3f4f6`/`#6b7280`), Public Holiday red (`#fee2e2`/`#b91c1c`),
Leave amber (`#fef3c7`/`#92400e`). PDF prints a Color Guide legend + shift-code
key (M/E/N/OT/L/OFF/PH) + list of that week's public holidays. Excel/CSV
mirrors the same with text markers (`PUBLIC HOLIDAY`) since CSV can't carry colour.

### 12.7 Expiry Alerts

Scans every employee row for 5 document types (Visa/Work Permit, Passport,
Emirates ID, Insurance, Driving License — **Labor Card is tracked on the
employee record but not included in this scan**). Buckets: Missing (no
date) / Critical ≤30 days / Warning ≤60 / Due Soon ≤90 / Valid >90.

### 12.8 HR Settings

Sub-nav: Organization (Departments/Branches — duplicate-name blocked
client-side, no backend uniqueness constraint since these live in the
generic store), Roles & Permissions (**Default Roles** button seeds 5
starter roles), OT Rules (**saved only to `localStorage`, never sent to the
backend at all**), Leave Policy (**settings saved here are not actually
consumed** by the leave-balance calculation, which hardcodes 21/30/90 days
regardless), General Rules (Save Rules is toast-only, no persistence),
**Holidays** (+ Add Holiday is currently just a toast — no real add-holiday
flow exists yet, despite this exact table being what Rota's Public Holiday
detection reads from), Users & Access (Add User form: Username*, Employee*,
Department auto-fill, Role, Password, Status).

### 12.9 Recruitment (ATS)

Only **Job Requisitions** and **Add Candidate** have real create modals.
Interviews/Offers/Onboarding are static tables with toast-placeholder
buttons. Job Requisition fields: Job Title* (only required field), Department,
Positions, Employment Type, Location, Target Date, Salary Range, Description,
Approval Required. Candidate fields: Name* (only required), Mobile, Email,
Position, Nationality, Experience, Expected Salary, Visa Status, Source,
Stage, CV upload (not persisted), Notes.

### 12.10-12.12 Performance / Training / Assets

**All placeholder-only** — every "+ New/Add" button is a
`toast('...coming soon')`. Tables display static seed data with no working
create flow in the current build.

### 12.13 Manager Portal

Read-only aggregation: live pending-approval counts (Leave/OT/Corrections;
Loan Requests count is static, not wired) with View buttons that just
navigate to the corresponding tab. Team Reports are toast placeholders
except Team Payroll Summary (real navigation to Payroll).

### 12.14 HR Workflow

**Not a real page** — the sidebar item is a plain alias
(`onclick="go('staff')"`) into the Employees page. There is no dedicated
screen, form, or logic behind "HR Workflow" anywhere in the codebase.

---

## 13. Employee Self-Service (ESS Portal)

**Location:** standalone `ess.html`, route `/ess`, backed by
`backend/app/routers/ess.py`. Separate login/JWT system from the main app.

> ⚠️ **Major deviation from `STORYBOARD.md`:** the real ESS has only
> **3 tabs — Profile, Attendance, Payslips**. There is **no Leave tab, no
> Overtime tab, and no Documents/PDF-download tab.** Those exist only as
> static marketing-style preview cards inside the HRMS admin's "ESS Portal"
> preview tab (for HR admins to see what ESS *could* look like), not in the
> actual employee-facing app. The backend confirms this — `ess.py` only
> exposes `/ess/login`, `/ess/me`, `/ess/attendance`, `/ess/payslips`.

**Login:** Employee ID/Number + Password (defaults to the Employee ID if
left blank). `POST /ess/login` looks up `Employee.employee_no` (case-insensitive);
runs a constant-time dummy bcrypt verify even on a not-found lookup
(timing-attack mitigation, same pattern as main auth). Since `Employee` has
no `password_hash` column, the check always falls through to
`password != employee_no` — **the password is always exactly the employee
number**, not just a default.

**Profile tab:** `GET /ess/me` → Employee ID, Full Name, Department,
Designation, Status.

**Attendance tab:** `GET /ess/attendance` → last **90** punch records
(Date/Time/Direction/Source), filtered to that employee, newest first.

**Payslips tab:** `GET /ess/payslips` → joins `PayrollItem`+`PayrollRun`,
last **24** periods, card grid (basic/allowances/OT/deductions/net).
**No PDF download exists** — cards are display-only. And since these SQL
tables are only populated by the real `POST /payroll/generate` endpoint
(§12.2) which the visible admin Payroll UI doesn't call, **ESS will show no
payslips in the current build** unless payroll is generated via direct API.

---

## Cross-Cutting Business Rules (confirmed in code)

| Rule | Enforced where | Detail |
|---|---|---|
| Payroll period uniqueness | Backend `payroll.py` | 409 if a run exists for `(company_id, period)`; period must match `^\d{4}-\d{2}$` |
| WPS blocked on missing IBAN | Backend `payroll.py` | batch `status="blocked"` if any employee lacks IBAN |
| Biometric API key shown once | Backend `attendance.py` | raw key returned once, only bcrypt hash persisted |
| Duplicate punch prevention | Backend `attendance.py` | dedup by exact `(company_id, employee_id, punch_time, device_id)` |
| Punch time sanity | Backend `attendance.py` | reject if >5 min future, or (device-sourced) >90 days old |
| CSV import required columns | Backend `attendance.py` | `employee_id`, `punch_time` required, 400 otherwise |
| Employee name required | Frontend `saveEmployee()` | only genuinely enforced field in the Employee form |
| Password confirm match | Frontend `saveEmployee()` | blocks save if mismatched |
| Leave dates required | Frontend `saveLeaveRequest()` | employee + from + to |
| OT employee required | Frontend `submitOTRequest()` | employee only |
| ESS password | Backend `ess.py` | always exactly the employee number |
| OT multipliers (runtime) | Frontend `updateOtMultiplier()` | Normal/Ramadan 1.25×, Weekend/Holiday 1.5× — no 2× anywhere |
| Gratuity (EOSB) formula | Frontend `calcGratuity()` | UAE Federal Decree-Law No. 33/2021, see §12.2 |

---

## 14. Compliance (Notifications / Expert Review / Exception Center)

### 14.1 Notifications

`index.html:2943-2971`, tabs Inbox / Rules / Channels.
- Inbox: static demo table (Time/Channel/Message/Related Record/Status); **Mark All Read is a toast only, no persistence.**
- Rules: 4 toggle rows (Invoice overdue reminder, VAT return due alert, Bank sync failure, Payroll approval reminder), plain checkboxes; **+ New Rule is a toast placeholder** — not a real rule builder yet.
- Channels: Email/WhatsApp-SMS/Push tiles, each with a Test button.
- **This entire page is presently static/demo UI** — no `/notifications` backend router exists. Real compliance alerting is generated only via the Exception Center below and the Dashboard's Alerts quick-link.

### 14.2 Expert Review

`index.html:4729-4826+`, tabs Find an Expert / My Requests / Messages.
- Expert cards are **hard-coded demo data** (3 experts), filterable by topic chips.
- New Request modal: Select Expert, Review Topic (VAT Return Review/FTA Audit Support/Penalty Dispute/Corporate Tax Advice/General Tax Query), Description.
- Messages: chat UI whose replies are a **purely client-side scripted response** after a 1.8s delay — **not connected to any backend AI or messaging endpoint.**
- Effectively a UI mock-up for a future marketplace feature.

### 14.3 Exception Center

Lives under both a standalone page and Settings → Exception Center tab
(same data). **Backend:** `exception_center.py` (`GET/POST /exceptions`).

**Auto-detected exception types** (computed live on every `GET /exceptions`, not stored):
| Source | Category | Severity |
|---|---|---|
| `PostingJob.status=='failed'` | Failed posting | high |
| `Job.status=='failed'` (OCR/extraction jobs) | OCR failure / Failed job | medium |
| Duplicate `record_key` within `salesInvoices`/`purchaseRecords` | Duplicate invoice | high |
| `StockProductMapping` missing sales/purchase/inventory account code | Unmapped stock item | medium |
| Active `Employee` with no `iban` | Payroll error (missing IBAN for WPS) | medium |
| Persisted `ExceptionEvent` rows (status ≠ closed) | as stored |

**Actions:** Refresh; Open (navigates to the relevant module); **AI
(Explain)** → `POST /ai/explain-exception` — real backend rule-based
explanation (§16.4); Map Now/Review/Retry (context-specific per exception type).

**Business rules:** the list is **computed on read**, not a persistent
queue you mark resolved from a fixed table (except explicitly-saved
`ExceptionEvent` rows, which do support a status field). Severity is fixed
`high`/`medium`/`low`; only `status != 'closed'` rows show. The Dashboard's
Alerts quick-link and the sidebar Exception Center badge both read this
same summary.

**Workflow:** any failed posting job, OCR failure, duplicate invoice number,
unmapped stock item, or employee missing IBAN automatically appears here on
next load → user clicks AI for a rule-based explanation + suggested fix →
clicks the type-specific action to actually resolve the underlying record →
next refresh, the exception disappears once its root condition is fixed.

---

## 15. Company & Settings

**HTML:** `index.html:3421-3857`, **9 tabs**: Company Registration →
Departments & Branches → Users & Roles → Tax Settings → Notifications →
Invoice Design → Quotation Design → Backup & Audit → Exception Center.

> ⚠️ Differs from `docs/storyboard.md`'s described tab set (Security/
> Approvals tabs) — the actual build has **Departments & Branches** instead.

**Company Registration fields:** Legal Company Name, Trade Name, Trade
License No. + Issue/Expiry Date, Business Activity, Legal Structure (LLC/
Free Zone Entity/Branch of Foreign Company/Sole Proprietorship), Emirate,
Free Zone, Business Type, Address/PO Box/Phone/Website, **TRN** (`maxlength=15`,
live digit-counter turning green at exactly 15 digits), VAT Registration
Date/Filing Frequency, FTA Account Username, Authorized Signatory/Emirates
ID/Designation/Tax Agent. Backend `CompanyUpdate.trn` validator: must be
`isdigit()` and exactly length 15, else `ValueError`. Update only overwrites
`name`/`country` if non-empty — required fields are never nulled.

**Departments & Branches:** Department/Branch Name* (required), Short Code
(`maxlength=6`, uppercase), Head/City, Status, Description.

**Tax Settings:** Standard VAT Rate (readonly "5%"), Zero Rate/Excise Rate
(editable text, not numerically enforced), VAT Registration Number
(**no live digit-counter on this specific field**, unlike Company
Registration's TRN), FTA Username (`type=email`), Filing Frequency, 3
informational toggles.

> ⚠️ There is **no dynamic Tax Codes management table** wired to any button
> in the current UI, even though the backend fully supports it:
> `POST /tax/codes` → 409 `"Tax code '{code}' already exists"` on duplicate
> `(company_id, code)`. Backend capability, not yet surfaced.

**Users & Roles:** Full Name*, Email, Role (Admin/Manager/Accountant/Sales/
Viewer → auto-applies a permission-grid preset), Status, Temporary Password
(auto-generated if empty), a per-module permission grid (view/create/
approve/export/admin).

**Invoice Design:** ~40 fields — Layout Name, multi-layout selector,
Template Style (6), Paper Size, Header Alignment, Accent Color, Brand Font,
Company Display Name, TRN Display show/hide, Tax Label, Business/Customer
TRN Labels, VAT Mode (Exclusive/Inclusive), toggles for PO Number/Delivery
Note/Reference/Payment Terms/Bank Details/QR Code/Tax Summary/Signature &
Stamp/Prepared/Approved By, Bank details, Footer Message, Language (English/
English+Arabic/Arabic) with full bilingual label overrides, QR Code Type
(Invoice URL/UAE VAT QR/ZATCA QR — ZATCA disabled, Saudi-only). Every field
updates a **live preview** on input; Save Layout persists both a
backwards-compatible single layout and the full multi-layout array.

**Quotation Design:** smaller mirror of Invoice Design — Template Style,
Accent Color, label overrides, "Use Invoice Branding" toggle (on by
default), Show Validity Date/Signature/VAT Summary, Terms/Footer.

**Backup & Audit:** toggles for what to include in an export (Invoices,
Purchases, Journal, Payroll/HR, Audit Log — all on by default); Audit Log
table sourced from the bootstrap's server-side `AuditLog` rows (capped at
last 50).

**Business rules (aggregate):**
- **TRN must be exactly 15 numeric digits** — enforced in 3 places (2 client-side digit-counters, 2 server-side Pydantic validators on register/update).
- FTA username uses native `type=email` on the Tax Settings tab but **not** consistently on the Company Registration tab's equivalent field.
- Duplicate tax code per company blocked with 409 (backend-only).
- Company `name`/`country` are never nulled by a partial update.
- Departments/Branches require a non-blank name; short code auto-uppercased, capped at 6 chars.

---

## 16. AI Features

### 16.1 AI Assistant (chat)

**Fields:** question textarea (Enter-to-send), 5 canned suggestion chips.
`askSystemAI(prompt)` → `POST /ai/assist` → backend keyword-routes the
question (vat/tax/trn/filing, exception/error/duplicate, account/journal/
mapping, document/ocr/invoice/receipt) into a canned-but-context-aware
answer that **interpolates live company numbers** (current net VAT, open
exception count) from a real DB snapshot. If the call fails, an extensive
**local, static** fallback covers ~20 topic areas. Every response echoes
explicit non-negotiables: *"AI does not post journals or approve source
transactions"*, *"existing validation/approval/posting/audit controls
remain authoritative"* — AI is explicitly advisory-only.

**AI Workbench** (`GET /ai/workbench`) shows a readiness panel: Document
intake / VAT validation / Accounting coding / Exception explanations /
Audit-aware answers, each annotated with live counts.

### 16.2 AI Invoice/Purchase Extraction

Trigger: file upload in Sales/Purchases/Expenses upload tabs. Endpoint:
`POST /app-data?action=documents.extract` (purchases/expenses) or
`?action=invoices.import` (sales) — both run in a **thread pool**
(`run_in_threadpool`) since extraction can take ~90s (blocking vision-API
call + PDF-rendering subprocess), so it doesn't freeze the event loop for
other users. Pipeline (in order/by file type): XLSX/CSV structured parsing
→ Amazon tax-invoice specialized parser → PDF text extraction (native
layer, then OCR fallback) → columnar/table-row heuristics → OpenAI or Claude
vision API if a key is configured. Output: invoice_no, supplier/customer,
TRN, date, line items, subtotal/VAT/total, **confidence score**, **issue
flags**. Duplicate invoice numbers flagged `already_in_db` before display.
Review UI: checkboxes, confidence %, sort/filter, per-row Edit before
accepting. Accept flow re-validates (VAT math/TRN format/duplicate-in-batch)
before writing into the register, triggering the same accounting/stock
cascades as manual entry.

### 16.3 AI Transaction Validation

`POST /ai/validate-transaction`. Checks: VAT differing from 5% by >AED 1.00
under "standard" treatment; VAT>0 with no evidence attached; VAT>0 with
missing/invalid (non-15-digit) supplier TRN; zero-rated/exempt treatment
carrying non-zero VAT. Account-mapping suggestions via keyword match
(rent/lease→Purchases, salary/payroll/wps→Salary Expense, stock/inventory→
Inventory, sale/revenue→Sales Income, bank/cash→Cash and Bank). Confidence:
92% (no issues), 72% (≤2), else 55%. Response explicitly states
`posting_allowed: false` — draft-review only, real posting still requires
the normal Source Transaction validate→approve flow.

### 16.4 AI Exception Explanation

`POST /ai/explain-exception`, triggered by the Exception Center's AI
button. Category-keyword-routed canned explanations (duplicate/TRN·VAT/
posting·account·journal/IBAN·WPS·payroll/generic fallback), each returning
an answer + 3 suggested actions. Explicitly stateless — exception status is
unchanged; explanation only.

### 16.5 AI Ledger Generation

`POST /accounts/ai-generate` + `/accounts/ai-approve` — natural-language
chart-of-accounts builder. Uses an LLM if configured, else a deterministic
rule-based fallback that keyword-matches account names to UAE-typical
types/codes and **guarantees it creates exactly as many ledger nodes as the
user named** (explicit anti-hallucination instruction in the system
prompt). Approval re-validates: no duplicate codes, code+name both present,
max depth 4 — mirrors the manual account-creation rules (§9).

### 16.6 AI Voucher/Journal Account Suggestion

`aiSuggestVoucherAccounts()` — **client-side only, no backend call**.
Requires a non-blank Narration; keyword-matches the voucher type +
narration against the loaded posting-account list to propose a Debit/
Credit pair.

### 16.7 Business rules (aggregate)
- **AI never posts, approves, or auto-resolves anything** — every response echoes that existing controls remain in force.
- All AI endpoints are strictly company-scoped — no cross-tenant data ever enters a prompt or response.
- Confidence scores accompany every AI call, letting the UI visually flag low-confidence output for manual review.
- Document extraction **degrades gracefully** through multiple fallback tiers, so it still partially works with no AI API key configured (rule-based/regex parsing only).

---

## 17. Super Admin Portal

**Entry:** `/superadmin` — role-gated: `/auth/me` must return `role: "superadmin"`,
otherwise the backend returns 403 on every `/superadmin/*` endpoint
(`_require_superadmin` dependency in `backend/app/routers/superadmin.py`) and
the frontend redirects non-superadmins to `/` on login.

### 16.1 Layout
Sidebar-nav single-page layout (7 sections, one visible at a time via
`.page-section.on`): **Overview, Companies, Usage Analytics, System Health,
Client Errors, Trial Requests, Audit Log.** Analytics and Health are lazy —
their data only loads the first time their nav item is clicked.

### 16.2 Fields by Section

**Create Company modal** (`openCreateCompany` → `createCompany`)
| Field | Type | Required | Validation |
|---|---|---|---|
| Company Name | text | Yes | Non-empty (trimmed) |
| TRN | text (mono) | No | Free text, no format check client-side |
| Subscription Expiry | date | No | — |
| Admin Email | email | Yes | Backend: 409 if email already registered (checked across ALL companies — emails are globally unique) |
| Admin Full Name | text | No | Defaults to "{Company Name} Admin" if blank |
| Admin Password | password | Yes | No client-side strength check |
| Module Permissions | checkbox grid (16 modules) | — | All-on by default; "All On"/"All Off" bulk toggles |

**Edit Company modal** (`openEditCompany` → `doEditCompany`)
| Field | Type | Required | Validation |
|---|---|---|---|
| Company Name | text | Yes | Non-empty |
| TRN | text (mono) | No | — |
| Country | text | No | — |
| Subscription Expiry | date | No | Blank = no expiry (unlimited access) |

**Add/Edit User modals** (`openAddUser`/`doAddUser`, `openEditUser`/`doEditUser`)
| Field | Type | Required | Validation |
|---|---|---|---|
| Email | email | Yes | Backend: 409 if email in use by a different user |
| Full Name | text | No | Defaults to email if blank |
| Password | password | Yes (add) / Optional (edit) | Edit: blank = keep current password |
| Role | select: admin/user/accountant/viewer | Yes | Invalid role silently falls back to "user" |

**Delete Company modal** (`deleteCompany` → `confirmDeleteCompany`)
| Field | Type | Required | Validation |
|---|---|---|---|
| Authorization Password 1 | password | Yes | Must exactly equal a hardcoded constant in the frontend JS |
| Authorization Password 2 | password | Yes | Must exactly equal a second hardcoded constant |

> ⚠️ **Security note for rebuilders:** both "authorization passwords" are
> literal string constants hardcoded in `superadmin.html`'s client-side JS
> (`confirmDeleteCompany`), not server-verified secrets. Anyone who can read
> the page source has both values. If rebuilding this, move this check to
> the backend (verify against an env var or the superadmin's own account
> password) rather than a client-side string compare.

**Impersonate** (`impersonateCompany`) — no fields, just a `confirm()` dialog.
Backend picks the company's oldest active `role="admin"` user automatically
(no user picker) — 400 error if the company has no active admin.

**Modules Permission modal** (`openModules` → `saveModules`)
- Checkbox grid, one card per module (16 total: sales, quotations, pos,
  purchase, inventory, expense, bank, accounting, corporate, reports, hrms,
  ess, notifications, expert, exception, ai).
- Each card now also shows a **usage badge** ("N actions (30d)" or "No recent
  activity"), sourced live from `/superadmin/usage-analytics?company_id=X`.

**Filters/selectors** (no modal, inline controls):
- Companies: text search (name/TRN/country) + status dropdown (All/Active/Expiring in 7 days/Expired)
- Usage Analytics: days range (14/30/90)
- System Health: fixed 14-day window, manual Refresh button
- Client Errors: days range (24h/7d/30d)
- Trial Requests: no filter, just Refresh
- Audit Log: days range (24h/7d/30d) + company dropdown (populated from the Companies list)

**Dead/unused code found:** `openResetPassword`/`doResetPassword` and
`openSetExpiry`/`doSetExpiry` are defined in the JS but not wired to any
button — password reset actually happens via Edit User's optional "New
Password" field, and expiry via Edit Company or the `+7d`/`+1yr` quick-extend
buttons. Don't rebuild these as separate flows; they're superseded.

### 16.3 Functions / Actions Reference
| Action | Function | Backend endpoint | Effect |
|---|---|---|---|
| Create company | `createCompany` | `POST /superadmin/companies` | Creates Company + one admin User in a single transaction |
| Edit company | `doEditCompany` | `PATCH /superadmin/companies/{id}` + `POST .../set-expiry` (parallel) | Two calls fired together via `Promise.all` |
| Quick-extend subscription | `quickExtend` | `POST .../set-expiry` | Computes new date client-side: `max(today, currentExpiry) + N days` |
| Add user | `doAddUser` | `POST /superadmin/companies/{id}/users` | — |
| Edit user | `doEditUser` | `PATCH .../users/{id}` (+ optional reset-password call) | — |
| Toggle user active/disabled | `toggleUserStatus` | `POST .../users/{id}/toggle-status` | Blocked (400) if target is a superadmin |
| Remove user | `deleteUser` | `DELETE .../users/{id}` | Blocked (400) if target is a superadmin |
| Set module permissions | `saveModules` | `PUT .../modules` | Only keys in the known 16-module list are persisted; unknown keys silently dropped |
| Delete company | `confirmDeleteCompany` | `DELETE /superadmin/companies/{id}` | See §17.4 cascade order. Blocked if the company has any superadmin user. |
| Impersonate | `impersonateCompany` | `POST .../impersonate` | Returns a JWT with an `imp` claim; stashes current token, swaps in the new one, redirects to `/` |
| Exit impersonation | `exitImpersonation` (in shared `app.js`) | `POST /superadmin/end-impersonation` | Restores the stashed superadmin token, redirects to `/superadmin` |
| Export companies | `exportCompaniesCSV` | *(client-side only, no API call)* | Builds CSV in-browser from already-loaded company data |
| Load usage analytics | `loadUsageAnalytics` | `GET /superadmin/usage-analytics?days=N` | Only fires when the Analytics nav item is clicked |
| Load system health | `loadSystemHealth` | `GET /superadmin/system-health?days=14` | Only fires when the Health nav item is clicked |

### 16.4 Conditions / Business Rules
- Every `/superadmin/*` endpoint requires `role == "superadmin"` — 403 otherwise.
- Emails are unique **globally**, not per-company (a user can't have the same email in two different companies).
- A company can never be deleted while it still has a superadmin-role user attached.
- A superadmin-role user can never be disabled or deleted via the user-management actions.
- Deleting a company cascades through ~30 tables in a specific dependency-safe order (leaf/line-item tables first, then tables referencing accounts/vouchers/users, then the main data tables, then reporting-snapshot tables, then `Account` self-references are nulled before the accounts themselves are deleted, then `User` rows, then the `Company` row itself). This order matters — deleting out of order will hit FK constraint errors.
- "Usage" tracking is deliberately **not** based on the frontend's generic `audit()` calls for user attribution, because that helper always writes `user: "System User"` (no real per-user identity is captured there) — instead it's derived from real `AppDataRecord` row timestamps/company_id (for volume/trend/ranking) and the real `User.created_at` (for new-user trend). There is no billing/revenue data anywhere in the schema, so "usage" intentionally means activity, not money.
- Impersonation sessions are stateless JWTs (no server-side session table) — "ending" a session is really just the client discarding the impersonation token and restoring its own; the only durable record is the audit-log entries written on start and end.

### 16.5 Step-by-Step Workflows

**Create a new tenant company:**
1. Super Admin clicks **+ New Company** on the Companies page.
2. Fills Company Name, optional TRN/expiry, Admin Email/Name/Password.
3. Toggles module permissions (defaults to all 16 on).
4. Clicks **Create Company** → `POST /superadmin/companies` → company + admin user created in one transaction → modal closes → list refreshes.

**Impersonate a company for support:**
1. Super Admin clicks **Impersonate** on a company row → confirmation dialog.
2. `POST /superadmin/companies/{id}/impersonate` → backend finds that company's oldest active admin user, issues a JWT with `sub=<that user>` and `imp=<superadmin id>`, writes an "Impersonation started" audit entry.
3. Frontend stores the current superadmin token under `taxflow_superadmin_token`, overwrites `taxflow_token` with the new impersonation token, redirects to `/`.
4. Every subsequent page load calls `/auth/me`; since the token carries the `imp` claim, the response includes `impersonated_by`, and `app.js`'s shared init path renders a persistent purple banner ("Viewing as X at Y as Super Admin") with an Exit button.
5. Clicking **Exit Impersonation** → `POST /superadmin/end-impersonation` (writes an "Impersonation ended" audit entry) → restores the stashed superadmin token → redirects to `/superadmin`.

**Delete a company (destructive, double-confirmed):**
1. Super Admin clicks **Delete** → modal warns this is permanent.
2. Must enter both hardcoded authorization passwords correctly (client-side check — see security note above) before the Delete button's handler even calls the API.
3. `DELETE /superadmin/companies/{id}` → backend blocks with 400 if the company has a superadmin user, otherwise cascades through every related table (see §17.4) and commits.

---

*(Sections for Auth, Dashboard, Sales, Purchasing, Inventory, Expenses, Bank &
Payments, Accounting, Corporate Accounting, Reports, HRMS Portal — including
Biometric Integration and Weekly Rota — Employee Self-Service, Compliance,
Settings, and AI Features are being compiled from the codebase and will be
appended below.)*
