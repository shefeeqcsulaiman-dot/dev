# TaxFlow Manual Testing Document

## Document Control

| Field | Value |
| --- | --- |
| Product | TaxFlow UAE Business Platform |
| Test Type | Manual functional, workflow, and regression testing |
| Environment | Local, staging, or UAT |
| Frontend URL | http://localhost:5173 |
| API URL | http://localhost:8000/docs or configured backend port |
| Default User | admin@taxflowapp.com |
| Test Date | |
| Tester | |
| Build / Commit | |

## Test Result Legend

| Status | Meaning |
| --- | --- |
| Pass | Actual result matches expected result |
| Fail | Actual result does not match expected result |
| Blocked | Cannot test because prerequisite or environment is unavailable |
| Not Run | Test has not been executed |
| N/A | Test does not apply to the current build |

## General Preconditions

- Application is running successfully.
- Tester can log in with a valid active user.
- Test company has customers, suppliers, items, VAT setup, accounts, and seed transactions.
- Browser cache is refreshed before each full regression cycle.
- Any failed result includes screenshot, test data, browser console error where available, API response where available, and exact reproduction steps.

## Global Tests

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GEN-001 | Authentication | Login | Valid user login | Open app, enter valid email and password, submit | User lands on dashboard and token/session is active | Not Run | |
| GEN-002 | Authentication | Login | Invalid login | Enter invalid credentials and submit | Login is rejected with clear error and no session is created | Not Run | |
| GEN-003 | Navigation | Sidebar | Navigate all visible modules | Click each sidebar module | Correct page title, module content, and action button are shown | Not Run | |
| GEN-004 | Navigation | Responsive | Mobile navigation | Resize to mobile width, open menu, switch modules | Menu opens/closes correctly and content remains usable | Not Run | |
| GEN-005 | Tenant Control | Data Scope | Company scoped data | Log in as a user from one company and review major lists | Only the user's company records are visible | Not Run | |
| GEN-006 | Audit | Sensitive Actions | Audit trail creation | Create, edit, delete, approve, post, or export a sensitive record | Audit log captures user, module, action, timestamp, and record details | Not Run | |
| GEN-007 | Validation | Required Fields | Required field enforcement | Submit key forms with mandatory fields empty | Save is blocked and fields show clear validation feedback | Not Run | |
| GEN-008 | Error Handling | API Failure | Backend unavailable handling | Stop or disconnect backend, refresh module data | UI shows graceful error state and does not corrupt local data | Not Run | |

## Module Test Matrix

### Dashboard

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DASH-001 | Dashboard | Summary Cards | Load database totals | Open Dashboard after login | Sales, purchases, tax, inventory, documents, and exception counts load from backend data | Not Run | |
| DASH-002 | Dashboard | Charts | Financial chart display | Review revenue, purchase, VAT, and KPI sections | Values are formatted correctly and no chart area is blank | Not Run | |
| DASH-003 | Dashboard | Drilldown Links | Navigate from count card | Click a dashboard card linked to a module | User lands on the correct module and tab | Not Run | |
| DASH-004 | Dashboard | Refresh | Updated data appears | Create a sales or purchase record, return to dashboard | Related dashboard totals update after refresh/reload | Not Run | |

### Company Registration and Settings

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SET-001 | Settings | Company Profile | Save company legal data | Enter legal name, address, TRN, and contact fields, save | Data is saved and reloaded correctly | Not Run | |
| SET-002 | Settings | Tax Details | TRN validation | Enter invalid TRN, then valid 15 digit TRN | Invalid TRN is rejected or flagged; valid TRN is accepted | Not Run | |
| SET-003 | Settings | Users and Roles | Role visibility | Review user list and assigned roles | User records show correct role, status, permissions, and last login | Not Run | |
| SET-004 | Settings | Permissions | Permission restrictions | Use a restricted role and attempt unavailable actions | Restricted actions are hidden or blocked | Not Run | |
| SET-005 | Settings | Invoice Layout | Invoice template update | Change invoice labels, tax display, brand fields, and preview | Preview reflects saved layout without breaking totals | Not Run | |
| SET-006 | Settings | Integrations | Integration fields | Review integration/API setting fields | Required integration settings validate and save correctly | Not Run | |
| SET-007 | Settings | Approval Matrix | Approval rule setup | Add or edit approval rule | Approval rule persists and appears in related workflows | Not Run | |

