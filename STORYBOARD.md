# TaxFlow UAE — Full Feature Storyboard

**Platform:** UAE Business Management & Compliance Platform  
**Stack:** FastAPI · SQLite (dev) / PostgreSQL (prod) · Single-page frontend  
**Live URL:** https://app.etaxflow.com  
**Default login:** admin@taxflowapp.com / admin123

---

## 1. Authentication

| Screen | Purpose | Key Actions |
|--------|---------|-------------|
| `/login` | Platform entry point | Email + password login; redirects to dashboard on success; JWT token (60-min expiry stored in localStorage) |
| `/login` (ESS link) | Employee portal access | "Employee Login" button navigates to `/ess` |

**Rules:**
- All pages redirect to `/login` if no valid token is present
- Token is checked on every page load via `localStorage.getItem('taxflow_token')`
- Password minimum: 6 characters (enforced at registration)

---

## 2. Dashboard

**Entry:** Sidebar → Dashboard (default landing page)

### 2.1 Header Row
| Element | What it shows |
|---------|--------------|
| Greeting | "Good morning / afternoon / evening, [User]" |
| Date | Today's date, auto-updated |
| New Invoice button | Opens Sales & Invoices page |
| Add Purchase button | Opens Purchases page |

### 2.2 KPI Cards (top row)
- **Revenue** — total sales in current period
- **Expenses** — total outgoing in current period
- **VAT Payable** — net VAT position
- **Outstanding** — unpaid invoice total

### 2.3 Main Grid
| Widget | Description |
|--------|-------------|
| Revenue vs Expenses chart | Monthly bar/line comparison |
| Recent Invoices | Last 5–10 sales invoices with status badges |
| Cash Flow mini-card | Current bank balance trend |
| HRMS quick-link | One-click to open HRMS Portal |
| Bank quick-link | One-click to open Bank & Payments |

### 2.4 Quick Actions (bottom row)
Six shortcut buttons: New Invoice · Add Purchase · New Quotation · Journal Entry · Reports · Alerts (→ Settings / Exception Center)

### 2.5 Topbar
| Element | Function |
|---------|----------|
| ☰ Hamburger | Collapses/expands sidebar (also lives in sidebar logo area) |
| ← Back | Go back to previous page |
| AI Extracting badge | Appears during background OCR extraction |
| POS Terminal | Opens POS in a new tab |
| EN / عربي toggle | Switch UI language |
| Calculator | Floating calculator widget |
| Dark mode | Toggle night theme |
| AI Assistant | Opens AI chat overlay |

---

## 3. Sales

**Entry:** Sidebar → Sales & Invoices

### 3.1 Invoice List (default tab)
- Table: Invoice No · Customer · Date · Due Date · Subtotal · VAT · Total · Status
- Actions: View PDF · Record Payment · Edit · Delete
- Filter by status (Draft / Sent / Paid / Overdue)
- Export to CSV
- Customer Directory sub-tab: add/edit customers with TRN, emirate, email, phone

### 3.2 Create Invoice (second tab)
**Form fields:**
| Field | Validation |
|-------|-----------|
| Invoice No. | Required, auto-generated if blank, unique |
| Invoice Date | Required, type=date |
| Due Date | Auto = invoice date + credit days |
| Customer Name | Required, datalist from customer directory |
| Customer TRN | 15-digit optional |
| Billing Address | Free text |
| Line Items | At least 1 line; qty > 0, price ≥ 0 |
| VAT | Auto-calculated at 5% |
| Notes / Terms | Optional free text |

**Live preview panel:** Shows a formatted invoice PDF-style preview as fields are filled.

**Save flow:** Validate → POST `/api/v1/invoices` → success toast → invoice appears in list.

### 3.3 AI Invoice Extraction (third tab)
- Upload supplier invoice images / PDFs
- Background OCR extracts: invoice number, supplier, TRN, date, lines, totals
- Results appear in validation table with confidence score and issue flags
- User reviews and clicks "Accept" to save to purchase register

### 3.4 Quotations
**Entry:** Sidebar → Quotations

| Feature | Detail |
|---------|--------|
| Create quotation | Same form structure as invoice |
| Convert to invoice | One-click promotion from quotation to draft invoice |
| Validity date | Optional; shown on quotation document |
| PDF layout | Inherits invoice branding by default |

