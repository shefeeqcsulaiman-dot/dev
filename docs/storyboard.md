# TaxFlow UAE — Full Application Storyboard

This document is a screen-by-screen storyboard of the entire TaxFlow application. It covers every page, every tab panel, every modal, every key user action, and the flows that connect them.

---

## 0. Entry Point — Login

```
┌─────────────────────────────────────────────┐
│              TaxFlow UAE                     │
│         UAE Business Management              │
│                                             │
│  Email    [admin@taxflowapp.com         ]   │
│  Password [••••••••                     ]   │
│                                             │
│            [ Sign In ]                       │
│                                             │
│  New company? [ Register ]                  │
└─────────────────────────────────────────────┘
```

**Actions:**
- Enter credentials → POST /auth/login → JWT token stored in session
- Successful login → React shell verifies /auth/me → mounts TaxFlow workspace
- Failed login → error message shown inline

**Registers:**
- `admin@taxflowapp.com / admin123` — company admin
- `superadmin@taxflowapp.com / superadmin123` — platform super admin → redirected to Super Admin Dashboard

---

## 0.1 Registration

```
┌──────────────────────────────────────────────┐
│  Company Name   [                          ]  │
│  Trade License  [                          ]  │
│  TRN            [                          ]  │
│  Admin Email    [                          ]  │
│  Password       [                          ]  │
│                                              │
│               [ Create Account ]             │
└──────────────────────────────────────────────┘
```

**Actions:**
- Creates company record + admin user
- Redirects to login

---

## 1. Workspace Shell

After login every screen shares the same shell:

```
┌─────────────────────────────────────────────────────────────────────┐
│ SIDEBAR                     │  TOPBAR                               │
│ ─────────────────────────── │  ─────────────────────────────────────│
│ TaxFlow UAE                 │  [ Page Title ]  [+Action] [AI] [Exc] │
│                             │                                       │
│ SALES                       │  PAGE CONTENT AREA                    │
│  Sales & Invoices    [21]   │                                       │
│  Quotations          [0]    │                                       │
│                             │                                       │
│ PURCHASING                  │                                       │
│  Purchases           [96]   │                                       │
│  Inventory           [31]   │                                       │
│  Expenses                   │                                       │
│                             │                                       │
│ FINANCE                     │                                       │
│  Bank & Payments     [13]   │                                       │
│  Accounting          [4]    │                                       │
│  Corporate Accounting       │                                       │
│  Reports             [12]   │                                       │
│                             │                                       │
│ PEOPLE                      │                                       │
│  Staff Management    [21]   │                                       │
│  Rota Planning              │                                       │
│  Payroll             [2]    │                                       │
│                             │                                       │
│ COMPLIANCE                  │                                       │
│  Notifications       [0]    │                                       │
│  Expert Review       [1]    │                                       │
│  Exception Center    [7]    │                                       │
│                             │                                       │
│ COMPANY                     │                                       │
│  Settings            [...]  │                                       │
│                             │                                       │
│ ─────────────────────────── │                                       │
│ [Company Logo] CompanyName  │                                       │
│ TRN: 100123456789012        │                                       │
└─────────────────────────────────────────────────────────────────────┘
```

**Sidebar badge rules:**
- Sales → invoice count only
- Purchases → purchase records + invoices + documents
- Bank → accounts + payments + receipts
- Staff → employee ORM count (not doubled)
- Payroll → payroll run count
- Exception → exception_events count (warn color)
- Rota → no badge (no schedule data yet)

---

## 2. Dashboard

```
┌───────────────────────────────────────────────────────────────────┐
│  DASHBOARD                                                        │
│                                                                   │
│  [ Sales Invoice ]  [ New Purchase ]   [ Quick Actions... ]       │
│                                                                   │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌─────────┐  │
│  │ Total Revenue│ │ VAT Payable  │ │ Open Invoices│ │  Staff  │  │
│  │ AED 842,400  │ │ AED 40,114  │ │     14       │ │   21    │  │
│  └──────────────┘ └──────────────┘ └──────────────┘ └─────────┘  │
│                                                                   │
│  Revenue vs VAT chart (monthly bars)                             │
│                                                                   │
│  ┌─────────────────────────┐  ┌──────────────────────────────┐   │
│  │ Recent Activity         │  │ Top Customers                │   │
│  │ Invoice saved — 2m ago  │  │ Emirates Steel   AED 182,400 │   │
│  │ Purchase posted — 5m    │  │ Al Futtaim Group AED 156,800 │   │
│  │ Employee added — 1h     │  │ ADNOC Dist.      AED 121,000 │   │
│  └─────────────────────────┘  └──────────────────────────────┘   │
│                                                                   │
│  ┌──────────────────┐  ┌──────────────────────────────────────┐   │
│  │ Invoice Status   │  │ Staff Today                          │   │
│  │ ● Paid      12  │  │ Present 18 / 21  Leave 2  Absent 1   │   │
│  │ ● Pending    3  │  └──────────────────────────────────────┘   │
│  │ ● Overdue    2  │                                             │
│  └──────────────────┘                                             │
│                                                                   │
│  Quick Access: [Sales] [Purchases] [Quotations] [Accounting]      │
│               [Reports] [Exception Center]                       │
└───────────────────────────────────────────────────────────────────┘
```

**Actions:**
- Click any KPI card → navigates to relevant module
- Click recent activity row → opens that record
- Quick access buttons → go to module
- Revenue chart hover → tooltip with month totals

---

## 3. Sales & Invoices

### 3.1 Tab: Upload Invoices

```
┌──────────────────────────────────────────────────────────────┐
│  SALES & INVOICES                                            │
│  [Upload] [AI Extraction] [Validation] [Invoices] [Create]  │
│  [Customers]                                                 │
│  ────────────────────────────────────────────────────────── │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │                                                     │    │
│  │   📎 Drop PDF / Excel invoices here                 │    │
│  │      or click to select files                       │    │
│  │                                                     │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                              │
│  Supported: PDF, JPG, PNG, XLSX, CSV                        │
│  Max size: 10MB per file                                     │
│                                                              │
│  [ Upload & Extract with AI ]                               │
└──────────────────────────────────────────────────────────────┘
```