### Sales and Invoices

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SAL-001 | Sales | Manual Invoice | Create sales invoice | Open Sales, create invoice with customer, items, VAT, due date | Invoice appears in register with Manual source | Not Run | |
| SAL-002 | Sales | Manual Invoice | Required fields | Save invoice without customer, date, invoice number, or line items | Save is blocked with validation messages | Not Run | |
| SAL-003 | Sales | VAT | Output VAT calculation | Create invoice with taxable line at 5 percent VAT | VAT and total equal subtotal plus VAT | Not Run | |
| SAL-004 | Sales | Customer TRN | TRN validation | Enter invalid and valid customer TRN values | Invalid TRN is flagged; valid 15 digit TRN is accepted | Not Run | |
| SAL-005 | Sales | Duplicate Control | Duplicate invoice number | Create or import an invoice number that already exists | Duplicate is rejected or held for review | Not Run | |
| SAL-006 | Sales | AI Upload | Upload sales invoice file | Upload PDF, image, CSV, or Excel invoice and run extraction | Extracted row appears in AI validation table | Not Run | |
| SAL-007 | Sales | AI Validation | Save valid extracted row | Select valid AI extracted invoice and save | Invoice appears in register with AI Upload source | Not Run | |
| SAL-008 | Sales | AI Validation | Low confidence or invalid row | Upload or edit row with missing invoice number, customer, date, TRN, or total mismatch | Row is held for review and cannot be saved as valid | Not Run | |
| SAL-009 | Sales | Invoice Actions | View, edit, delete | Use row action buttons on invoice register | View opens details, edit updates row, delete observes dependency checks | Not Run | |
| SAL-010 | Sales | Sharing | Share invoice | Open share action and copy/share public invoice link | Share message and link include correct invoice number, customer, amount, and due date | Not Run | |
| SAL-011 | Sales | PDF | Download/print invoice | Generate invoice PDF/print view | PDF view uses configured layout, totals, TRN, and bilingual labels where enabled | Not Run | |
| SAL-012 | Sales | Accounting Link | Source transaction and posting | Approve/post eligible invoice | Source transaction, journal, tax line, and audit record are created where implemented | Not Run | |

### Quotations

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QUO-001 | Quotations | Create | Create quotation | Open Quotations and create customer quotation | Quotation is saved with quote number, customer, lines, totals, and status | Not Run | |
| QUO-002 | Quotations | Preview | Preview quotation | Open quotation preview | Preview uses quotation labels and layout settings | Not Run | |
| QUO-003 | Quotations | Convert | Convert to invoice | Convert quotation to invoice where available | Sales invoice is created without losing customer, lines, VAT, and totals | Not Run | |
| QUO-004 | Quotations | Share | Share quotation | Use quotation share action | Share content includes correct quotation number and customer | Not Run | |