### 3.5 Point of Sale (POS)
**Entry:** Topbar "POS Terminal" button (opens `/pos` in new tab)  
Also in Sidebar → Sales section

- Real-time product scanning / selection
- Running total with VAT
- Cash / card payment recording
- Receipt generation

---

## 4. Purchasing

**Entry:** Sidebar → Purchases

### 4.1 Purchase Register (default tab)
- Table: Invoice No · Supplier · TRN · Date · Subtotal · VAT · Total · Status
- Filter by supplier, date range, status
- Export CSV

### 4.2 Add Purchase (Manual) (second tab)
**Form fields:**
| Field | Validation |
|-------|-----------|
| Supplier | Required, dropdown from vendor directory |
| Reference No. | Free text |
| Purchase Date | Required, datetime-local |
| Line items | description, qty > 0, unit cost ≥ 0, VAT rate |
| Shipping / discount / other charges | Optional |
| Payment terms | Immediate / credit |

### 4.3 Vendor Directory
- Add/edit suppliers: name, TRN (15-digit), category, email, address
- Open balance tracking

---

## 5. Inventory

**Entry:** Sidebar → Inventory

### 5.1 Stock Overview (default tab)
- Product cards / table: code, name, warehouse, qty on hand, cost, reorder level
- Low-stock alert badges

### 5.2 Warehouses
- Add warehouses / locations
- Assign stock by warehouse

### 5.3 Stock Movements
- Auto-logged on every purchase / sale
- Manual adjustment (requires reason and non-zero delta → goes to Approval queue)

### 5.4 Adjustment Approvals
- Pending queue of stock adjustment requests
- Approve / reject with notes

### 5.5 Valuation
- FIFO layered cost calculation
- Valuation report by product

### 5.6 Units & Conversions
- Define measurement units (KG, LTR, PCS, CTN, etc.)
- Conversion factors between units (must be > 0)

### 5.7 Product Mappings (AI link)
- Map AI-extracted product names to inventory SKUs
- Auto-fill prices from inventory on invoice lines

---

## 6. Expenses

**Entry:** Sidebar → Expenses

### 6.1 Expense List (default tab)
- Table: Date · Description · Category · Amount · Receipt · Status
- Period-lock enforcement (cannot edit expenses in a locked period)

### 6.2 Add Expense (second tab)
| Field | Validation |
|-------|-----------|
| Description | Required |
| Category | Select from list |
| Amount | Required, > 0 |
| Date | Required |
| Receipt upload | Optional (PDF/image) |
| VAT | Optional toggle |

---

## 7. Bank & Payments

**Entry:** Sidebar → Bank & Payments

### 7.1 Bank Accounts tab
- Account list with current balances
- Add bank account (name, IBAN, currency)

### 7.2 Statement Import tab
- Upload CSV/OFX bank statement
- Auto-match transactions to invoices / purchases
- Reconciliation status per line

### 7.3 Payments tab
- Record outgoing payments (linked to purchase or supplier)
- Amount > 0, contact required

### 7.4 Receipts tab
- Record incoming payments (linked to invoice)

### 7.5 WPS Batches tab
- Generate WPS SIF files from payroll runs
- Status: Ready / Blocked (blocked if any employee is missing IBAN)

---

## 8. Accounting

**Entry:** Sidebar → Accounting

### 8.1 Chart of Accounts
- Full UAE-standard COA: Assets · Liabilities · Equity · Revenue · Expenses
- Add accounts: code, name, type, normal balance, node type
- No duplicate account codes within company

### 8.2 Journal Entries
- Double-entry: every journal must have debits = credits
- Each line: account, description, debit OR credit (not both)
- Period-lock prevents entries in closed periods

### 8.3 General Ledger
- Drill-down per account
- Date-range filter

### 8.4 Tax Summary panel
- VAT position (output – input)
- Corporate tax summary
- Links to VAT Report and Corporate Tax pages

---

## 9. Corporate Accounting

**Entry:** Sidebar → Corporate Accounting