**Actions:**
- Drop files or click to browse
- Upload triggers POST /app-data?action=documents.extract
- On success → auto-navigate to AI Extraction tab

---

### 3.2 Tab: AI Extraction

```
┌──────────────────────────────────────────────────────────────────┐
│  AI EXTRACTION                                                   │
│  ─────────────────────────────────────────────────────────────  │
│  ☐  Invoice No.  Customer       Date        Amount   Confidence  │
│  ☑  INV-2024-001  Emirates Steel  2024-06-01  AED 12,400  94%   │
│  ☑  INV-2024-002  Al Futtaim     2024-06-03  AED 8,200   87%    │
│  ☐  INV-2024-003  ADNOC Dist.    2024-06-05  AED 31,000  62%    │
│  ─────────────────────────────────────────────────────────────  │
│  [ Select All ]  [ Save All Selected ]  [ Clear ]               │
└──────────────────────────────────────────────────────────────────┘
```

**Actions:**
- Checkbox each row to include in save
- Low confidence rows are highlighted for review
- Save All Selected → validates each row → passes to Validation tab

---

### 3.3 Tab: Validation

```
┌──────────────────────────────────────────────────────────────────┐
│  VALIDATION                                                      │
│  ─────────────────────────────────────────────────────────────  │
│  ✓  INV-2024-001   Emirates Steel    Valid                       │
│  ✓  INV-2024-002   Al Futtaim        Valid                       │
│  ✗  INV-2024-003   ADNOC Dist.       Review — VAT mismatch       │
│  ─────────────────────────────────────────────────────────────  │
│  Validation checks:                                              │
│  ✓ Invoice number required and unique                           │
│  ✓ Customer required                                            │
│  ✓ Date required                                                │
│  ✓ Total = Subtotal + VAT                                       │
│  ✓ TRN 15 digits when present                                   │
│  ✓ No duplicate in current batch                                │
│  ─────────────────────────────────────────────────────────────  │
│  [ Save Valid Rows → Invoice Register ]                         │
└──────────────────────────────────────────────────────────────────┘
```

**Actions:**
- Valid rows shown with green check
- Review rows shown with red X and reason
- Save → sends to invoice register, creates source transaction

---

### 3.4 Tab: Invoices

```
┌──────────────────────────────────────────────────────────────────────────┐
│  INVOICES                                [ Search... ] [ Upload ] [+New] │
│                                                                          │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐                    │
│  │  Total   │ │ Outstanding│ │  Paid   │ │ Overdue  │                    │
│  │  AED 842K│ │ AED 142K  │ │ AED 700K│ │ AED 42K  │                    │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘                    │
│                                                                          │
│  No.        Customer        Date       Due        Total     Status       │
│  INV-001    Emirates Steel  01/06/24  01/07/24   12,400   [Paid  ]  ⋯   │
│  INV-002    Al Futtaim      03/06/24  03/07/24    8,200   [Pending]  ⋯  │
│  INV-003    ADNOC Dist.     05/06/24  05/07/24   31,000   [Overdue]  ⋯  │
│  ─────────────────────────────────────────────────────────────────────  │
│  Row actions: [👁 View] [⬇ PDF] [↗ Share] [✏ Edit] [✓ Paid] [🗑 Delete] │
└──────────────────────────────────────────────────────────────────────────┘
```

**Row action flows:**

```
View    → opens Invoice View Modal (3.5)
PDF     → opens print popup (light mode, Google Fonts, invoice layout)
Share   → opens Share Modal (3.7)
Edit    → loads invoice into Create tab (3.6) with all fields pre-filled
Paid    → redirects to Customer Receipt modal pre-filled (7.2)
Delete  → confirm dialog → removes record
```

---

### 3.5 Invoice View Modal

```
┌───────────────────────────────────────────────────────────────┐
│  Invoice INV-001               [Print PDF][Online View][Edit] │
│  Emirates Steel — Paid         [Mark Paid][Design][Share]     │
│  ─────────────────────────────────────────────────────────── │
│                                                               │
│  [Full rendered invoice — same as download design]            │
│  Company logo/initials, accent color bar, billing info,       │
│  line items table, totals, QR code, bank details,            │
│  authorized signature block                                   │
│                                                               │
│  ─────────────────────────────────────────────────────────── │
│  [Email] [WhatsApp] [Copy Link] [All Share Options]  [Close]  │
└───────────────────────────────────────────────────────────────┘
```

**Button actions:**
- Print PDF → `invoiceViewPrintHtml()` → popup with `body.theme-light` + Google Fonts
- Online View → opens `/taxflow/digital-invoice.html#invoice=...`
- Edit → `editSalesInvoiceFromRow()` → loads into Create tab
- Mark Paid → `openReceiptForInvoice()` → Bank → Receipts → Customer Receipt form
- Design → `goToInvoiceDesignSettings()` → Settings > Invoice Design tab
- Share → opens Share Modal (3.7)
- Email/WhatsApp → `shareCurrentInvoice(channel)`
- Copy Link → copies digital invoice URL to clipboard

---

### 3.6 Tab: Create Invoice (two-column)