### Purchases, Bills, and Vendors

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PUR-001 | Purchases | Manual Purchase | Create purchase record | Enter supplier, reference, date, location, line items, VAT, payment details | Purchase appears in purchase records table | Not Run | |
| PUR-002 | Purchases | Supplier TRN | TRN validation | Enter invalid and valid supplier TRN values | Invalid TRN is flagged; valid 15 digit TRN is accepted | Not Run | |
| PUR-003 | Purchases | Duplicate Control | Duplicate supplier invoice | Save same supplier invoice/reference twice | Duplicate is rejected or flagged | Not Run | |
| PUR-004 | Purchases | Input VAT | VAT recoverable calculation | Create taxable purchase at 5 percent VAT | Input VAT and total are calculated correctly | Not Run | |
| PUR-005 | Purchases | Document Upload | Upload purchase invoice | Upload supported purchase document and run extraction | Extracted purchase rows appear for validation | Not Run | |
| PUR-006 | Purchases | AI Validation | Save extracted purchase | Select valid extracted rows and save | Purchases save to records and source is AI Upload | Not Run | |
| PUR-007 | Purchases | AI Edit | Correct extracted row | Edit extracted supplier, lines, VAT, discount, shipping, or total | Recalculated totals are correct and row can be saved when valid | Not Run | |
| PUR-008 | Purchases | Inventory Sync | Stock receipt from purchase | Save stock item purchase with quantity | Stock movement and valuation layer are created or refreshed | Not Run | |
| PUR-009 | Purchases | Accounting Link | Posting impact | Approve/post eligible purchase | Accounting entry is Dr Inventory/Expense, Dr VAT Recoverable, Cr Accounts Payable | Not Run | |
| PUR-010 | Purchases | Delete/Edit | Purchase correction | Edit or delete purchase record | Linked source transaction, stock movement, and valuation data update without duplication | Not Run | |

### Inventory

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| INV-001 | Inventory | Item Master | Add item | Create stock item with SKU/name/category/unit | Item appears in inventory list and item selectors | Not Run | |
| INV-002 | Inventory | Category Setup | Add category | Create category with sales/purchase scope and default VAT | Category is saved and available in item/purchase flows | Not Run | |
| INV-003 | Inventory | Unit Setup | Add unit | Create unit with code, name, type, decimals | Unit is saved and available in item/purchase flows | Not Run | |
| INV-004 | Inventory | Unit Conversion | BOX to PCS conversion | Purchase 1 BOX where 1 BOX equals 12 PCS | Stock movement stores 12 PCS and purchase line stores 1 BOX | Not Run | |
| INV-005 | Inventory | Stock Dashboard | Current stock | Save purchase and open stock dashboard | Current stock reflects database stock movements | Not Run | |
| INV-006 | Inventory | Valuation | Weighted average cost | Create purchase receipt and review valuation | Unit cost and remaining quantity are correct | Not Run | |
| INV-007 | Inventory | Sales Issue | Stock reduction | Sell stock tracked item | Stock reduces from base unit and COGS is calculated where implemented | Not Run | |
| INV-008 | Inventory | Negative Stock | Prevent oversell | Attempt to sell more than available stock | Sale is blocked unless negative stock is enabled | Not Run | |
| INV-009 | Inventory | Adjustment | Stock adjustment approval | Create adjustment in/out | Approval/audit requirement is enforced and movement is recorded after approval | Not Run | |
| INV-010 | Inventory | Mapping | Product mapping save | Map purchase product to generated stock name and pricing | Mapping saves as Mapped and search/sort remain correct | Not Run | |

### Expenses

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| EXP-001 | Expenses | Create | Create expense | Add expense with category, supplier/payee, date, amount, VAT, evidence | Expense appears in list with correct total and status | Not Run | |
| EXP-002 | Expenses | Approval | Approve expense | Submit expense for approval and approve | Status changes correctly and audit log is created | Not Run | |
| EXP-003 | Expenses | VAT | Recoverable/non-recoverable VAT | Create taxable and blocked/non-business expense | VAT treatment is correct and reportable tax lines are controlled | Not Run | |
| EXP-004 | Expenses | Evidence | Attachment required | Attempt VAT expense without supporting document | Missing evidence is blocked or exception is created where required | Not Run | |

