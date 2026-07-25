# TaxFlow HRMS Architecture

This document describes the HRMS module in detail. It complements `docs/architecture.md` §15–17 and §22 (HR/Rota/Payroll/WPS), which cover HRMS at whole-system level. This file is the authoritative HRMS-specific reference.

HRMS is served standalone at `https://app.etaxflow.com/hrms` (`frontend/public/taxflow/hrms.html`), separate from the main TaxFlow shell, with its own sidebar/topbar but sharing `styles.css`, auth, and the FastAPI backend.

## 1. Core Architectural Fact: Two-Tier Persistence

HRMS today has two different persistence tiers, and knowing which tier a feature is on determines what work is required to make it production-real.

```text
Tier 1 — Real ORM tables (backend/app/models.py)
  Employee, PayrollRun, PayrollItem, WpsBatch,
  BiometricDevice, AttendancePunch
  -> proper SQLAlchemy models, real routers, real business logic

Tier 2 — JSON bridge collections (app_data_records via saveServer())
  leaveRequests, employeeLoans, salaryAdvances, jobRequisitions,
  candidates, overtimeRequests, interviews, offerLetters, onboarding,
  performanceReviews, trainingPrograms, assets, expiryAlerts
  -> stored as opaque JSON payloads under a single generic table,
     no schema, no FK integrity, no dedicated validation/reporting
```

`app_data_records` is the same prototype compatibility bridge flagged in `docs/architecture.md` §23 as something production must remove. Every HRMS module built on Tier 2 inherits that debt: it works for demo/UI purposes but has none of the guarantees (referential integrity, tenant-safe indexing, typed validation, report-grade querying) that Tier 1 has.

**Rule going forward:** new HRMS features should default to Tier 1 (real tables + Pydantic schemas + router). Tier 2 is acceptable only for genuinely low-stakes, high-churn UI state.

## 2. Module Map