```
┌──────────────────────────────────────────────────────────────────────┐
│  ← New Tax Invoice                                    [Save & Send]  │
│  ─────────────────────────────────────────────────  ┌─────────────┐  │
│  MAIN FORM (77%)                                   │ SIDEBAR (23%)│  │
│                                                     │             │  │
│  ┌── INVOICE DETAILS ──────────────────────────┐   │ Live Summary│  │
│  │  Invoice No.  [INV-          ]              │   │ ─────────── │  │
│  │  Date         [2026-06-06    ]              │   │ No.  INV-   │  │
│  │  Due Date     [2026-07-06    ]              │   │ Cust —      │  │
│  │  PO Number    [              ]              │   │ Date —      │  │
│  │  Delivery No. [              ]              │   │ Due  —      │  │
│  │  Reference    [              ]              │   │ ─────────── │  │
│  └─────────────────────────────────────────────┘   │ Sub AED 0   │  │
│                                                     │ VAT AED 0   │  │
│  ┌── BILL TO ──────────────────────────────────┐   │ Tot AED 0   │  │
│  │  Customer     [▼ Select customer      ]      │   │             │  │
│  │  TRN          [                       ]      │   │[Full Preview]│  │
│  │  Address      [                       ]      │   │             │  │
│  └─────────────────────────────────────────────┘   │ ✓ VAT auto  │  │
│                                                     │ ✓ Bilingual │  │
│  ┌── LINE ITEMS ────────────────────────────── ┐   │ ✓ QR code   │  │
│  │  # │ Description │ Qty │ Price │ VAT │ Amt  │   │ ✓ FTA compl.│  │
│  │  1 │ [          ]│ [1] │ [0.00]│ 5% │ 0.00 │   │             │  │
│  │  [+ Add Line]                               │   │[☆ Customize │  │
│  │  ────────────────────────────────────────   │   │  Design]    │  │
│  │  Subtotal            AED 0.00               │   └─────────────┘  │
│  │  VAT 5%              AED 0.00               │                    │
│  │  Total               AED 0.00               │                    │
│  └─────────────────────────────────────────────┘                    │
│                                                                      │
│  ┌── PAYMENT & NOTES ──────────────────────────┐                    │
│  │  Payment Terms [Net 30]                      │                    │
│  │  Notes         [                           ] │                    │
│  └─────────────────────────────────────────────┘                    │
└──────────────────────────────────────────────────────────────────────┘
```

**Live behavior:**
- Every field change → `updateSalesInvPreview()` updates sidebar instantly
- Customer selected → TRN and address auto-filled from customer directory
- Add Line → new row with product autocomplete
- Each line qty/price change → VAT calculated → totals updated → sidebar updated
- Full Preview → `openDraftInvoicePreview()` → View modal with current data
- Customize Design → `goToInvoiceDesignSettings()` → Settings > Invoice Design
- Save & Send → validates → saves to DB + app_data_records → toast confirmation

---

### 3.7 Share Modal

```
┌────────────────────────────────────────────────────────────┐
│  Share Invoice INV-001                              [×]    │
│  ─────────────────────────────────────────────────────── │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐    │
│  │  Email   │ │ WhatsApp │ │ Download │ │ Online   │    │
│  │    📧    │ │    💬    │ │  PDF 📄  │ │  View 🔗 │    │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘    │
│                                                          │
│  Send to:    [customer@email.com        ]                │
│  Phone:      [+971 50 123 4567          ]                │
│  Message:    [Please find attached invoice INV-001...]   │
│  Online Link:[https://taxflow.../inv=...]                │
│              [Copy Link]                                  │
│                                                          │
│  [ Send Email ]  [ Send WhatsApp ]          [ Close ]   │
└────────────────────────────────────────────────────────────┘
```

**Actions:**
- Customer email/phone auto-populated from customer directory
- Email → `shareCurrentInvoice('email')`
- WhatsApp → `shareCurrentInvoice('whatsapp')` → wa.me link with message
- Download PDF → `downloadCurrentInvoicePdf()`
- Online View → opens digital invoice URL
- Copy Link → clipboard

---

### 3.8 Tab: Customers

```
┌───────────────────────────────────────────────────────────────┐
│  CUSTOMERS                                  [+ Add Customer]  │
│  ─────────────────────────────────────────────────────────── │
│  Name              TRN              Emirate  Contact  Total   │
│  Emirates Steel    100123456789012  Dubai    …        842K    │
│  Al Futtaim Group  100234567890123  Dubai    …        156K    │
│  ─────────────────────────────────────────────────────────── │
│  [Search customers...]                                        │
└───────────────────────────────────────────────────────────────┘
```

**Add Customer modal** fields: Name, TRN, Emirate, Contact, Email, Phone, Address, Credit Limit

---

## 4. Quotations

### 4.1 Tab: Quotation List

```
┌──────────────────────────────────────────────────────────────┐
│  QUOTATIONS                              [+ New Quotation]   │
│  ─────────────────────────────────────────────────────────  │
│  QUO No.  Customer       Date       Expiry    Amount  Status │
│  QUO-001  ADNOC Dist.   01/06/24  30/06/24   8,200  Sent    │
│  QUO-002  Emaar Props.  03/06/24  03/07/24  18,400  Draft   │
│  ─────────────────────────────────────────────────────────  │
│  Row: [View] [Convert to Invoice] [Edit] [Delete]           │
└──────────────────────────────────────────────────────────────┘
```

**Convert to Invoice** → copies quotation into Create Invoice tab with all data pre-filled.

---

### 4.2 Tab: New Quotation

Same layout as Create Invoice (3.6) with quotation-specific fields: Validity Date, Terms & Conditions.

---

## 5. Purchases

### 5.1 Tab: Upload Documents

```
┌───────────────────────────────────────────────────────────┐
│  PURCHASES — UPLOAD DOCUMENTS                             │
│  ───────────────────────────────────────────────────────  │
│  ┌────────────────────────────────────────────────────┐   │
│  │   📎 Drop purchase invoices / bills here            │   │
│  │      PDF, JPG, PNG, XLSX, CSV  Max 10MB            │   │
│  └────────────────────────────────────────────────────┘   │
│  [ Upload & Extract ]                                     │
└───────────────────────────────────────────────────────────┘
```

---

### 5.2 Tab: AI Extraction (Purchase)

Same as Sales AI Extraction but for supplier invoices. Shows: Supplier, Invoice No., Date, Amount, VAT, Confidence.

---

### 5.3 Tab: Purchase Records

```
┌───────────────────────────────────────────────────────────────────────┐
│  PURCHASE RECORDS                 [Search] [Manual Entry] [+ New]     │
│  ─────────────────────────────────────────────────────────────────── │
│  Ref No.   Product      Supplier    Date      Items  Net     Total    │
│  PUR-001   Steel Pipes  Gulf Steel  01/06/24    20   8,400   8,820    │
│  PUR-002   Cables       Al Faris    03/06/24    50   2,100   2,205    │
│  ─────────────────────────────────────────────────────────────────── │
│  Source badge: [AI Upload] or [Manual]                               │
└───────────────────────────────────────────────────────────────────────┘
```