### Bank Accounts and Payments

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| BNK-001 | Bank | Accounts | Add bank account | Create bank account with account name, bank, IBAN/account number | Bank account appears in list | Not Run | |
| BNK-002 | Bank | Transactions | Add transaction | Add bank transaction or statement row | Transaction appears with amount, date, reference, and status | Not Run | |
| BNK-003 | Bank | Reconciliation | Match transaction | Match bank transaction to invoice/payment | Matched status is saved and unmatched list updates | Not Run | |
| PAY-001 | Payments | Customer Receipt | Record receipt | Record customer payment against invoice | AR reduces and bank/cash increases where implemented | Not Run | |
| PAY-002 | Payments | Supplier Payment | Record supplier payment | Pay supplier bill/purchase | AP reduces and bank/cash decreases where implemented | Not Run | |
| PAY-003 | Payments | Duplicate Payment | Duplicate prevention | Attempt same payment twice | Duplicate is rejected or flagged for review | Not Run | |

### Accounting

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ACC-001 | Accounting | Chart of Accounts | Account list | Open Chart of Accounts | Accounts load with code, name, type, and balance | Not Run | |
| ACC-002 | Accounting | Journal Entry | Balanced journal | Create journal with equal debit and credit | Journal posts and appears in ledger | Not Run | |
| ACC-003 | Accounting | Journal Entry | Unbalanced journal | Create journal with unequal debit and credit | Posting is blocked | Not Run | |
| ACC-004 | Accounting | Ledger | Ledger filtering | Filter ledger by account or reference | Ledger shows only matching posted entries | Not Run | |
| ACC-005 | Accounting | Duplicate Posting | Idempotency | Attempt to post same source transaction twice | Duplicate journal is prevented | Not Run | |
| ACC-006 | Accounting | Reversal | Reverse posted journal | Create reversal for a posted journal | Reversal is balanced, linked to original, and audited | Not Run | |
| ACC-007 | Accounting | Period Lock | Locked period posting | Lock accounting period, then attempt posting | Posting is rejected except approved corrections | Not Run | |
| ACC-008 | Accounting | Posting Retry | Failed posting retry | Retry a failed posting job | Retry is idempotent and final status is recorded | Not Run | |

### Corporate Accounting

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CORP-001 | Corporate Accounting | Corporate Tax | Load corporate tax records | Open Corporate Accounting, Corporate Tax tab | Records load from backend and totals display correctly | Not Run | |
| CORP-002 | Corporate Accounting | Fixed Assets | Asset register | Review or add asset record | Asset cost, depreciation, and status are correct | Not Run | |
| CORP-003 | Corporate Accounting | Accruals/Prepayments | Schedule review | Create or review accrual/prepayment | Recognition schedule and accounting impact are correct | Not Run | |
| CORP-004 | Corporate Accounting | Cost Centers | Cost center report | Review cost center records | Cost allocations are visible and totals are accurate | Not Run | |
| CORP-005 | Corporate Accounting | Budgets | Budget comparison | Review budget records | Budget, actual, variance, and status are correct | Not Run | |
| CORP-006 | Corporate Accounting | Month End Close | Close controls | Review close checklist and approval status | Close items, blockers, and approvals are visible | Not Run | |

### VAT and Tax

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TAX-001 | VAT | Output VAT | Sales tax line | Post taxable sales invoice | VAT_OUTPUT_5 tax line is created with correct taxable amount and tax amount | Not Run | |
| TAX-002 | VAT | Input VAT | Purchase tax line | Post taxable purchase invoice | VAT_INPUT_5 tax line is created with correct recoverable amount | Not Run | |
| TAX-003 | VAT | Exempt/Zero Rated | Special tax treatment | Create exempt or zero-rated transaction where supported | Tax code is correct and VAT amount is zero | Not Run | |
| TAX-004 | VAT | Reverse Charge | Reverse charge purchase | Create reverse charge transaction where supported | Output and input reverse charge tax lines are recorded correctly | Not Run | |
| TAX-005 | VAT | VAT Report | Report source | Open VAT report | Report reads tax lines and does not recalculate from raw invoice rows as source of truth | Not Run | |
| TAX-006 | VAT | VAT Return | Generate return | Generate VAT return for period | Output, input, adjustments, and payable/refundable total are correct | Not Run | |
| TAX-007 | VAT | Period Lock | Locked VAT period | Lock VAT period and attempt change to tax line | Change is rejected or handled through adjustment | Not Run | |
| TAX-008 | VAT | Evidence | Evidence link | Review VAT claim with attached document | Tax evidence is linked and traceable | Not Run | |
| TAX-009 | VAT | Exceptions | VAT mismatch | Create or import transaction with tax mismatch | VAT mismatch exception is created | Not Run | |