```text
HRMS
|-- Core HR
|   |-- Employee Master           [Tier 1: Employee]
|   |-- Attendance                [Tier 1: AttendancePunch, BiometricDevice]
|   |-- Leave Management          [Tier 2: leaveRequests]
|   |-- Overtime                  [Tier 2: overtimeRequests]
|   |-- Corrections               [Tier 2, via attendance UI]
|   |-- Loans & Advances          [Tier 2: employeeLoans, salaryAdvances]
|   |-- Expiry Alerts             [derived — reads Employee document fields, no own table]
|   |-- HR Settings (roles)       [Tier 2]
|   `-- Biometric Devices         [Tier 1: BiometricDevice]
|
|-- Payroll & WPS
|   |-- Payroll Runs              [Tier 1: PayrollRun, PayrollItem]
|   `-- WPS / SIF Export          [Tier 1: WpsBatch]
|
|-- Access Control & GPS Attendance   [planned — not yet built]
|   |-- Employee Login (username/password, role-scoped) [extends Employee + ess.py]
|   |-- RBAC (Roles, Permissions, Role Permissions)
|   |-- Role-Based Dashboards (Admin/HR/Payroll/Manager/Employee)
|   |-- Company Locations & Geofencing
|   |-- GPS Check-In/Check-Out
|   |-- Live Employee Location Tracking
|   `-- Automatic Check-Out on geofence exit
|
|-- Recruitment
|   |-- Job Requisitions          [Tier 2: jobRequisitions]
|   |-- Candidates                [Tier 2: candidates]
|   |-- Interviews                [Tier 2: interviews]
|   |-- Offer Letters             [Tier 2: offerLetters]
|   `-- Onboarding                [Tier 2: onboarding]
|
|-- Extended HR
|   |-- Performance (KPI/Appraisal) [Tier 2: performanceReviews]
|   |-- Training & Certification  [Tier 2: trainingPrograms]
|   |-- Asset Management          [Tier 2: assets]
|   |-- ESS Portal                [Tier 1 auth + Tier 1 reads: ess.py]
|   `-- Manager Portal            [reads across Tier 1 + Tier 2]
|
|-- AI (page-hrms-ai)
|   |-- CV Parsing                [hr_ai.py — stateless, no persistence]
|   |-- Payroll Anomaly Detection [hr_ai.py — reads PayrollItem]
|   |-- Attrition Risk            [hr_ai.py — reads Employee/attendance]
|   |-- Compliance Check          [hr_ai.py — stateless]
|   |-- Leave Pattern Analysis    [hr_ai.py — reads leaveRequests bridge]
|   |-- JD Generator              [hr_ai.py — stateless]
|   `-- HR Chatbot                [hr_ai.py — stateless]
|
`-- Org Chart (page-hrms-org)     [derived from Employee.department/designation/reporting_manager]
```

## 3. Frontend Structure

```text
frontend/public/taxflow/hrms.html
|-- page-hrms         Hub: 15+ module cards, UAE Compliance panel, section headers
|-- page-staff        Tabs: Employees, Attendance, Overtime, Leave Management,
|                            Corrections, Loans & Advances, Expiry Alerts,
|                            HR Settings, Biometric
|-- page-recruitment  Tabs: Job Requisitions, Candidates, Interviews,
|                            Offer Letters, Onboarding
|-- page-hrms-ext     Tabs: Performance, Training, Asset Management,
|                            ESS Portal, Manager Portal
|-- page-hrms-ai      AI Workbench: CV parsing, attrition, compliance, chatbot
`-- page-hrms-org     Org chart view (derived, read-only)

frontend/public/taxflow/src/app.js  (shared with main app, ~16,000 lines)
|-- HRMS-specific functions live in this single file, not a separate module.
|-- saveServer(collection, record) -> POST /api/v1/app-data (Tier 2 write path)
|-- Key functions: updateOtMultiplier(), previewEmpPhoto(), refreshExpiryAlerts(),
|   calcLoanEmi(), saveLoan(), saveLoanAdvance(), saveJobRequisition(),
|   saveCandidate(), filterCandidates(), refreshRecruitmentStats(),
|   refreshManagerPortalCounts()
```

There is no per-module frontend file split for HRMS yet — everything lives in `app.js` alongside the rest of TaxFlow. `docs/architecture.md` Phase 1 already calls for splitting `app.js` by module; HRMS should get its own `hrms.js` bundle when that happens.

## 4. Backend Structure