### 9.1 Corporate Tax Return
| Field | Description |
|-------|-------------|
| Tax Period | YYYY format |
| Accounting Profit | From P&L |
| Non-deductible Expenses | Added back |
| Exempt Income | Deducted |
| Tax Loss Adjustment | Prior-year losses |
| Tax Rate | Default 9% UAE CT |
| Taxable Income | Auto-calculated |
| CT Payable | Auto-calculated |

Upserts by period (no duplicate returns per period).

### 9.2 Bills & Payables
- Supplier bill register
- Match bills to purchase invoices

### 9.3 Document Pack
- Audit evidence bundle export: VAT evidence, payroll evidence, accounting evidence

---

## 10. Reports

**Entry:** Sidebar → Reports

| Report | Contents |
|--------|----------|
| P&L Statement | Revenue – Expenses = Net Profit, by period |
| Balance Sheet | Assets = Liabilities + Equity snapshot |
| VAT Return | Output VAT, input VAT, net payable; FTA filing format |
| Aged Receivables | Outstanding invoices by age bucket |
| Aged Payables | Outstanding bills by age bucket |
| Payroll Summary | Headcount, gross pay, net pay by period |
| eInvoicing Readiness | Compliance checklist score for Phase 1/2 FTA requirements |
| Audit Document Pack | Export all evidence for a selected period |

FTA Readiness panel: TRN Validation %, VAT Math %, Document Coverage %

---

## 11. People & HR (HRMS Portal)

**Entry:** Sidebar → HRMS (navigates to `/hrms`)  
**Authentication:** Shares main app JWT token

### 11.1 Employee Directory
- List: employee no · name · department · designation · status · join date
- Search, filter by department / status
- Export CSV

### 11.2 Employee Form (Add / Edit)
**Tabs within the form:**

| Tab | Fields |
|-----|--------|
| Personal | Full name, nickname, nationality, DOB, gender, marital status, mobile, email, address |
| Employment | Employee ID, employment type (12 types), department*, designation, branch*, join date, shift, location, cost center |
| Salary & Payroll | Total salary (required, ≥ 0), OT policy, leave policy, basic/allowances breakdown |
| Documents | Passport no/expiry, Emirates ID/expiry, visa/expiry, work permit, labor card, driving license |
| Insurance | Type, policy number, expiry |
| Bank (WPS) | Bank name, IBAN, WPS ID |
| Loans & Advances | Advance requests, repayment schedule |

**Employment types (12):** Full-Time, Part-Time, Contract, Probation, Intern/Trainee, Daily Wage, Hourly, Weekly, Short-Term, Freelance/Consultant, Seasonal, Project-Based

### 11.3 Payroll
- **Generate Payroll:** Select period (YYYY-MM), one run per period enforced (409 if duplicate)
- Auto-calculates: basic + allowances (15%) + overtime − deductions
- **Payroll Run list:** status badges (Draft / Approved / Paid)
- **Quick Adjustments:** per-employee bonus/deduction/advance/OT, requires amount > 0 and reason
- **WPS Batch:** generates SIF file; blocked if any IBAN missing

### 11.4 Leave Management
**10 leave types:** Annual Leave, Sick Leave, Emergency Leave, Maternity Leave, Paternity Leave, Hajj Leave, Unpaid Leave, Compensatory Leave, Study Leave, Public Holiday

- Apply for leave, approve/reject workflow
- Leave balance tracking per employee
- UAE Labour Law entitlements enforced

### 11.5 Attendance
- Punch In / Punch Out records
- Daily attendance summary
- **Biometric Integration** (HR Settings tab): connect physical devices, each handled per its actual connection type —
  - **TCP/IP pull** (ZKTeco F/K/iClock/SpeedFace/ProFace/G/UA/IN/MB Series, Anviz): device syncs via `zk_bridge.py` running on an office PC on the same network, polling every 30s; the bridge persists its last-synced punch to disk so a restart doesn't resend the whole device log
  - **HTTP push** (ZKTeco ADMS, Suprema, Hikvision): device posts directly to `POST /api/v1/punch` with an `X-Device-Key` header — no bridge script needed
  - **Manual / CSV**: no live connection; export from the device's own software and use Import CSV
  - **Device Setup Guide**: step-by-step modal that branches by device type — bridge-script download + a pre-filled `zk_bridge.conf` snippet for TCP/IP, a copyable server-details table for HTTP push, or a single CSV-import step for Manual — each with its own flow diagram
  - Add Device / Test connectivity (socket reachability check for TCP/IP, last-punch-age check for push) / Remove, from a Biometric Devices table with live Total/Active KPI tiles
  - API key shown once at creation, bcrypt-hashed server-side; device-sourced punches are deduplicated by (employee, timestamp, device) so a bridge restart can't create duplicate attendance rows
  - Sync Activity Log: last 50 punch records from all sources (device / manual / CSV), with Import CSV and Refresh