**Purchase posting flow (on save):**
```
Save purchase record
  → source_transactions sync
  → stock_product_mappings lookup/create
  → stock_movements (purchase type)
  → inventory_valuation_layers
```

---

### 5.4 Tab: Manual Entry

Form with: Supplier, Reference No., Date, Status, Location, Pay Term, Document Upload, Product Lines (qty, unit, price, VAT), Discount, Notes, Shipping, Payment details.

---

### 5.5 Tab: Local Purchase Orders

LPO list with: PO No., Vendor, Date, Amount, Status, linked Received/Pending items.

---

### 5.6 Tab: Vendors

Vendor directory: Name, TRN, Country, Contact, Total Purchases. Add Vendor modal.

---

### 5.7 Tab: Purchase Settings

Shared item categories and units used across purchases and sales:
- Category Setup: category name, scope, default VAT
- Unit Setup: unit code, name, type, decimal places

---

## 6. Inventory

### 6.1 Tab: Stock Dashboard

```
┌──────────────────────────────────────────────────────────────────┐
│  INVENTORY — STOCK DASHBOARD                                     │
│  ─────────────────────────────────────────────────────────────  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ Products │ │In Stock  │ │Low Stock │ │ Warehouses│           │
│  │   31     │ │  1,240   │ │    4     │ │    3      │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│  ─────────────────────────────────────────────────────────────  │
│  Product       Warehouse   On Hand   Reserved  Available  Alert  │
│  Steel Pipes   Dubai HQ    240 PCS   20 PCS    220 PCS          │
│  Cables 10m    Abu Dhabi   85 ROL    0         85 ROL     ⚠     │
│  ─────────────────────────────────────────────────────────────  │
│  Source: GET /api/v1/inventory/stock-levels                     │
└──────────────────────────────────────────────────────────────────┘
```

---

### 6.2 Tab: Item Master

Fields: Item Code, Name, Type (Stock/Service/Consumable/Fixed Asset/Raw/Finished), Category, Unit, Sales Account, Purchase Account, Inventory Account, COGS Account, VAT Code, Default Warehouse, Reorder Level, Opening Stock.

---

### 6.3 Tab: Stock Movement

Movement log: Movement Type, Item, Warehouse, Qty, Unit Cost, Reference, Date.
Movement types: Purchase Receipt, Sales Delivery, Sales Return, Purchase Return, Adjustment In/Out, Transfer In/Out, Opening Stock.

---

### 6.4 Tab: Stock Mapping