### Reports

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| REP-001 | Reports | VAT Report | Open VAT report | Navigate to Reports, VAT tab | VAT report loads from backend/database values | Not Run | |
| REP-002 | Reports | Profit and Loss | P&L totals | Review P&L report | Revenue, expenses, payroll, and net profit are calculated correctly | Not Run | |
| REP-003 | Reports | Balance Sheet | Balance sheet totals | Review balance sheet where available | Assets equal liabilities plus equity | Not Run | |
| REP-004 | Reports | Trial Balance | Trial balance | Review trial balance | Total debit equals total credit | Not Run | |
| REP-005 | Reports | Aging | Customer/supplier aging | Open aging reports | Buckets and totals match source transactions | Not Run | |
| REP-006 | Reports | Export | Export report PDF/print | Export active report and all reports | Export includes correct title, date, data, and formatting | Not Run | |
| REP-007 | Reports | AI Insights | Generate AI report insight | Run AI insight on report | AI narrative is generated without changing accounting data | Not Run | |

### Documents and Evidence

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DOC-001 | Documents | Upload | Upload file | Upload supported file type | Document appears in document list with metadata | Not Run | |
| DOC-002 | Documents | Validation | Unsupported file | Upload unsupported or invalid file type | Upload is rejected safely | Not Run | |
| DOC-003 | Documents | Link Evidence | Link to source record | Link document to invoice, purchase, VAT, journal, or payroll run | Link is saved and visible from source record | Not Run | |
| DOC-004 | Documents | Extraction | Extract document data | Run extraction on invoice/receipt document | Extracted fields appear for review with confidence or validation status | Not Run | |
| DOC-005 | Documents | Audit Pack | Evidence review | Review audit evidence pack | Required VAT/accounting/payroll evidence is present and traceable | Not Run | |

### Exception Center

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| EXC-001 | Exception Center | Load | Open exceptions | Navigate to Exception Center | Open exceptions load with module, category, severity, source, owner, and status | Not Run | |
| EXC-002 | Exception Center | Failed Posting | Failed posting exception | Force or locate failed posting | Failed posting appears with retry or resolution action | Not Run | |
| EXC-003 | Exception Center | Duplicate | Duplicate invoice exception | Trigger duplicate invoice/purchase | Duplicate appears in Exception Center where implemented | Not Run | |
| EXC-004 | Exception Center | OCR Failure | Extraction failure | Upload unreadable document | OCR failure exception is created | Not Run | |
| EXC-005 | Exception Center | Resolution | Resolve exception | Resolve, skip, retry, or escalate exception | Status updates and resolution audit log is created | Not Run | |
| EXC-006 | Exception Center | AI Explanation | Explain exception | Click AI/explain action | Suggested explanation/actions appear without changing exception status | Not Run | |

### AI/OCR Assistant

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AI-001 | AI Assistant | TaxFlow Question | Ask workflow question | Ask about invoice, VAT, purchase, accounting, payroll, or settings workflow | Assistant returns relevant guidance and does not approve/post transactions | Not Run | |
| AI-002 | AI Assistant | Backend Fallback | Backend AI unavailable | Disconnect backend AI and ask question | Local fallback answer is shown gracefully | Not Run | |
| AI-003 | AI/OCR | Field Extraction | Extract invoice fields | Upload known sample invoice | Invoice number, party, TRN, date, subtotal, VAT, total are extracted correctly | Not Run | |
| AI-004 | AI/OCR | Confidence | Low confidence review | Upload poor quality file | Low confidence row is held for human review | Not Run | |
| AI-005 | AI/OCR | Audit | Accept/reject extraction | Accept or reject extracted row | Action is logged for audit where implemented | Not Run | |