### 11.6 Overtime
- OT types: Normal · Ramadan · Weekend · Public Holiday
- Multipliers per UAE Labour Law: 1.25× / 1.5× / 2×
- OT hours entry with start/end time

### 11.7 Weekly Rota
- Drag-and-drop shift planner grid (days × employees)
- Repeat schedule: apply one shift pattern across daily / weekly / custom range in one click
- Audit trail on rota changes
- Staff Schedule download (PDF and Excel/CSV): colour-coded by day type — Working Day (green), Day Off (grey), Public Holiday (red, auto-detected from the Holiday Calendar and labelled with the holiday's name), Leave (amber) — with a colour-guide legend and shift-code key (M/E/N/OT/L/OFF/PH) printed on the export

### 11.8 Expiry Alerts
- Centralized tracker for: passport, Emirates ID, visa, insurance, work permit, driving license, labor card
- Traffic-light badges: expired (red) / expiring soon (amber) / valid (green)
- Days-remaining countdown

### 11.9 HR Settings
- Departments: add/edit/delete
- Branches / Locations
- OT rules configuration
- Attendance rules (allow multiple breaks, shift tolerance)
- Holiday Calendar: company public holidays (date, name, location, paid/status) — feeds the Weekly Rota's automatic Public Holiday detection
- Biometric Integration: see §11.5

---

## 12. Employee Self-Service (ESS Portal)

**Entry:** `/ess` — separate portal with own login  
**Authentication:** Employee logs in with Employee No. + password (default password = employee no.)

| Page | What the employee sees |
|------|----------------------|
| Dashboard | Welcome, today's attendance status, leave balance |
| Attendance | Last 90 punch records: date, time, direction (in/out), source |
| Payslips | Last 24 months: period, basic, allowances, OT, deductions, net pay |
| Leave | Apply for leave, view status of applications |
| Overtime | Submit OT request, view approved OT |
| Documents | Download payslips as PDF |

---

## 13. Compliance

**Entry:** Sidebar → Compliance section

### 13.1 Notifications
- System-generated compliance alerts: upcoming VAT filing, expiring documents, overdue invoices, payroll due
- Read / dismiss / snooze actions

### 13.2 Expert Review
- Audit preparation checklist
- Upload supporting documents
- Generate audit document pack (VAT evidence · payroll evidence · accounting evidence)
- Review status tracking

### 13.3 Exception Center
- Moved to **Settings → Exception Center tab** (no longer a standalone sidebar page)
- All flagged transactions appear here
- Severity levels: High / Medium / Low
- Status: Open / In Review / Resolved
- Exception types: missing TRN, unmatched payment, VAT mismatch, duplicate invoice
- Resolution workflow with notes
- Dashboard Alerts tile navigates directly to Settings → Exception Center tab

---

## 14. Company & Settings

**Entry:** Sidebar → Company & Settings

### 14.1 Company Profile
| Section | Fields |
|---------|--------|
| Basic Info | Company name, trade name, business type, legal structure |
| Registration | TRN (15-digit, numeric only), trade license no, issue/expiry dates |
| Location | Address, emirate, free zone flag |
| Branding | Logo upload (shown in sidebar and on invoices) |
| Contacts | Phone, email, website |

### 14.2 VAT / FTA Settings
- TRN with live format validation (chkTRN — strips non-digits, enforces 15-digit length)
- VAT registration date
- Filing frequency (monthly / quarterly)
- FTA username (type=email)
- FTA password
- Excise tax rate (100% / 50%)

### 14.3 Tax Codes
- Default UAE codes: SR (Standard Rate 5%), ZR (Zero Rate 0%), EX (Exempt), OS (Out of Scope)
- Add custom codes: code, name, rate (0–100%), reporting box
- Duplicate code per company is blocked (409)

### 14.4 Invoice Layout
- Company display name and TRN label
- Show/hide business TRN and customer TRN
- VAT mode: exclusive / inclusive
- Font, colour, logo position
- Live preview panel that updates in real-time

### 14.5 Quotation Layout
- Inherits invoice branding by default (toggle)
- Show/hide validity date, signature block

### 14.6 Currencies
- Base currency (AED default)
- Secondary currency support

### 14.7 Audit Trail
- Full log of every user action (create / update / delete)
- Timestamp, user, module, action description
- Export for compliance evidence

### 14.8 Exception Center
- Embedded as a tab inside Settings (moved from sidebar)
- Shows exception stats: Open · High · Medium · Low counts
- Full exception table: type, description, severity, status, date, resolution notes
- Same data as standalone Exception Center page (same element IDs — JS unchanged)

---

## 15. AI Features

### 15.1 AI Assistant
**Entry:** Topbar → "AI Assistant" button (also Sidebar → AI Assistant)  
- Chat interface connected to live company data
- Ask questions: "What is my VAT payable for June?", "Show unpaid invoices over AED 10,000"
- Powered by LLM with company context

### 15.2 AI Invoice Extraction
- Upload supplier invoice images (JPG/PNG/PDF)
- Background OCR + LLM extraction: invoice no, supplier name, TRN, date, line items, totals, VAT
- Confidence score per field
- User validates extracted data before saving
- Extraction count shown in topbar badge while processing

### 15.3 AI Transaction Validation
- Validates extracted invoices against FTA rules
- Checks: TRN format, VAT calculation, required fields, tax treatment
- Flags issues into Exception Center automatically

---

## 16. Super Admin Portal

**Entry:** `/superadmin` — separate page, superadmin credentials only

### 16.1 Company Management
| Action | Detail |
|--------|--------|
| Create company | Name, TRN, email, admin full name, password, expiry date, module selection |
| Module selection | Grid of toggles per company — only enabled modules appear in that company's sidebar |
| Extend subscription | Quick buttons: +7d · +1yr |
| Delete company | Two-password authorization required (both must match before delete fires); cascades all FK-linked data |

### 16.2 Company List
- Table: ID · Name · TRN · Admin email · Expiry · Modules · Actions
- Live subscription status badge (Active / Expired)

---

## Module Dependency Map

```
Login ──► Dashboard
           ├── Sales ──► Quotations ──► (convert) ──► Invoice
           │         └── POS Terminal
           ├── Purchases ──► Inventory ──► Stock Adjustments
           │             └── AI Extraction ──► Exception Center
           ├── Expenses
           ├── Bank & Payments ──► WPS Batches (from Payroll)
           ├── Accounting ──► Journal Entries
           │              └── Corporate Tax
           ├── Reports ──► VAT Return (from Tax Lines)
           │           └── P&L / Balance Sheet (from Accounting)
           ├── HRMS Portal ──► Payroll ──► WPS Batch ──► Bank
           │              └── ESS Portal (employee login)
           ├── Notifications (from all modules)
           └── Settings ──► Tax Codes (used in invoices + reports)
                        ├── Company Profile (used in invoice PDF)
                        └── Exception Center (from AI Extraction + VAT checks)
```

---

## Validation Summary (enforced rules)

| Layer | Rule |
|-------|------|
| Backend | TRN must be exactly 15 numeric digits |
| Backend | Payroll period must be YYYY-MM format |
| Backend | No duplicate payroll run per period per company |
| Backend | No duplicate tax code per company |
| Backend | Stock adjustment delta must be non-zero, reason non-blank |
| Backend | Invoice numbers are unique per company |
| Backend | Duplicate emails return 409 (no user enumeration) |
| Backend | Rate limiting: login 10/min, register 5/hour |
| Frontend | Invoice: No., date, and customer are required |
| Frontend | Invoice line qty > 0, price ≥ 0 (type=number enforced) |
| Frontend | Purchase: supplier and date are required |
| Frontend | Employee salary ≥ 0, required |
| Frontend | Payroll adjustment amount > 0, required |
| Frontend | FTA username validated as email format |
| Frontend | TRN fields show live digit counter (15/15 turns green) |
| Frontend | Period-lock blocks edits to closed accounting periods |