```
┌─────────────────────────────────────────────────────────────────────┐
│  STOCK MAPPING                           [Search product name...]   │
│  ─────────────────────────────────────────────────────────────────  │
│  Product Name        Generated Name     Warehouse  Unit  Status      │
│  Steel Pipes 6m      Steel Pipe 6M      Dubai HQ   PCS   [Mapped]   │
│  Cables 10mm sq      Cable 10mm²        Abu Dhabi   ROL   [Review]   │
│  Industrial Oil 5L   Industrial Oil 5L  Sharjah    BTL   [Unmapped]  │
│  ─────────────────────────────────────────────────────────────────  │
│  [Map Selected]                                                      │
│                                                                      │
│  MAPPING PANEL (on row select):                                     │
│  Product Name    [Steel Pipes 6m          ]                         │
│  Generated Name  [Steel Pipe 6M           ]                         │
│  Cost    [42.00]  Markup [56%]  Fixed [No]  Price [65.52]          │
│  Tax Rate [VAT 5%]  Inc. VAT [68.80]  Service Fees [No]            │
│                                            [ Save Mapping ]         │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 7. Expenses

### 7.1 Tab: AI Upload

Same drop zone as purchases. Extracts: vendor, amount, date, category, VAT.

### 7.2 Tab: New Expense

Fields: Expense Category, Vendor, Amount, VAT, Date, Payment Method, Project, Notes, Receipt attachment.

### 7.3 Tab: Approvals

Pending expense approval queue: Submitted By, Category, Amount, Date, [Approve] [Reject].

### 7.4 Tab: Expense List

All expenses with Status (Draft / Submitted / Approved / Rejected / Posted).

---

## 8. Bank & Payments

### 8.1 Tab: Bank Accounts

```
┌───────────────────────────────────────────────────────────────┐
│  BANK ACCOUNTS                          [+ Add Account]       │
│  ─────────────────────────────────────────────────────────── │
│  Account Name      Bank         IBAN              Balance     │
│  Emirates NBD Ops  Emirates NBD AE07033100…12345  AED 284,200 │
│  FAB Current       FAB          AE14040100…67890  AED  82,400 │
└───────────────────────────────────────────────────────────────┘
```

### 8.2 Tab: Transactions

Bank statement lines: Date, Description, Debit, Credit, Balance, Matched/Unmatched status.
Upload bank statement CSV / connect bank feed.

### 8.3 Tab: Reconciliation

```
┌────────────────────────────────────────────────────────────────┐
│  BANK RECONCILIATION                                           │
│  ─────────────────────────────────────────────────────────── │
│  Bank Statement  │  System Record      │ Match % │ Action     │
│  AED 12,400 ref  │  INV-001 payment    │  100%   │ [Confirm]  │
│  AED  8,200 ref  │  INV-002 receipt    │   80%   │ [Confirm]  │
│  AED  3,100 ref  │  —                  │   —     │ [Match]    │
│  ─────────────────────────────────────────────────────────── │
│  [ Confirm All Matches ]  [ Export Reconciliation Report ]    │
└────────────────────────────────────────────────────────────────┘
```

---

## 9. Bills & Vendors (page-bills)

### 9.1 Tab: Bills

Supplier bills list: Bill No., Vendor, Due Date, Amount, Status (Unpaid/Partial/Paid).
Row: [Pay] [View] [Edit] [Delete]

### 9.2 Tab: Vendors

Vendor master (shared with Purchases > Vendors).

### 9.3 Tab: Purchase Orders

PO list: PO No., Vendor, Items, Amount, Status (Open/Partial/Closed/Cancelled).

### 9.4 Tab: Aged Payables

Aging buckets: Current / 1–30 days / 31–60 days / 61–90 days / 90+ days per vendor.

---

## 10. Payments (page-payments)

### 10.1 Tab: Received

Customer receipts table: Receipt No., Client, Date, Amount, Method, Linked Invoice.

### 10.2 Tab: Paid

Supplier payments table: Payment No., Vendor, Date, Amount, Method, Linked Bill.

**Payment / Receipt Modal (shared):**

```
┌──────────────────────────────────────────────────────────────────┐
│  New Receipt                                                     │
│  [Receipt ●] [Payment ○]                                        │
│  ─────────────────────────────────────────────────────────────  │
│  Client      [▼ Emirates Steel          ]  — auto-filled from   │
│  Date        [2026-06-06] Time [10:30]       invoice Mark Paid  │
│  Method      [Cash ●] [Emirates NBD ○] [FAB ○]                  │
│  Amount      [AED 12,400.00  ]                                  │
│  Reference   [Payment for INV-001       ]                       │
│                                                                  │
│  RECORD ALLOCATION                           ☐ All              │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │ ☑ │ INV-001 │ 01/06/24 │ Sales Invoice │ 12,400 │ 12,400│   │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
│  Bank Account  [▼ Emirates NBD — AE07033…]                      │
│  Comments      [Receipt for Invoice INV-001 — Emirates Steel]   │
│  ─────────────────────────────────────────────────────────────  │
│  Balance: AED 12,400.00   Allocated: AED 12,400.00  ✓ Balanced  │
│                                                                  │
│  [ Save Receipt ]                              [ Cancel ]       │
└──────────────────────────────────────────────────────────────────┘
```

When opened via "Mark Paid" on a sales invoice:
- Client, Amount, Reference, Comments, and Allocation row pre-filled
- User reviews → Save → `markPaymentDocumentPaid()` → invoice status → Paid

---

## 11. Accounting

### 11.1 Tab: Chart of Accounts

```
┌───────────────────────────────────────────────────────────────┐
│  CHART OF ACCOUNTS                      [+ Add Account]       │
│  ─────────────────────────────────────────────────────────── │
│  Code    Name                    Type         Balance         │
│  1000    Assets (Group)          Group        —               │
│  1100      Cash & Bank           Asset        AED 366,600     │
│  1200      Accounts Receivable   Asset        AED 142,400     │
│  2000    Liabilities (Group)     Group        —               │
│  2100      Accounts Payable      Liability    AED  84,200     │
│  2200      VAT Payable           Liability    AED  40,114     │
│  3000    Equity                  Equity       —               │
│  4000    Revenue                 Income       AED 842,400     │
│  5000    Expenses                Expense      AED 284,100     │
│  ─────────────────────────────────────────────────────────── │
│  [Edit] [Delete]                                              │
└───────────────────────────────────────────────────────────────┘
```

---

### 11.2 Tab: Voucher Types

Voucher type setup: Name, Code, Prefix, Numbering sequence.

---

### 11.3 Tab: General Ledger

Ledger entries by account: Date, Description, Debit, Credit, Running Balance. Filters: Account, Date range, Module source.

---

### 11.4 Tab: Payments / Receipts

Combined view of posted payments and receipts with GL impact shown.

---

### 11.5 Tab: Statutory Filing

VAT return filing: Period, Output VAT, Input VAT, Net VAT, Status (Draft / Filed / Pending).

---

### 11.6 Tab: Bank Reconciliation

Same as Bank > Reconciliation, accessed from accounting context.

---

## 12. Corporate Accounting

Nine sub-tabs, all reading from backend snapshot/ORM tables:

| Tab | Content |
|-----|---------|
| Corporate Tax | CT returns, taxable income, CT rate, due dates |
| Fixed Assets | Asset register, depreciation schedule, book value |
| Accruals | Accrued expenses and prepayments by period |
| Cost Centers | Budget vs actual by cost center |
| Budgets | Annual budget setup and variance tracking |
| Cash Flow | 13-week cash flow forecast |
| Credit Control | Customer credit limits, overdue tracking, collection actions |
| Consolidation | Group consolidation across entities |
| Approvals | Approval matrix: module, amount threshold, approver role |

---

## 13. Reports

```
┌──────────────────────────────────────────────────────────────────┐
│  REPORTS                                                         │
│  ─────────────────────────────────────────────────────────────  │
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────────┐   │
│  │  VAT Report    │ │  P&L Statement │ │  Trial Balance     │   │
│  │  Jun 2024      │ │  Q2 2024       │ │  As at 30/06/24    │   │
│  │  [ Generate ]  │ │  [ Generate ]  │ │  [ Generate ]      │   │
│  └────────────────┘ └────────────────┘ └────────────────────┘   │
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────────┐   │
│  │ Customer Aging │ │ Supplier Aging │ │  Cash Flow         │   │
│  │  [ Generate ]  │ │  [ Generate ]  │ │  [ Generate ]      │   │
│  └────────────────┘ └────────────────┘ └────────────────────┘   │
│  ┌────────────────┐ ┌────────────────┐ ┌────────────────────┐   │
│  │ Inventory Val. │ │ Payroll Report │ │  Audit Trail       │   │
│  │  [ Generate ]  │ │  [ Generate ]  │ │  [ Generate ]      │   │
│  └────────────────┘ └────────────────┘ └────────────────────┘   │
│                                                                  │
│  AI Insights panel — reads all module data and surfaces risks    │
│  [ Ask AI about your financials... ]                             │
└──────────────────────────────────────────────────────────────────┘
```

All reports source from: general_ledger, tax_lines, stock_movements, payroll_runs — not dashboard totals.

---

## 14. Staff Management

### 14.1 Tab: Employees

```
┌───────────────────────────────────────────────────────────────────┐
│  EMPLOYEES                                       [+ Add Employee] │
│  ─────────────────────────────────────────────────────────────── │
│  ID       Name            Dept.     Title       Salary   Status   │
│  EMP-001  Ahmed Al Rashid  Finance  Accountant  8,500    Active   │
│  EMP-002  Sara Mohamed     HR       HR Manager  9,200    Active   │
│  ─────────────────────────────────────────────────────────────── │
│  Row: [View Profile]                                              │
└───────────────────────────────────────────────────────────────────┘
```

**Employee Profile modal:**
Personal details, documents (passport, visa, work permit, Emirates ID), salary, IBAN, leave balance, overtime history, emergency contact.

---

### 14.2 Tab: Attendance

Daily attendance log: Employee, Date, Check-in, Check-out, Hours, Status (Present/Late/Absent/Leave).

### 14.3 Tab: Overtime

Overtime records: Employee, Date, Extra hours, Rate, Amount. [Approve] [Reject].

### 14.4 Tab: Leave Management

Leave requests: Employee, Type (Annual/Sick/Emergency), Dates, Days, Status. [Approve] [Reject].

### 14.5 Tab: Corrections

Attendance correction requests with reason and approval workflow.

### 14.6 Tab: HR Settings

Leave policies, shift templates, overtime rules, public holidays.

### 14.7 Tab: Biometric Integration

Biometric device configuration and sync status.

---

## 15. Rota Planning

### 15.1 Tab: Shift Setup

Define shifts: Name, Start Time, End Time, Break Duration, Department.

### 15.2 Tab: Weekly Rota

```
┌──────────────────────────────────────────────────────────────────────┐
│  WEEKLY ROTA — Week of 02/06/2024                [< Prev] [Next >]  │
│  ──────────────────────────────────────────────────────────────────  │
│  Employee         Mon  Tue  Wed  Thu  Fri  Sat  Sun                  │
│  Ahmed Al Rashid  M    M    M    M    M    —    —                    │
│  Sara Mohamed     M    M    M    M    M    M    —                    │
│  [Assign Shift]   [Check Coverage]  [Publish Rota]                  │
└──────────────────────────────────────────────────────────────────────┘
```

### 15.3 Tab: Monthly Rota

Calendar view of the full month with daily shift assignments.

### 15.4 Tab: Department Rota

Coverage analysis by department and day.

### 15.5 Tab: Swap Requests

Employee shift swap requests with manager approval.

### 15.6 Tab: Rota Approval

Final review before HR publishes the rota. Published rota locks and notifies employees.

---

## 16. Payroll

### 16.1 Tab: Run Payroll

```
┌────────────────────────────────────────────────────────────────────┐
│  RUN PAYROLL                                                       │
│  ─────────────────────────────────────────────────────────────── │
│  Period    [June 2024            ]                                │
│  Employee  [● All  ○ Department  ○ Individual]                    │
│                                                                    │
│  [ Generate Payroll ]                                             │
│  ─────────────────────────────────────────────────────────────── │
│  Employee       Basic    OT      Deductions  Net                  │
│  Ahmed Al Rashid 8,500  400.00    850.00    8,050.00              │
│  Sara Mohamed    9,200    —       920.00    8,280.00              │
│  ─────────────────────────────────────────────────────────────── │
│  Total gross: AED 172,000   Total net: AED 161,800               │
│  [ Post Payroll ]  [ Export Payslips ]  [ Generate WPS SIF ]     │
└────────────────────────────────────────────────────────────────────┘
```

### 16.2 Tab: Salary Register

Full salary history per employee with breakdowns.

### 16.3 Tab: Benefits / EOS

End-of-service gratuity calculator, benefits tracker.

### 16.4 Tab: WPS / SIF

```
┌──────────────────────────────────────────────────────────────────┐
│  WPS / SIF EXPORT                                                │
│  ─────────────────────────────────────────────────────────────  │
│  Payroll Run: June 2024                                          │
│  Bank: Emirates NBD                                              │
│  Employees: 21                                                   │
│  Total: AED 161,800                                              │
│  ─────────────────────────────────────────────────────────────  │
│  Validation: ✓ All IBANs present  ✓ WPS agent registered        │
│  ─────────────────────────────────────────────────────────────  │
│  [ Generate SIF File ]  [ Download SIF ]  [ Mark Uploaded ]     │
└──────────────────────────────────────────────────────────────────┘
```

### 16.5 Tab: Payslips

Payslip browser per employee per period. Download as PDF.

### 16.6 Tab: Approvals

Multi-level payroll approval before posting.

### 16.7 Tab: Accounting

Payroll posting status → journal entries created:
```
Dr Salary Expense    AED 172,000
    Cr Salary Payable    AED 161,800
    Cr Deductions Payable AED 10,200
