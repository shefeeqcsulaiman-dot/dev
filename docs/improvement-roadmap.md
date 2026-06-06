# TaxFlow Improvement Roadmap

This file tracks the full improvement backlog. The app now runs as a React + Vite frontend with a FastAPI backend, local SQLite development mode, API-backed app data, module APIs for purchases/items/units/settings, sales and purchase AI upload validation tables, invoice source tracking, an Exception Center, corporate accounting modules, event/outbox tables, stock unit/costing tables, reporting snapshot tables, audit detail tables, and a repeatable 50-record seed dataset. The remaining items below need deeper frontend refactoring, stronger backend services, and production controls.

## Frontend Foundation

- Replace inline HTML event handlers with delegated listeners in `src/app.js`.
- Split `src/app.js` into modules for navigation, modals, toasts, sales, purchases, payroll, reports, settings, validation, and API clients.
- Replace large `innerHTML` templates with small rendering helpers or component functions.
- Add keyboard-first accessibility: focus trapping in modals, ARIA labels for icon buttons, visible focus states, and table captions.
- Add sortable, searchable, paginated tables across invoices, purchases, expenses, payroll, reports, and audit logs.
- Add empty, loading, success, warning, and error states for every module.

## Product Workflows

- Add full create, edit, delete, approve, and restore workflows for invoices, purchases, expenses, journals, inventory items, employees, and payroll runs.
- Generate invoice PDFs, payslip PDFs, report PDFs, VAT return exports, and Excel exports.
- Add customer and supplier ledgers, payment allocation, reminders, and receivables aging actions.
- Add UAE VAT return preparation with source drill-down and validation before export.
- Convert corporate accounting screens from static MVP tables to full create, edit, approve, post, and report workflows.
- Add posting engines for depreciation, accrual reversal, bad debt write-off, budget approvals, and consolidation eliminations.
- Add bank statement import, MT940/CSV parsing, and reconciliation matching rules.
- Add WPS/SIF export with employee bank validation and approval holds.
- Extend AI upload validation from client-side checks to server-side validation jobs with persisted validation results.
- Add correction/edit workflows directly inside AI upload validation rows before Save All.

## Backend And Data

- Move legacy UI saves from `/api/v1/app-data?action=save` to the live module APIs now available for purchases, items, units, and settings.
- Replace demo `/app-data?action=documents.extract` and `/app-data?action=invoices.import` responses with production OCR/import services.
- Add authentication, session management, MFA, roles, and permissions.
- Add tenant/company scoping for every record.
- Persist records in a database with database-level unique constraints and soft deletes.
- Store uploaded files in object storage, not browser memory.
- Add backend validation schemas for every API request.
- Add database unique constraints for invoice references and purchase references per company.
- Persist AI extraction batches, row-level validation results, skipped rows, and save decisions.
- Add background jobs for OCR, import, exports, emails, reminders, and backups.
- Add webhook and adapter services for bank feeds, eInvoicing, messaging, and accounting integrations.
- Replace local SQLite development storage with PostgreSQL for production and remove SQLite-only journal settings.
- Add migration management for all live tables, including exception events, domain events, audit details, stock unit/costing tables, and reporting snapshots.

## Security And Compliance

- Sanitize all user/API-rendered content and avoid direct HTML injection.
- Add upload size limits, MIME validation, antivirus scanning hooks, and file retention policy.
- Add audit logs for edits, approvals, exports, logins, failed logins, API key rotation, backups, and permission changes.
- Encrypt sensitive company, tax, bank, payroll, and integration data.
- Add CSRF protection if cookie auth is used.
- Add API rate limits and permission checks on every endpoint.

## Reporting And AI

- Move AI report generation to a backend service.
- Base AI insights on normalized ledger, invoice, payroll, bank, and VAT data.
- Add explainable anomaly detection with links to source transactions.
- Add cash-flow forecasting, VAT forecasting, scenario modeling, and period comparisons.
- Add report packs for management, audit, VAT filing, payroll, and receivables.

## Engineering Quality

- Add ESLint and Prettier once package dependencies are allowed.
- Add unit tests for VAT calculations, invoice totals, payroll totals, gratuity, WPS validation, AI upload validation, and duplicate detection.
- Add browser smoke tests for navigation, sales uploads, purchase uploads, AI validation, modals, payroll, and reports.
- Add CI checks for JavaScript syntax, asset references, tests, and bundle size.