### Staff, HR, and Attendance

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| HR-001 | Staff | Employee Master | Add employee | Create employee with personal, employment, bank, and document details | Employee record is saved and visible | Not Run | |
| HR-002 | Staff | Employee Validation | Required employee data | Save employee with missing mandatory fields | Save is blocked with validation | Not Run | |
| HR-003 | Staff | Attendance | Attendance record | Add or review check-in/check-out | Attendance record calculates hours and status correctly | Not Run | |
| HR-004 | Staff | Leave | Leave request | Submit and approve/reject leave | Leave status updates and audit log is created | Not Run | |
| HR-005 | Staff | Documents | Employee documents | Upload or export document checklist | Documents/checklist are associated with employee | Not Run | |

### Rota Planning

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| ROTA-001 | Rota | Shift Setup | Create shift | Add shift with start/end time and department | Shift is saved and available in rota planning | Not Run | |
| ROTA-002 | Rota | Weekly Rota | Assign staff | Assign employees to shifts | Rota shows correct staff, dates, and coverage | Not Run | |
| ROTA-003 | Rota | Conflicts | Conflict detection | Assign overlapping shifts or leave conflict | Conflict is flagged before publish | Not Run | |
| ROTA-004 | Rota | Publish | Publish rota | Publish approved rota | Published rota becomes read-only or controlled by change approval | Not Run | |
| ROTA-005 | Rota | Attendance Link | Rota to attendance | Review attendance against published rota | Late/early/overtime calculations use published shift data where implemented | Not Run | |

### Payroll and WPS/SIF

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PAYR-001 | Payroll | Payroll Run | Calculate payroll | Open Payroll and run payroll calculation | Gross, deductions, net pay, and totals are calculated | Not Run | |
| PAYR-002 | Payroll | Validation | Missing bank details | Include employee with missing bank/WPS data | WPS hold or validation warning is shown | Not Run | |
| PAYR-003 | Payroll | Approval | Approve payroll | Approve calculated payroll | Approval status updates and audit log is created | Not Run | |
| PAYR-004 | Payroll | Duplicate Run | Duplicate prevention | Attempt duplicate payroll run for same period | Duplicate is blocked or flagged | Not Run | |
| PAYR-005 | Payroll | Payslip | Payslip preview | Open payslip for employee | Payslip shows correct salary, deductions, net pay, and period | Not Run | |
| PAYR-006 | Payroll | WPS/SIF | Generate SIF preview | Generate WPS/SIF file or preview | Employee lines, employer IDs, totals, and validation errors are correct | Not Run | |
| PAYR-007 | Payroll | Accounting | Post payroll journal | Post approved payroll | Journal is balanced and linked to payroll run | Not Run | |
| PAYR-008 | Payroll | Period Lock | Locked payroll period | Lock payroll period and attempt change | Change is rejected or controlled by approval | Not Run | |

### Expert Review

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| EXPERT-001 | Expert Review | New Request | Submit review request | Create expert review request with module, notes, and evidence | Request is saved with correct status and owner | Not Run | |
| EXPERT-002 | Expert Review | Evidence | Attach files | Attach invoice/report/document to review request | Evidence is linked and visible | Not Run | |
| EXPERT-003 | Expert Review | Status | Update review status | Change status from submitted to in review/resolved | Status history and audit log are created | Not Run | |

### Notifications

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| NOTIF-001 | Notifications | Alerts | Generate workflow alert | Trigger approval, exception, or due date alert | Notification appears for assigned user | Not Run | |
| NOTIF-002 | Notifications | Read Status | Mark as read | Open and mark notification as read | Read status persists after refresh | Not Run | |
| NOTIF-003 | Notifications | Routing | Role based notification | Trigger event assigned to accountant/admin/approver | Correct role receives notification only | Not Run | |