```text
backend/app/routers/
|-- attendance.py   /api/v1/attendance/*   — devices, punch import, trend, summary
|-- payroll.py      /api/v1/payroll/*      — employees, runs, generate, wps-batch
|-- ess.py          /api/v1/ess/*          — employee self-service (JWT-scoped to employee, not user)
`-- hr_ai.py         /api/v1/ai/hr/*        — CV parse, anomaly/attrition/compliance/leave/JD/chatbot

backend/app/models.py
|-- Employee            employees table       (Tier 1)
|-- PayrollRun          payroll_runs table    (Tier 1)
|-- PayrollItem         payroll_items table   (Tier 1)
|-- WpsBatch            wps_batches table     (Tier 1)
|-- BiometricDevice     biometric_devices     (Tier 1)
`-- AttendancePunch     attendance_punches    (Tier 1, indexed by company_id+punch_date)

backend/app/routers/app_data.py (or main.py bridge)
`-- Everything else (leave, loans, recruitment, performance, training, assets)
    persists as JSON rows keyed by `collection` in app_data_records.
```

## 5. Current Data Model (Tier 1 — real tables)

```text
employees
  id, company_id, employee_no, full_name, department, designation,
  basic_salary, iban, wps_id, status, password_hash (for ESS login)

payroll_runs
  id, company_id, period, status, gross_total, deductions_total, net_total

payroll_items
  id, run_id, employee_id, basic, allowances, overtime, deductions,
  net_pay, wps_status

wps_batches
  id, company_id, payroll_run_id, batch_number, status, sif_content

biometric_devices
  id, company_id, name, device_type, ip_address, port, location,
  api_key_hash, status, last_sync

attendance_punches
  id, company_id, employee_id, employee_name, punch_time, punch_date,
  direction, device_id, device_name, source
```

Gap vs. the rich employee form the frontend already collects (Emirates ID, nationality, DOB, gender, marital status, employment type, join date, branch, OT rate, leave policy, reporting manager, shift, work location, work permit, visa expiry, cost center, emergency contact, insurance, passport/EID/labor card/driving license numbers and expiries): **none of these fields exist on the `Employee` ORM model today.** The employee form in the UI is wider than what the backend `Employee` table can actually store, so most of that detail is either dropped or shoved into the Tier 2 bridge under the employee's `app_data_records` row rather than the `employees` table.

There is a second gap on top of that: `employees` has no `username`, no `role_id`, and no work-location assignment. `password_hash` exists today only for the separate ESS employee-portal login (`ess.py`), which issues an employee-scoped JWT with no role claims. There is no company-side concept of roles/permissions, no company location/geofence records, and no GPS-based attendance path — attendance today is device/CSV punch-based only (`attendance_punches`), not employee-initiated GPS check-in. §6, §7, and §8 below define the tables, flows, and endpoints to close this.

## 6. Recommended Data Model (close the gap)

To move Recruitment, Leave, Loans, Performance, Training, and Assets to Tier 1:

```text
employees (extend existing table, do not create a parallel one)
  + emirates_id, nationality, date_of_birth, gender, marital_status
  + employment_type, join_date, branch_id, ot_rate, leave_policy_id
  + reporting_manager_id, shift_id, work_location_id, work_permit_no
  + visa_expiry_date, cost_center, emergency_contact_name/phone
  + insurance_type, insurance_policy_no, insurance_expiry_date
  + passport_no, passport_expiry_date, labor_card_no
  + driving_license_no, driving_license_expiry_date, photo_url
  + username, role_id, is_active, last_login, last_activity,
    password_changed_at
    (password_hash already exists — reused for this login, not duplicated;
    ess.py's employee-scoped JWT should be extended to carry role_id once
    roles exist, rather than building a second auth table)

roles
  id, company_id, role_name, description, is_system_role

permissions
  id, module, permission_name

role_permissions
  role_id, permission_id

company_locations
  id, company_id, location_name, branch_id, address,
  latitude, longitude, allowed_radius_meters, status

employee_locations
  employee_id, location_id, is_primary

employee_location_logs
  id, employee_id, company_id, latitude, longitude, accuracy,
  inside_geofence, device, battery, timestamp

attendance_sessions
  id, employee_id, company_id, check_in, check_out,
  check_in_location, check_out_location, auto_checkout, status
  (paired session view over GPS check-in/out; attendance_punches remains
  the raw event log for device/CSV punches — see §7 for how the two relate)

leave_types
  id, company_id, name, paid, annual_entitlement_days, status

leave_requests
  id, company_id, employee_id, leave_type_id, start_date, end_date,
  days, reason, status, approved_by, approved_at

overtime_requests
  id, company_id, employee_id, ot_type, date, hours, multiplier,
  computed_amount, status, approved_by

employee_loans
  id, company_id, employee_id, loan_type, amount, months, emi,
  reason, start_date, status, approved_by

salary_advances
  id, company_id, employee_id, month, amount, reason, status, approved_by

job_requisitions
  id, company_id, title, department, positions, employment_type,
  location, target_date, salary_min, salary_max, description, status

candidates
  id, company_id, requisition_id, name, mobile, email, position,
  nationality, experience_years, expected_salary, visa_status,
  source, stage, cv_document_id

interviews
  id, company_id, candidate_id, round, interviewer_id, scheduled_at,
  status, feedback, score

offer_letters
  id, company_id, candidate_id, position, salary, start_date,
  status, issued_at, accepted_at

onboarding_tasks
  id, company_id, candidate_id, employee_id, step, status,
  completed_at, completed_by

performance_cycles
  id, company_id, name, stage, start_date, end_date, status

performance_reviews
  id, company_id, cycle_id, employee_id, reviewer_id, kpi_scores_json,
  overall_rating, comments, status

training_programs
  id, company_id, name, category, start_date, end_date, provider, status

employee_training_records
  id, company_id, employee_id, program_id, completion_status,
  certificate_expiry_date, score

assets
  id, company_id, asset_type, asset_tag, description, status

asset_assignments
  id, company_id, asset_id, employee_id, assigned_date,
  returned_date, condition_notes
```

Every table above follows the tenant-isolation rule from `docs/architecture.md` §21: `company_id` on every row, `created_by`/`updated_by`/timestamps via the existing `TimestampMixin`.

## 7. Data Flow

### Attendance → Payroll (already real, Tier 1)

```text
Biometric Device / Manual Punch / CSV Import
        |
        v
POST /api/v1/attendance/punch(-{device_key})  or  /import-csv
        |
        v
attendance_punches (company_id, employee_id, punch_date indexed)
        |
        v
GET /attendance/summary, /trend, /today
        |
        v
Payroll generation reads attendance for OT/absence
        |
        v
POST /api/v1/payroll/generate -> payroll_runs + payroll_items
        |
        v
POST /payroll/runs/{id}/wps-batch -> wps_batches (SIF content)
```

### Leave / Loans / Recruitment (current — Tier 2, target — Tier 1)

```text
Current:
UI form -> saveServer(collection, record) -> POST /api/v1/app-data
        -> app_data_records row (payload = JSON, no schema)
        -> read back via GET /api/v1/app-data/records/{collection}

Target:
UI form -> POST /api/v1/hr/leave-requests (or /loans, /recruitment/*)
        -> Pydantic schema validation
        -> dedicated table (see §6)
        -> triggers domain event (LeaveApproved, LoanApproved) per
           docs/architecture.md §20.2 event system
        -> payroll/reports read from the real table, not JSON payload
```

### ESS Portal

```text
Employee login (employee_no + password_hash)
        |
        v
POST /api/v1/ess/login -> JWT scoped to employee_id (not a user account)
        |
        v
GET /ess/me, /ess/attendance, /ess/payslips
        |
        v
Employee-only views over Tier 1 tables (attendance_punches, payroll_items)
        |
        v
Leave/OT requests submitted from ESS still land in Tier 2 today —
should point at the same /api/v1/hr/leave-requests endpoint once built,
so ESS and HR admin read/write the same table instead of two paths.
```

### Employee Login & RBAC (planned)

```text
HR Admin
        |
        v
Create Employee -> assign username, password, role_id, work location
        |
        v
Employee Login (username + password)
        |
        v
JWT issued with employee_id + role_id claims
        |
        v
Role Lookup (role_permissions)
        |
        v
Role-Based Dashboard: Administrator / HR / Payroll / Manager / Employee
        |
        v
Every module route checked against permissions for that role
```

This extends the existing ESS login rather than replacing it: `ess.py` already issues an employee-scoped JWT for the self-service portal. Once `role_id` exists, that same token gains a role claim, and HR-admin-facing routes gain permission checks — there is one employee identity and one login system, not two.

### GPS Attendance Flow (planned)

```text
Employee Login
        |
        v
Browser/app requests GPS permission
        |
        v
Current lat/long captured
        |
        v
Compare against employee_locations -> company_locations.allowed_radius_meters
        |
        v
Inside radius? --YES--> Allow Check-In -> attendance_sessions row opened
        |
        NO
        v
Reject: "Outside company location"
```

### Live Employee Tracking (planned)

```text
Employee checked in (attendance_sessions.status = open)
        |
        v
GPS ping every ~2 minutes -> employee_location_logs
        |
        v
HR Dashboard reads latest employee_location_logs per employee
```

### Automatic Check-Out (planned)

```text
Employee checked in
        |
        v
GPS monitoring via employee_location_logs
        |
        v
Distance from assigned company_locations > allowed_radius_meters
        |
        v
Grace period elapses (configurable, e.g. 2-5 min) still outside?
        |
        YES
        v
attendance_sessions.check_out set, auto_checkout = true
        |
        v
Notify employee + HR
```

Grace period exists specifically to avoid closing a session on transient GPS drift near the geofence boundary — a hard cutoff at the radius edge would produce false checkouts.

### Expiry Alerts (derived, not a source of truth)

```text
refreshExpiryAlerts() reads DOM data-employee attributes
        |
        v
Computes days-left for visa/passport/EID/insurance from Employee fields
        |
        v
Renders KPI buckets (Critical <=30, Warning <=90, Valid, Missing)
```

Once employee expiry fields move to real ORM columns (§6), this should become a backend query (`GET /api/v1/hr/expiry-alerts`) instead of a DOM scrape, so it works from any client and can drive actual notifications.

## 8. API Surface

### Live today

```text
POST   /api/v1/attendance/punch
POST   /api/v1/attendance/punch/{device_key}
POST   /api/v1/attendance/import-csv
GET    /api/v1/attendance/today
GET    /api/v1/attendance/trend
GET    /api/v1/attendance/punches
GET    /api/v1/attendance/summary
GET/POST /api/v1/attendance/devices
DELETE /api/v1/attendance/devices/{id}
POST   /api/v1/attendance/devices/{id}/test
GET    /api/v1/attendance/bridge-script

GET    /api/v1/payroll/employees
GET    /api/v1/payroll/runs
POST   /api/v1/payroll/generate
POST   /api/v1/payroll/runs/{run_id}/wps-batch

POST   /api/v1/ess/login
GET    /api/v1/ess/me
POST   /api/v1/ess/change-password
GET    /api/v1/ess/attendance
GET    /api/v1/ess/payslips

POST   /api/v1/ai/hr/cv-parse
POST   /api/v1/ai/hr/payroll-anomaly
POST   /api/v1/ai/hr/attrition-risk
POST   /api/v1/ai/hr/compliance-check
POST   /api/v1/ai/hr/leave-analysis
POST   /api/v1/ai/hr/jd-generate
POST   /api/v1/ai/hr/chatbot

(bridge, Tier 2 — used by everything not listed above)
GET/POST /api/v1/app-data
GET      /api/v1/app-data/records/{collection}
```

### Recommended (Tier 1 migration targets)

```text
GET/POST /api/v1/hr/employees            (extend beyond payroll.py's read-only /payroll/employees)

POST     /api/v1/hr/login
POST     /api/v1/hr/logout
GET      /api/v1/hr/me
GET      /api/v1/hr/dashboard            (role-scoped: response shape depends on caller's role)
GET/POST /api/v1/hr/roles
GET      /api/v1/hr/permissions
GET/POST /api/v1/hr/company-locations
PUT/DELETE /api/v1/hr/company-locations/{id}
POST     /api/v1/hr/check-in
POST     /api/v1/hr/check-out
POST     /api/v1/hr/location             (GPS ping while checked in)
GET      /api/v1/hr/live-locations       (HR dashboard feed)

GET/POST /api/v1/hr/leave-types
GET/POST /api/v1/hr/leave-requests
POST     /api/v1/hr/leave-requests/{id}/approve
POST     /api/v1/hr/leave-requests/{id}/reject
GET/POST /api/v1/hr/overtime-requests
POST     /api/v1/hr/overtime-requests/{id}/approve
GET/POST /api/v1/hr/loans
POST     /api/v1/hr/loans/{id}/approve
GET/POST /api/v1/hr/salary-advances
GET      /api/v1/hr/expiry-alerts
GET/POST /api/v1/recruitment/requisitions
GET/POST /api/v1/recruitment/candidates
POST     /api/v1/recruitment/candidates/{id}/stage
GET/POST /api/v1/recruitment/interviews
GET/POST /api/v1/recruitment/offers
GET/POST /api/v1/recruitment/onboarding-tasks
GET/POST /api/v1/hr/performance/cycles
GET/POST /api/v1/hr/performance/reviews
GET/POST /api/v1/hr/training/programs
GET/POST /api/v1/hr/training/records
GET/POST /api/v1/hr/assets
POST     /api/v1/hr/assets/{id}/assign
POST     /api/v1/hr/assets/{id}/return
```

## 9. Security & Tenant Isolation Notes Specific to HRMS

- `ess.py` issues a JWT scoped to `employee_id`, not the normal user/company JWT — routers reading employee-submitted data must not trust `company_id` from the token payload without re-validating the employee belongs to that company, since HRMS is the one module with a second, lower-trust auth path.
- Salary, IBAN, Emirates ID, passport, and insurance fields are PII/financial data — Tier 2 storage as unstructured JSON in `app_data_records` gives none of the column-level access control a real table could have. This is a concrete reason to prioritize the Tier 1 migration for Employee fields specifically, ahead of the lower-sensitivity Recruitment/Training tables.
- Loan/advance approval and leave approval are exactly the kind of "high-risk action" `docs/architecture.md` §19 calls out — once moved to Tier 1, they should get full audit_logs entries (old value/new value/approver/reason), which JSON bridge writes today do not reliably produce.
- Once `role_id` and RBAC exist, every HRMS route must check the caller's permissions server-side, not just hide UI in the frontend for a given role — the frontend role-based dashboard is a UX layer, the backend permission check is the actual control.
- GPS coordinates and location logs are sensitive personal data: transmit over HTTPS only, scope `employee_location_logs` reads to HR/admin roles and the employee themself, and stop writing new location pings once an `attendance_sessions` row is closed (don't track employees after checkout).
- Geofence radius and grace period must be configurable per company, not hardcoded, since office layouts and GPS accuracy vary — a fixed 500m/2min value will false-positive for some sites and under-protect others.
- Every login, logout, check-in, check-out, automatic check-out, and location-permission denial should write to `audit_logs`, since these are the events most likely to be disputed by an employee ("I was never late," "I did check out").

## 10. Build Priority

```text
1. Extend Employee ORM model with the profile fields already collected by
   the UI, plus username/role_id/work_location_id/is_active/last_login
   (§6 employees block) — closes the biggest data-integrity gap first.
2. Build RBAC: roles, permissions, role_permissions, and extend ess.py's
   employee JWT to carry role_id instead of adding a second auth system.
3. Build Company Locations + Geofencing (company_locations,
   employee_locations).
4. Build GPS check-in/check-out (attendance_sessions,
   employee_location_logs) and live tracking on the HR dashboard.
5. Add automatic check-out on geofence exit with a configurable grace
   period.
6. Build role-specific dashboards (Administrator, HR, Payroll, Manager,
   Employee) backed by real permission checks, not frontend-only role
   hiding.
7. Migrate Leave + Overtime + Loans/Advances to Tier 1
   (highest write volume, feeds payroll and ESS).
8. Migrate Recruitment (requisitions -> candidates -> interviews ->
   offers -> onboarding) as one connected pipeline, not five separate tables
   built independently.
9. Migrate Performance + Training + Assets (lower write volume, can lag).
10. Turn Expiry Alerts into a backend query once Employee fields are real
    columns, and wire it into the notification system
    (docs/architecture.md §20.2 event system).
11. Split HRMS frontend logic out of the shared app.js into its own bundle,
    per the Phase 1 module-split guidance in docs/architecture.md.
```

This priority order follows the same principle as the main architecture doc: persist the data correctly before layering more UI on top of it.