```

---

## 17. Notifications

### 17.1 Tab: Inbox

System notifications: VAT due dates, invoice reminders, approval requests, exception alerts.

### 17.2 Tab: Rules

Configure notification triggers: "Invoice overdue > 7 days → email + in-app", "VAT return due in 14 days → email".

### 17.3 Tab: Channels

Email, WhatsApp, In-App, SMS configuration and test.

---

## 18. Expert Review

### 18.1 Tab: Find an Expert

Browse certified UAE accountants and auditors available for review.

### 18.2 Tab: My Requests

Submitted review requests with status: Pending / In Review / Completed.

### 18.3 Tab: Messages

In-app messaging with assigned expert.

---

## 19. Exception Center

```
┌──────────────────────────────────────────────────────────────────────┐
│  EXCEPTION CENTER                                    [Refresh]       │
│  ──────────────────────────────────────────────────────────────────  │
│  ┌──────┐  ┌──────┐  ┌──────┐  ┌──────┐                            │
│  │ Open │  │ High │  │ Med  │  │ Low  │                            │
│  │  7   │  │  2   │  │  3   │  │  2   │                            │
│  └──────┘  └──────┘  └──────┘  └──────┘                            │
│  ──────────────────────────────────────────────────────────────────  │
│  Module      Description                    Severity  Action        │
│  Sales       Duplicate invoice INV-2024-003  High     [Explain AI]  │
│  Inventory   Unmapped item: Steel Pipe 6m    Medium   [Map Now]     │
│  Accounting  Failed posting — VAT mismatch   High     [Retry]       │
│  OCR         Low confidence extraction       Low      [Review]      │
│  ──────────────────────────────────────────────────────────────────  │
│  [Explain AI] → AI generates explanation and suggested fix          │
└──────────────────────────────────────────────────────────────────────┘
```

Exception sources: Failed postings, duplicate invoices, unmapped stock, VAT mismatches, OCR failures, bank unmatched, payroll errors, eInvoice failures.

---

## 20. Settings

Nine tabs covering all system configuration:

### 20.1 Company Registration

Trade license, TRN, FTA registration, authorized signatory, company stamp, branch offices.

### 20.2 Users & Roles

User list with role (Admin / Accountant / HR / Viewer). Invite new user, reset password, deactivate.

### 20.3 Tax Settings

VAT registration details, tax codes, VAT periods, return due dates, FTA filing preferences.

### 20.4 Notifications

Notification preferences per event type, per user role.

### 20.5 Security

MFA setup, session timeout, IP whitelist, password policy.

### 20.6 Approvals

Approval matrix: module + amount threshold + required approver role + escalation.

### 20.7 Invoice Design

```
┌─────────────────────────────────────────────────────────────────────┐
│  INVOICE DESIGN LAYOUT                [Preview Invoice] [Save]      │
│  ─────────────────────────────────────────────────────────────────  │
│  Template    [Modern Tax Invoice ▼]  Paper      [A4 Portrait ▼]    │
│  Accent Color [#2563eb ■]            Alignment  [Left ▼]           │
│  Font         [Modern Sans ▼]        Language   [English ▼]         │
│  Company Name [My Company LLC    ]                                  │
│  TRN          [show ▼]              Address    [Dubai, UAE    ]     │
│  Terms        [Net 30            ]  Footer     [Thank you...  ]     │
│  Due Days     [30]                  Currency   [AED 1,234.00 ▼]    │
│  ─────────────────────────────────────────────────────────────────  │
│  Bank Details: Name, Account, IBAN, SWIFT, Payment Link             │
│  Labels: Heading, Product, Qty, VAT, Total (bilingual EN/AR)       │
│  Show/hide: TRN, Customer TRN, PO No., Delivery Note, Reference    │
│  Show/hide: QR Code, Tax Summary, Signature, Stamp, Prepared By    │
│  QR Type: [UAE VAT QR ▼] or [Invoice URL ▼]                       │
│  ─────────────────────────────────────────────────────────────────  │
│  [ Live invoice preview updates on every field change ]            │
└─────────────────────────────────────────────────────────────────────┘
```

Settings saved to server → `getInvoiceLayout()` → `renderSalesInvoicePreview()` → print/download both use same design.

Shortcut to this tab from:
- Sales Create form → "Customize Invoice Design" button
- Invoice View modal → "Design" button

### 20.8 Quotation Design

Same layout as Invoice Design but for quotation documents.

### 20.9 Backup & Audit

Database export, audit log browser (last 50 actions), system health check.

---

## 21. AI Assistant

```
┌─────────────────────────────────────────────────────────────────────┐
│  AI ASSISTANT                                                       │
│  ─────────────────────────────────────────────────────────────────  │
│  ┌────────────────────────────────────────────────────────────┐    │
│  │ Hi! I'm your TaxFlow AI. Ask me anything about your        │    │
│  │ finances, invoices, VAT, inventory, or payroll.            │    │
│  └────────────────────────────────────────────────────────────┘    │
│                                                                     │
│  [What is my current VAT payable?           ] [Send]               │
│                                                                     │
│  Recent questions:                                                  │
│  • Why did INV-003 fail validation?                                 │
│  • How do I generate a WPS SIF file?                               │
│  • What is the VAT treatment for zero-rated exports?               │
│                                                                     │
│  Sources: POST /api/v1/ai/assist   GET /api/v1/ai/workbench        │
│           POST /api/v1/ai/validate-transaction                     │
│           POST /api/v1/ai/explain-exception                        │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 22. Super Admin Dashboard

Accessible only to `role === "superadmin"`. Replaces the regular workspace.

```
┌─────────────────────────────────────────────────────────────────────┐
│  TAXFLOW — SUPER ADMIN                                              │
│  ─────────────────────────────────────────────────────────────────  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐           │
│  │Companies │  │  Users   │  │Employees │  │Sub Users │           │
│  │    4     │  │   12     │  │   84     │  │    8     │           │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘           │
│  ─────────────────────────────────────────────────────────────────  │
│  Company Name       Admin Email             Created      Expires    │
│  Gulf Steel LLC     admin@gulfsteel.ae     01/01/2024   01/01/2025 │
│  Al Futtaim         admin@alfuttaim.ae     15/02/2024   15/02/2025 │
│  ADNOC Dist.        admin@adnocdist.ae     01/03/2024   01/03/2025 │
│  ─────────────────────────────────────────────────────────────────  │
│  Company  │ Admin Email │ Created │ Expires │ Employees │ Sub Users │
│  [ View ] [ Reset Password ] [ Suspend ] [ Delete ]                │
│  ─────────────────────────────────────────────────────────────────  │
│  Source: GET /api/v1/superadmin/companies                          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 23. Key End-to-End Workflows

### Workflow A — Sales Invoice Lifecycle

```
Create Invoice (tab: Create)
  ↓ fill customer, lines, dates
  ↓ live sidebar updates instantly
  ↓ [Save & Send]
Invoices table row appears
  ↓ [View] → view modal
  ↓ [Share] → email/WhatsApp/PDF/link
  ↓ [Mark Paid] → Customer Receipt modal pre-filled
    ↓ [Save Receipt] → invoice status = Paid
  ↓ [Download PDF] → print popup (same design as modal)
```

### Workflow B — AI Purchase Lifecycle

```
Upload Documents (tab: Upload)
  ↓ [Upload & Extract]
AI Extraction (tab: AI Extraction)
  ↓ review extracted rows, select valid ones
  ↓ [Save All Selected]
Validation (tab: Validation)
  ↓ backend validates each row
  ↓ [Save Valid Rows]
Purchase Records (tab: Purchase Records)
  ↓ backend syncs: source_transactions → stock_movements → valuation_layers
Inventory — Stock Dashboard
  ↓ updated stock quantity visible immediately
```

### Workflow C — Payroll Lifecycle

```
Staff — Employees (all 21 active)
  ↓ [Run Payroll] tab
    ↓ Select period, [Generate Payroll]
    ↓ Review gross/net per employee
    ↓ [Approvals] tab → manager approves
  ↓ [Post Payroll]
    ↓ journal entry created:
      Dr Salary Expense / Cr Salary Payable / Cr Deductions
  ↓ [WPS/SIF] tab → generate SIF → download → upload to bank
  ↓ [Payslips] tab → individual PDF payslips
```

### Workflow D — Accounting Posting Lifecycle

```
Business action (invoice/purchase/payment)
  ↓
Source Transaction created
  ↓
Validation Engine
  ↓
Approval Engine
  ↓
Posting Queue
  ↓
Accounting Posting Engine
  ↓
Journal Entry (balanced debit = credit)
  ↓
General Ledger updated
  ↓
Tax Lines created
  ↓
Reports refreshed
  ↓
Audit Log entry
```

### Workflow E — Invoice Design → Print

```
Settings > Invoice Design
  ↓ change color, font, logo, fields
  ↓ live preview updates instantly (getInvoiceLayout())
  ↓ [Save Layout] → POST /api/v1/app-data (invoiceLayout)
Sales > View Invoice modal
  ↓ renderSalesInvoicePreview(inv) — uses getInvoiceLayout()
  ↓ same design shown in modal
  ↓ [Print PDF]
    ↓ invoiceViewPrintHtml(inv)
    ↓ popup with: Google Fonts + body.theme-light + styles.css
    ↓ identical design to modal, light mode, print-optimized
```

### Workflow F — Exception Resolution

```
Exception raised (failed posting / duplicate / OCR error)
  ↓
Exception Center (sidebar badge updates to show count)
  ↓ click exception row
  ↓ [Explain AI] → POST /api/v1/ai/explain-exception
    ↓ AI explains cause and suggests fix
  ↓ [Retry] → re-runs failed posting
  ↓ [Map Now] → opens stock mapping panel
  ↓ [Review] → opens OCR extraction for correction
  ↓ exception resolved → removed from open list
```

---

## 24. Modal Inventory

| Modal ID | Trigger | Purpose |
|---|---|---|
| m-sales-view | View button on invoice row | Full invoice preview with all actions |
| m-invoice-share | Share button | 4-channel share grid with message |
| m-payment | Mark Paid / + Record Payment | Customer receipt or supplier payment form |
| m-customer | + Add Customer | Customer creation |
| m-emp | + Add Employee | Employee creation |
| m-acc | + New Entry | Journal entry creation |
| m-newreview | + New Request | Expert review request |
| m-inventory-item | + Add Item | Inventory item creation |

---

## 25. Navigation Map

```
Login
  └── Workspace
        ├── Dashboard
        ├── Sales & Invoices
        │     ├── Upload Invoices
        │     ├── AI Extraction
        │     ├── Validation
        │     ├── Invoices ──────────── [View Modal] → [Share Modal]
        │     ├── Create Invoice ─────── [→ Invoices on save]
        │     └── Customers
        ├── Quotations
        │     ├── Quotation List ─────── [Convert → Create Invoice]
        │     └── New Quotation
        ├── Purchases
        │     ├── Upload Documents
        │     ├── AI Extraction
        │     ├── Purchase Records ────── [→ Inventory stock update]
        │     ├── Local Purchase Orders
        │     ├── Vendors
        │     └── Purchase Settings
        ├── Inventory
        │     ├── Stock Dashboard ─────── [→ Stock Mapping]
        │     ├── Item Master
        │     ├── Stock Movement
        │     └── Stock Mapping
        ├── Expenses
        │     ├── AI Upload
        │     ├── New Expense
        │     ├── Approvals
        │     └── Expense List
        ├── Bank & Payments
        │     ├── Bank Accounts
        │     ├── Transactions
        │     └── Reconciliation
        ├── Bills & Vendors
        │     ├── Bills
        │     ├── Vendors
        │     ├── Purchase Orders
        │     └── Aged Payables
        ├── Payments ────────────────── [← Mark Paid on Invoice]
        │     ├── Received (receipts)
        │     └── Paid (payments)
        ├── Accounting
        │     ├── Chart of Accounts
        │     ├── Voucher Types
        │     ├── General Ledger
        │     ├── Payments / Receipts
        │     ├── Statutory Filing
        │     └── Bank Reconciliation
        ├── Corporate Accounting
        │     ├── Corporate Tax
        │     ├── Fixed Assets
        │     ├── Accruals
        │     ├── Cost Centers
        │     ├── Budgets
        │     ├── Cash Flow
        │     ├── Credit Control
        │     ├── Consolidation
        │     └── Approvals
        ├── Reports
        ├── Staff Management
        │     ├── Employees
        │     ├── Attendance
        │     ├── Overtime
        │     ├── Leave Management
        │     ├── Corrections
        │     ├── HR Settings
        │     └── Biometric Integration
        ├── Rota Planning
        │     ├── Shift Setup
        │     ├── Weekly Rota
        │     ├── Monthly Rota
        │     ├── Department Rota
        │     ├── Swap Requests
        │     └── Rota Approval
        ├── Payroll
        │     ├── Run Payroll
        │     ├── Salary Register
        │     ├── Benefits / EOS
        │     ├── WPS / SIF
        │     ├── Payslips
        │     ├── Approvals
        │     └── Accounting
        ├── Notifications
        │     ├── Inbox
        │     ├── Rules
        │     └── Channels
        ├── Expert Review
        │     ├── Find an Expert
        │     ├── My Requests
        │     └── Messages
        ├── Exception Center
        ├── AI Assistant
        └── Settings
              ├── Company Registration
              ├── Users & Roles
              ├── Tax Settings
              ├── Notifications
              ├── Security
              ├── Approvals
              ├── Invoice Design ─────── [← Customize Design shortcut]
              ├── Quotation Design
              └── Backup & Audit

Super Admin (separate entrypoint)
  └── Company list with user/employee/sub-user counts
```