### Security and Access Control

| Test ID | Module | Sub Module | Scenario | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SEC-001 | Security | Tenant Isolation | Cross-company records | Attempt to access another company's record through UI/API | Access is denied and data is not exposed | Not Run | |
| SEC-002 | Security | API Auth | Missing token | Call protected API without token | API returns unauthorized response | Not Run | |
| SEC-003 | Security | Permissions | Restricted action | Attempt admin/accounting/payroll action with lower role | Action is denied | Not Run | |
| SEC-004 | Security | Upload Security | Malicious upload | Upload executable or dangerous file type | Upload is rejected and logged | Not Run | |
| SEC-005 | Security | Session | Session expiry/logout | Logout or expire token, then access protected page/API | User is redirected or request is rejected | Not Run | |
| SEC-006 | Security | Sensitive Audit | Permission or setting change | Change user role, VAT setting, period lock, or approval rule | Old value, new value, user, time, and reason are logged where implemented | Not Run | |

## End-to-End Business Workflows

| Test ID | Workflow | Modules Covered | Steps | Expected Result | Status | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| E2E-001 | Sales to Ledger to VAT | Sales, Source Transactions, Accounting, VAT, Reports, Audit | Create sales invoice, validate, approve/post, review journal, tax line, VAT report, audit | Invoice is posted once, journal is balanced, VAT output is correct, report updates | Not Run | |
| E2E-002 | Purchase to Stock to VAT | Purchases, Inventory, Accounting, VAT, Documents | Upload or create purchase, validate supplier/TRN, save, review stock, post, review VAT input and evidence | Purchase updates stock, creates balanced journal, and supports input VAT claim | Not Run | |
| E2E-003 | Inventory Sale With COGS | Inventory, Sales, Accounting, Reports | Purchase stock, sell item, review stock reduction, COGS, inventory valuation | Quantity and cost flow are correct and no negative stock occurs | Not Run | |
| E2E-004 | Exception Resolution | Sales/Purchases, Exception Center, Audit | Trigger duplicate/OCR/VAT/posting error, resolve or retry | Exception appears, resolution updates status, and audit trail is complete | Not Run | |
| E2E-005 | Payroll to Accounting | HR, Payroll, WPS, Accounting, Reports | Create employee, run payroll, validate WPS, approve, post journal, review reports | Payroll controls pass, journal balances, and reports update | Not Run | |
| E2E-006 | Period Close Control | Accounting, VAT, Payroll, Inventory | Lock period and attempt backdated changes in each area | Locked period rejects unauthorized changes and records audit events | Not Run | |

## Manual Test Execution Summary

| Module | Total Cases | Passed | Failed | Blocked | Not Run | Tester Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Global | | | | | | |
| Dashboard | | | | | | |
| Settings | | | | | | |
| Sales and Invoices | | | | | | |
| Quotations | | | | | | |
| Purchases | | | | | | |
| Inventory | | | | | | |
| Expenses | | | | | | |
| Bank and Payments | | | | | | |
| Accounting | | | | | | |
| Corporate Accounting | | | | | | |
| VAT and Tax | | | | | | |
| Reports | | | | | | |
| Documents | | | | | | |
| Exception Center | | | | | | |
| AI/OCR Assistant | | | | | | |
| Staff/HR | | | | | | |
| Rota | | | | | | |
| Payroll/WPS | | | | | | |
| Expert Review | | | | | | |
| Notifications | | | | | | |
| Security | | | | | | |
| End-to-End | | | | | | |

## Defect Log

| Defect ID | Test ID | Module | Severity | Summary | Steps to Reproduce | Expected | Actual | Owner | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| | | | | | | | | | |

## Sign-Off

| Role | Name | Decision | Date | Comments |
| --- | --- | --- | --- | --- |
| QA Tester | | | | |
| Product Owner | | | | |
| Engineering Lead | | | | |
| Business/Accounting Reviewer | | | | |
