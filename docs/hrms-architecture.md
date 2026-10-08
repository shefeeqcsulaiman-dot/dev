# TaxFlow HRMS Architecture

This document describes the HRMS module in detail. It complements `docs/architecture.md` §15–17 and §22 (HR/Rota/Payroll/WPS), which cover HRMS at whole-system level. This file is the authoritative HRMS-specific reference.

HRMS is served standalone at `https://e4cs.com/hrms` (`frontend/public/taxflow/hrms.html`), separate from the main TaxFlow shell, with its own sidebar/topbar but sharing `styles.css`, auth, and the FastAPI backend.

## 1. Core Architectural Fact: Two-Tier Persistence

HRMS has two different persistence tiers, and knowing which tier a feature is on determines what work is required to make it production-real. RBAC, GPS attendance, Branch Management, and Leave have all moved to Tier 1 since this document was first written — the sections below reflect that.

```text
Tier 1 — Real ORM tables (backend/app/models.py)
  Employee, PayrollRun, PayrollItem, WpsBatch,
  BiometricDevice, AttendanceDetail, LeaveRequest,
  Role, Permission, RolePermission,
  Branch, EmployeeBranchAccess,
  CompanyLocation, EmployeeLocation, AttendanceSession, EmployeeLocationLog
  -> proper SQLAlchemy models, real routers, real business logic

Tier 2 — JSON bridge collections (app_data_records via saveServer())
  employeeLoans, salaryAdvances, jobRequisitions, candidates,
  interviews, offerLetters, onboarding, performanceReviews,
  trainingPrograms, assets, expiryAlerts, hr_settings, tasks
  -> stored as opaque JSON payloads under a single generic table,
     no schema, no FK integrity, no dedicated validation/reporting,
     AND (see §9.1) no per-feature RBAC on the write path either —
     any authenticated principal can write to any Tier 2 collection
     regardless of their role's actual permissions
```

`app_data_records` is the same prototype compatibility bridge flagged in `docs/architecture.md` §23 as something production must remove. Every HRMS module still on Tier 2 inherits that debt: it works for demo/UI purposes but has none of the guarantees (referential integrity, tenant-safe indexing, typed validation, report-grade querying, or per-feature permission checks) that Tier 1 has.

**Rule going forward:** new HRMS features should default to Tier 1 (real tables + Pydantic schemas + router). Tier 2 is acceptable only for genuinely low-stakes, high-churn UI state — and even then, see §9.1 before assuming it's safe from a permissions standpoint.

## 2. Module Map

```text
HRMS
|-- Core HR
|   |-- Employee Master           [Tier 1: Employee]
|   |-- Attendance                [Tier 1: AttendanceDetail, BiometricDevice — one row
|   |                                       per company/employee/calendar day, every
|   |                                       individual scan (device/CSV/manual/BioTime)
|   |                                       kept in a raw_events JSON column on top of
|   |                                       the aggregated clock-in/out columns; see §7]
|   |-- GPS Attendance            [Tier 1: AttendanceSession, EmployeeLocationLog —
|   |                                       employee-initiated check-in/out, separate
|   |                                       from device punches, see §7]
|   |-- Leave Management          [Tier 1: LeaveRequest]
|   |-- Overtime                  [Tier 2: overtimeRequests]
|   |-- Corrections               [Tier 2, via attendance UI]
|   |-- Loans & Advances          [Tier 2: employeeLoans, salaryAdvances]
|   |-- Expiry Alerts             [derived — reads Employee document fields, no own table]
|   |-- Task Management           [Tier 2: tasks — flat table (the earlier Kanban
|   |                                       board was replaced), an assignee, a fixed
|   |                                       8-color picker, and a "+ Assign Task" flow
|   |                                       that can assign one saved task to several
|   |                                       employees at once; ESS-facing (GET
|   |                                       /ess/tasks) and Rota-integrated — attaching
|   |                                       a task to a Weekly Rota day can assign it to
|   |                                       that employee in the same action, see §7]
|   |-- HR Settings               [Tier 2: hr_settings — OT rules, weekend policy,
|   |                                       leave policy, dept/branch/role config]
|   `-- Biometric Devices         [Tier 1: BiometricDevice]
|
|-- Payroll & WPS
|   |-- Payroll Runs              [Tier 1: PayrollRun, PayrollItem]
|   |-- Reports & Analytics       [derived: GET /attendance/monthly-report — the
|   |                                       first report; no persistence of its own]
|   `-- WPS / SIF Export          [Tier 1: WpsBatch]
|
|-- Access Control & GPS Attendance   [Tier 1 — built, not planned]
|   |-- Employee Login (username/password, role-scoped) [Employee + /hr/login]
|   |-- RBAC (Roles, Permissions, Role Permissions)      [Role, Permission, RolePermission]
|   |-- Role-Based Dashboards (Administrator/HR Manager/Payroll Officer/...)
|   |                                                     [GET /hr/dashboard]
|   |-- Company Locations & Geofencing                   [CompanyLocation]
|   |-- GPS Check-In/Check-Out                           [AttendanceSession]
|   |-- Live Employee Location Tracking                  [EmployeeLocationLog,
|   |                                                      GET /hr/live-locations]
|   `-- Automatic Check-Out on geofence exit              [real-time via /hr/location,
|                                                           plus a Celery beat task —
|                                                           see §7]
|
|-- Branch Management            [Tier 1: Branch, EmployeeBranchAccess — separate
|   |                                       from Company Locations/geofencing above;
|   |                                       this is organizational (which office an
|   |                                       employee/record belongs to), not GPS]
|   |-- Branch CRUD + branch-scoped module toggles
|   |-- Branch's own shared login (separate from any Employee account)
|   `-- Multi-branch Employee access (EmployeeBranchAccess, on top of the
|           employee's primary Branch.id)
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
|   |-- Leave Pattern Analysis    [hr_ai.py — reads LeaveRequest]
|   |-- JD Generator              [hr_ai.py — stateless]
|   `-- HR Chatbot                [hr_ai.py — stateless]
|
`-- Org Chart (page-hrms-org)     [derived from Employee.department/designation/reporting_manager]
```

## 3. Frontend Structure

```text
frontend/public/taxflow/hrms.html
|-- page-hrms          Hub: module cards, UAE Compliance panel, section headers
|-- page-staff         Tabs: Employees, Attendance, Overtime, Leave Management,
|                             Corrections, Loans & Advances, Expiry Alerts,
|                             HR Settings (incl. Departments & Branches, Roles &
|                             Permissions, OT Rules, Weekend Policy), Biometric
|-- page-rota          Tabs: Weekly Rota, Monthly Rota, Department Rota, Swap
|                             Requests, Rota Approval. Weekly Rota's Staff Schedule
|                             cell is clickable (opens Edit Shift: shift type/times/
|                             break/notes plus a "Tasks for this Day" list, each task
|                             carrying its own time range and inheriting Task
|                             Management's color) — Monthly Rota's day cells are the
|                             same clickable button/rotaCellHtml(), so a task attached
|                             on Weekly shows there too. Clicking an employee's name
|                             (not a day cell) opens "Employee Tasks", a day-by-day
|                             list of that employee's tasks for the week with times,
|                             which sits where "Weekly Summary" used to and pushed
|                             that card down one slot. Monthly Rota's side card is
|                             "Monthly Staff Overview" — active-employee roster with
|                             real scheduled hours, not the assignment-history count
|                             it used to derive from rotaAssignmentsById (that
|                             leaked Inactive employees' old rows into the total).
|                             Week Start/Month pickers persist the last-viewed
|                             period to localStorage — previously reset to today's
|                             week/month on every page reload. The Staff Schedule's
|                             Excel/PDF export (downloadRotaExcel()/downloadRotaPdf())
|                             now includes each shift's attached tasks (title, color,
|                             time) — it used to export only the shift code and time,
|                             silently dropping task info onto the floor for anyone
|                             reading the download away from the screen. The Draft/
|                             Pending/Published status pill above Staff Schedule and
|                             Monthly Schedule is now derived from the real saved
|                             assignment/approval data on every render
|                             (updateRotaStatusBadges()) instead of being pure
|                             in-session state — it used to always show "Draft" again
|                             after a reload even when the week really was published.
|                             publishRota() is now scoped to the currently-filtered
|                             week (via selectedWeekAssignments()) rather than every
|                             rotaAssignments row ever loaded into the browser (up to
|                             500, spanning many weeks/departments) — publishing this
|                             week used to silently flip next month's still-Draft
|                             rows to Published too.
|-- page-hrms-reports   Reports & Analytics: Attendance Report (sortable
|                             Present/Absent columns, CSV export), Late Coming
|                             Report (per-employee late-arrival summary against the
|                             configured start time + grace period) — more can be
|                             added as further tabs the same way
|-- page-tasks          Task Management: flat table (the earlier Kanban board was
|                             removed), "+ Add Task" collects an assignee and a
|                             color directly, "+ Assign Task" can assign one saved
|                             task to several employees at once (checkbox grid with
|                             a search box), a task with no assignee still lists
|                             (shown as "Unassigned") rather than disappearing
|-- page-recruitment   Tabs: Job Requisitions, Candidates, Interviews,
|                             Offer Letters, Onboarding
|-- page-hrms-ext      Tabs: Performance, Training, Asset Management,
|                             ESS Portal, Manager Portal
|-- page-hrms-ai       AI Workbench: CV parsing, attrition, compliance, chatbot
`-- page-hrms-org      Org chart view (derived, read-only)

frontend/public/taxflow/src/app.js  (shared with main app, ~26,000 lines)
|-- HRMS-specific functions live in this single file, not a separate module.
|-- saveServer(collection, record) -> POST /api/v1/app-data (Tier 2 write path)
|-- Key functions: updateOtMultiplier(), previewEmpPhoto(), refreshExpiryAlerts(),
|   calcLoanEmi(), saveLoan(), saveLoanAdvance(), saveJobRequisition(),
|   saveCandidate(), filterCandidates(), refreshRecruitmentStats(),
|   refreshManagerPortalCounts(), renderMonthlyRotaBoard(), renderMonthlyStaffOverview(),
|   loadHrAttendanceReport(), loadHrLateReport(), renderTaskBoard()/saveTaskModal(),
|   confirmAssignTask() (multi-employee), _ensureTaskAssignedToEmployee() (shared
|   by Assign Task and Rota's task picker — clones a saved task into a real
|   assignment, reusing one already assigned to the same employee instead of
|   duplicating it), renderRotaEmployeeTasksPanel(), _openQuickAddModal() (generic
|   single-field modal — addLeavePolicy()/addHrJobGrade() use it; addHoliday() now
|   opens its own multi-field modal — all three used to be native prompt()/
|   confirm() chains, up to 4 in a row for Add Holiday), updateRotaStatusBadges()/
|   _deriveRotaStatusForAssignments() (Rota's Draft/Pending/Published pill, derived
|   from real data on every render rather than trusted from the last click)
|-- currentRotaStaff()/_getAttendanceEmployees() both filter Inactive employees
|   out of their respective lists — this "Inactive shouldn't appear in a live
|   roster" rule recurs across the codebase and has been found missing in a new
|   spot more than once (GET /payroll/employees, GET /ess/team, and Monthly
|   Rota's old "Monthly Summary" panel — which counted straight off
|   rotaAssignmentsById, a map that deliberately keeps a former employee's past
|   assignment rows — all needed the same fix separately). Anything new that
|   lists "who's on my team/roster/rota" should filter Employee.status/
|   staff.status explicitly rather than assuming an existing helper already
|   covers it.
|-- A second recurring bug class, found while profiling a 20+ second HRMS load
|   freeze on a 300-employee test company: a per-record render function called
|   once per row inside hydrateFromServer()'s bootstrap loop (renderRecordList())
|   must NOT also trigger its own full table/board re-render on every call —
|   renderEmployeeRecord(), renderPayrollEmployeeRecord(), and
|   renderRotaAssignmentRecord() all did this unconditionally (refreshEnhancedTable()
|   or renderRotaBoards() per row), making hydration O(n^2) in the row count.
|   The fix is the isHydratingFromServer guard already used elsewhere in this
|   file for the same problem (Item Master's renderProductRecord()) — skip the
|   expensive call while isHydratingFromServer is true, and call it exactly
|   once after the hydration loop finishes instead. Any new render*Record()
|   function wired into a renderRecordList(...) hydration loop needs this same
|   guard from the start, not as an afterthought once a customer's data grows
|   large enough to expose it.
```

There is no per-module frontend file split for HRMS yet — everything lives in `app.js` alongside the rest of TaxFlow. `docs/architecture.md` Phase 1 already calls for splitting `app.js` by module; HRMS should get its own `hrms.js` bundle when that happens.

## 4. Backend Structure

```text
backend/app/routers/
|-- attendance.py   /api/v1/attendance/*   — devices, punch import, trend, summary,
|                                             monthly-report, late-report, employee-daily,
|                                             bridge-script/report-script. Writes go
|                                             through attendance_store.py (below), not
|                                             its own insert logic.
|-- payroll.py      /api/v1/payroll/*      — employees (active-only; basic_salary
|                                             zeroed for a caller without
|                                             employees:view_salary — see §9.4),
|                                             runs, generate, wps-batch
|-- leave.py        /api/v1/leave/*        — requests, approve/reject/delete, balance
|-- hr_access.py    /api/v1/hr/*           — HR login/RBAC (roles, permissions,
|                                             department_scope), company locations,
|                                             GPS check-in/out/location pings,
|                                             live-locations, role-scoped dashboard,
|                                             employee portal-access and branch-access
|                                             admin
|-- branches.py     /api/v1/branches/*     — branch CRUD, branch's own login
|-- ess.py          /api/v1/ess/*          — employee self-service (JWT-scoped to
|                                             employee, not user); see §7's expanded
|                                             ESS Portal section for the newer
|                                             team/holiday-calendar endpoints
`-- hr_ai.py         /api/v1/ai/hr/*        — CV parse, anomaly/attrition/compliance/
                                              leave/JD/chatbot

backend/app/attendance_store.py
`-- The single write path for every attendance event source (device/CSV/manual
    punch via attendance.py, BioTime pulls via biotime_sync.py, approved
    corrections via app_data.py) — one concurrency-safe upsert
    (upsert_attendance_event()) instead of three independent insert/dedupe
    implementations. A leaf module (only app.models/app.timezone_utils + stdlib)
    so attendance.py and biotime_sync.py can both import it without a cycle. See
    §7's rewritten Attendance data flow.

backend/app/worker.py (Celery beat)
`-- hr.auto_checkout_stale_sessions — closes AttendanceSession rows whose last
    known location has been outside the geofence past the grace period, for a
    device that stopped pinging (i.e. never called /hr/location again) rather
    than genuinely being back inside — see §7's Automatic Check-Out flow for
    the real-time half of this (_maybe_auto_checkout(), triggered from
    /hr/location itself).

backend/app/models.py
|-- Employee              employees table            (Tier 1)
|-- PayrollRun            payroll_runs table         (Tier 1)
|-- PayrollItem           payroll_items table        (Tier 1)
|-- WpsBatch              wps_batches table          (Tier 1)
|-- BiometricDevice       biometric_devices          (Tier 1)
|-- AttendanceDetail      attendance_details         (Tier 1, one row per company+employee+
|                                                       work_date, unique constraint on that
|                                                       triple; superseded the old one-row-
|                                                       per-scan AttendancePunch table)
|-- LeaveRequest          leave_requests             (Tier 1, indexed by company_id+employee_id)
|-- Role / Permission / RolePermission                (Tier 1 — RBAC)
|-- Branch / EmployeeBranchAccess                     (Tier 1 — Branch Management)
`-- CompanyLocation / EmployeeLocation / AttendanceSession / EmployeeLocationLog
                                                       (Tier 1 — GPS attendance)

backend/app/routers/app_data.py
`-- Everything else (loans, salary advances, recruitment, performance, training,
    assets, hr_settings, tasks) persists as JSON rows keyed by `collection` in
    app_data_records, through one generic action-based endpoint
    (POST /api/v1/app-data?action=save|delete, GET /api/v1/app-data/records/{collection})
    with no per-collection permission check — see §9.1.
```

## 5. Current Data Model (Tier 1 — real tables)

```text
employees
  id, company_id, employee_no, full_name, department, designation,
  basic_salary, housing_allowance, transport_allowance, other_allowance,
  iban, wps_id, status, password_hash (for ESS/HR login — unset means the
  password defaults to employee_no itself, see §9.1)
  -- access control / GPS attendance (built, see §7):
  username, role_id, work_location_id, branch_id, is_active,
  last_login, last_activity, password_changed_at

payroll_runs
  id, company_id, branch_id, period, status, gross_total,
  deductions_total, net_total

payroll_items
  id, run_id, employee_id, basic, allowances, overtime, deductions,
  net_pay, wps_status

wps_batches
  id, company_id, payroll_run_id, batch_number, status, sif_content

biometric_devices
  id, company_id, name, device_type, ip_address, port, location,
  api_key_hash, status, last_sync

attendance_details
  id, company_id, employee_id (string employee_no, not a FK), employee_name,
  work_date (YYYY-MM-DD string)
  clock_in_1..5, clock_out_1..5, work_seconds_1..5  (up to 5 in/out pairs a day)
  total_seconds, ot_seconds, under_seconds, session_count
  raw_events (JSON list of every individual scan: id, punch_time (UTC ISO),
  direction, device_id, device_name, source, employee_name — the audit trail
  the aggregate columns above are computed from)
  (one row per company+employee+day, written exclusively through
  attendance_store.upsert_attendance_event(); device/CSV/manual punches and
  BioTime pulls all funnel through it — see §7. AttendanceSession below
  remains the separate GPS check-in/out path, not merged into this table)

leave_requests
  id, company_id, employee_id, leave_type, start_date, end_date, days,
  reason, status, approved_by (User), approved_by_employee_id (Employee),
  approved_at
  (both this table and attendance_details above are now included in the
  company-wide Download Backup — GET /app-data/export and /app-data/db-dump,
  app_data.py — as leave_requests_db/attendance_details_db; neither was
  queried by either export path before this session, so "full company
  data" backups silently had no leave or attendance history in them at all)

roles
  id, company_id, role_name, description, is_system_role

permissions
  id, module, permission_name

role_permissions
  role_id, permission_id

branches
  id, company_id, name, code, city, address, country, currency (reference-
  only — a branch's invoices/VAT still use the parent Company's own
  currency/vat_rate, unchanged), status ("Active"/"Inactive", capitalized —
  a deliberate mismatch with CompanyLocation.status's lowercase convention,
  kept for compatibility with the pre-existing Settings UI it replaced the
  backing store for), modules_enabled (JSON array, NULL/empty = unrestricted,
  same convention as Company.modules_enabled)
  -- the branch's own shared login (separate from any Employee account):
  username (globally unique), password_hash (no employee_no-style default —
  must be set explicitly), password_changed_at, last_login, last_activity

employee_branch_access
  id, employee_id, branch_id, is_primary
  (additional branches an employee may switch into, on top of their
  primary Employee.branch_id; zero rows = locked to that one branch, or
  unrestricted if branch_id is NULL — purely additive)

company_locations
  id, company_id, location_name, branch_id, address, latitude, longitude,
  allowed_radius_meters (default 200), status

employee_locations
  id, employee_id, location_id, is_primary

attendance_sessions
  id, company_id, employee_id, location_id, branch_id, check_in, check_out,
  check_in_lat/lng, check_out_lat/lng, auto_checkout, status
  (paired GPS check-in/out session; attendance_details remains the separate
  device/CSV/BioTime punch record — the two are not merged)

employee_location_logs
  id, company_id, employee_id, session_id, latitude, longitude, accuracy,
  inside_geofence, device, battery
```

Gap that's still real: the rich employee profile the frontend's Add/Edit Employee form already collects (Emirates ID, nationality, DOB, gender, marital status, employment type, join date, OT rate override, leave policy, reporting manager, shift, work permit, visa expiry, cost center, emergency contact, insurance, passport/EID/labor card/driving license numbers and expiries) still has **no matching columns on `Employee`** — none of this moved when username/role_id/branch_id/GPS fields were added. That detail is either dropped or shoved into the Tier 2 bridge under the employee's `app_data_records` row rather than the `employees` table. §6 below still lists this as the top remaining gap.

## 6. Recommended Data Model (close the remaining gap)

RBAC, GPS/geofencing, Branch Management, and Leave have all shipped since this document first called for them (§5 now lists their real schema). What's left:

```text
employees (extend existing table, do not create a parallel one)
  + emirates_id, nationality, date_of_birth, gender, marital_status
  + employment_type, join_date, ot_rate, leave_policy_id
  + reporting_manager_id, shift_id, work_permit_no
  + visa_expiry_date, cost_center, emergency_contact_name/phone
  + insurance_type, insurance_policy_no, insurance_expiry_date
  + passport_no, passport_expiry_date, labor_card_no
  + driving_license_no, driving_license_expiry_date, photo_url

leave_types
  id, company_id, name, paid, annual_entitlement_days, status
  (leave_requests itself is Tier 1 already — see §5 — but the leave TYPE
  catalog/entitlement rules a request is validated against are still the
  hr_settings Tier 2 blob, not a real table; this is why an admin's edit
  to a named policy's day-count in HR Settings doesn't reliably reach the
  backend's own entitlement enforcement, per docs/architecture.md's HRMS
  audit findings)

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

tasks (new — currently Tier 2, listed here as a migration candidate
  once write volume/reporting needs justify it, same bar as the rest of
  this table's contents)
  id, company_id, title, description, assigned_to (employee_id),
  priority, due_date, status, created_by
```

Every table above follows the tenant-isolation rule from `docs/architecture.md` §21: `company_id` on every row, `created_by`/`updated_by`/timestamps via the existing `TimestampMixin`.

## 7. Data Flow

### Attendance → Payroll (Tier 1)

```text
Biometric Device / Manual Punch / CSV Import / BioTime pull / approved
correction (app_data.py)
        |
        v
attendance_store.upsert_attendance_event(company_id, employee_id, punch_time,
    direction, ...) -- the single write path every source above calls into;
    re-pairs that one day's raw_events and rewrites the aggregate
    clock_in/out_N + work_seconds_N + total/ot/under_seconds columns
        |
        v
attendance_details (one row per company+employee+work_date, unique
    constraint on that triple -- see §5)
        |
        v
GET /attendance/summary, /trend, /today, /monthly-report, /late-report,
    /employee-daily, /import-rows
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

`attendance_store.py` is a deliberate leaf module (only `app.models` +
`app.timezone_utils` + stdlib) so both `attendance.py` and
`biometric_sync`/`biotime_sync.py` can import `upsert_attendance_event()`
without a circular import, and so every write — regardless of source —
goes through the same re-pairing/aggregation logic instead of three
divergent implementations. This replaced the older `AttendancePunch`
one-row-per-scan table, which required every reader to re-derive
sessions/hours itself; `attendance_details` now carries the aggregate
columns pre-computed, with `raw_events` kept only as the audit trail.

`/attendance/monthly-report` (new) computes present/absent/leave days per
employee for a selected month — present/absent/leave are all counted in
"working days" per the company's configured Weekend Policy (`hr_settings`'s
`weekend-policy-config` record: Sat/Sun default, or Fri/Sat), so a company
that actually runs Sun-Thu doesn't have its real day off scored as
"Absent." Total/OT hours are a first-punch-to-last-punch-per-day
approximation — adequate for a summary report, not claiming the same
in/out session pairing rigor as `zk_bridge.py`/`daily_attendance_report.py`.

`/attendance/employee-daily` (the Attendance Report's per-employee
drill-down, and the popup behind clicking an employee name there) does
claim that rigor: `_pair_day_punches()` (attendance.py) reimplements
`zk_bridge.py`/`daily_attendance_report.py`'s direction-aware in/out state
machine in pure Python (no pandas — not a backend dependency) rather than
importing those scripts, and pairs into up to 3 sessions/day with real
per-session and total hours, not a single first/last-punch span. It also
collapses a repeat "in" event within 5 minutes of the currently-open one
into the same session (`_DWELL_DUPLICATE_WINDOW`) — a dwell/proximity
sensor or simple entry-only turnstile can re-read one physical entry
several times in quick succession, which without this would turn one real
entry into a burst of spurious no-checkout sessions, filling (and
exceeding) the 3-session cap before a genuinely later, distinct entry that
day ever got a slot. Each day carries an `is_today` flag so the UI can
tell "hasn't checked out yet, might still" (today) apart from "no checkout
was ever recorded" (a past day, most likely an entry-only device) — both
looked like an identical bare blank before this distinction existed.

### Leave (Tier 1)

```text
UI form -> POST /api/v1/leave/requests -> LeaveRequest row (pending)
        |
        v
POST /leave/requests/{id}/approve or /reject (role/permission-checked)
        |
        v
GET /leave/balance reads LeaveRequest directly (real entitlement math,
    year-boundary-aware, per-type caps) -- not a DOM scrape
        |
        v
Approved leave feeds payroll deduction calculations and the Attendance
Calendar's "Leave" day coloring
```

The leave TYPE catalog and entitlement day-counts an approval is validated
against still live in the `hr_settings` Tier 2 blob (see §6) — the request
itself is a real row, but the rules it's checked against are not yet.

### Loans / Recruitment / Performance / Training / Assets (Tier 2, target Tier 1)

```text
Current:
UI form -> saveServer(collection, record) -> POST /api/v1/app-data?action=save
        -> app_data_records row (payload = JSON, no schema, no per-feature
           permission check -- see §9.1)
        -> read back via GET /api/v1/app-data/records/{collection}

Target:
UI form -> POST /api/v1/hr/loans (or /recruitment/*, etc.)
        -> Pydantic schema validation
        -> dedicated table (see §6)
        -> require_principal_permission("loans:edit") or equivalent, per
           feature -- closing the §9.1 gap as each collection migrates
        -> triggers domain event (LoanApproved, etc.) per
           docs/architecture.md §20.2 event system
        -> payroll/reports read from the real table, not JSON payload
```

### ESS Portal

```text
Employee login (employee_no + password_hash, or employee_no as the
password itself if password_hash was never set -- see §9.1)
        |
        v
POST /api/v1/ess/login -> JWT scoped to employee_id (not a user account)
        |
        v
GET /ess/me, /ess/attendance, /ess/payslips, /ess/leave, /ess/leave-balance,
    /ess/tasks, /ess/rota, /ess/announcements, /ess/team, /ess/team/today,
    /ess/team/leave, /ess/team/rota, /ess/holidays
POST /ess/leave (submit a new request), /ess/change-password
        |
        v
Employee-only views, each independently filtered by emp.id/emp.company_id
from the token -- never a client-supplied id:
  - attendance_details, payroll_items (Tier 1, own SQL columns)
  - leave_requests (Tier 1, own employee_id column)
  - tasks, rotaAssignments (Tier 2 AppDataRecord -- see below)
```

**Team / Holiday Calendar endpoints (added this session)** — `GET
/ess/team` returns the caller's department peers (name, designation,
`Employee.status == "active"` only — see §3's note on this recurring
bug class), scoped the same way `/hr/roles`'s department-scoped custom
roles are (`_role_department_scope()`-style: the employee's own
`department` column, no cross-department leak). `GET /ess/team/today`
layers today's attendance status onto that same roster (used by both
the ESS Team tab and the Dashboard's "My Team Today" preview card —
`loadTeamToday()` in `ess.html` populates both from one fetch). `GET
/ess/team/leave?month=YYYY-MM` returns approved leave for that same
department roster within a given month (any pending/rejected request is
excluded), which `ess.html`'s `renderHolidayCalendarGrid()` overlays
onto a real Mon-Sun month grid alongside `GET /ess/holidays`. None of
these three endpoints require a Principal/RBAC permission — an ESS
token has the same "reach" as `/ess/me` itself, just narrowed to the
caller's own department server-side, matching the existing `/ess/leave`
precedent below.

**Department Rota + per-shift tasks (added this session)** — the Rota
tab previously only ever showed an employee their own shifts, with no
way to see who else in the department is working, and it silently
dropped the tasks a manager attaches to a shift via HRMS's Weekly/
Monthly Rota task picker even though `GET /ess/rota` already returned
them. `GET /ess/team/rota?week=YYYY-MM-DD` returns the caller's own
department's roster and that Mon-Sun week's `rotaAssignments` rows
separately (not nested), so `ess.html` can render a grid the same way
HRMS's own Department Rota does, keyed by `(employee_no, date)` —
matching `rotaAssignments`' own id convention (see `ess_rota()`'s
docstring below on why `employee_no`, not `emp.id`). Same "own
department, everyone, no role-scope required" reach as `/ess/team/today`
and `/ess/team/leave`. The "My Rota" list itself now also renders each
shift's attached tasks (title, color, time) instead of only the shift.

**Login routing (fixed this session, `dbdf432`)** — `login.html`'s
`signIn()` used to always redirect a successful `/hr/login` to
`hrms.html`, which for a bare "Employee" role (no module `:view`
permissions at all) rendered a broken, near-empty page. It now fetches
`/hr/me` right after login, computes the caller's allowed modules from
`:view`-suffixed permission keys (excluding `dashboard`), and if that
set is empty, stores the token as `ess_token` instead and redirects to
`/ess` — i.e. a role with no HRMS module access lands on the ESS portal
it can actually use. Wrapped in try/catch that fails open to the old
`hrms.html` redirect if `/hr/me` is unreachable, so a transient network
error can't strand a legitimate HR user on a login screen.

Leave requests ARE submittable from ESS (2026-09-02) — `POST /ess/leave`
mirrors `POST /leave/requests`'s validation (allowed types, no
overlapping pending/approved request) but always targets the token's own
employee, never a body-supplied `employee_id`, and does not require a
Principal/RBAC permission the way `/leave/requests` does (an ESS token is
not a Principal at all — see `ess_bearer()`). Deliberately a separate
endpoint rather than pointing ESS at `/leave/requests` directly, since
that endpoint's `require_principal_permission("leave:edit")` gate can
never be satisfied by an ESS token.

`/ess/tasks` and `/ess/rota` are the two Tier 2 (AppDataRecord)
collections ESS reads — there's no SQL column to filter "this employee's
rows" by, so the whole collection is pulled per company and filtered in
Python, the same workaround the Holiday Calendar lookup in `attendance.py`
already uses for the same class of problem. `/ess/rota` additionally
narrows to a 7-days-back/30-days-forward window — an employee's full rota
history could be large (see §30 in docs/architecture.md on the
`rotaAssignments` cap) and nobody needs last year's shifts on their phone.

**The two collections use different, inconsistent employee-identifier
conventions — a real gotcha, not a design choice.** `tasks.assigned_to`
is the real `Employee.id` UUID (`populateTaskAssigneeSelect()` in app.js
populates the assignee `<select>` from `/payroll/employees`, whose `id`
field is the UUID). `rotaAssignments.employee_id` is the employee's
`employee_no` string instead (`currentRotaStaff()`/
`employeeFromDirectoryRow()` in app.js build a rota row's `staff.id` by
reading the Employee Directory table's visible "ID" *column*, which
displays `employee_no`, not the UUID — confirmed against live production
data: 100% of `rotaAssignments` rows use `employee_no`-shaped values like
`"22"`, zero use UUIDs). `/ess/rota` matches on `emp.employee_no`
accordingly — an earlier version (shipped 2026-09-02, fixed same day)
matched on `emp.id` by assuming the Tasks convention applied here too,
which silently returned an empty rota for every real employee with real
rota data, caught only when live-tested against an employee who actually
had assignments rather than the synthetic fixture data used at ship time.
Any future Tier 2 collection touching an employee reference needs its own
convention verified against real saved data before assuming either
pattern — don't extrapolate from a sibling collection.

### Employee Login & RBAC (Tier 1 — built)

```text
HR Admin (permission: hr_settings:edit -- see §9.1 for why that's too
broad a gate for this)
        |
        v
Create Employee -> PUT /hr/admin/employees/{id}/portal-access
                   (username, password, role_id, is_active)
        |
        v
Employee Login -> POST /hr/login (username + password)
        |
        v
JWT issued with employee_id + role_id claims
        |
        v
Role lookup (role_permissions) via require_principal_permission()
        |
        v
Role-Based Dashboard: GET /hr/dashboard branches on role_name
  (Administrator/HR Manager, Payroll Officer, ... else generic Employee view)
        |
        v
Every Tier 1 HRMS route is checked against the caller's permissions
server-side (require_principal_permission(...) dependency) -- Tier 2
routes are not (§9.1)
```

This is the same identity `ess.py` already used for the self-service
portal, extended with a `role_id` claim rather than a second auth system —
one employee identity, one login system (`/hr/login` for the HR-admin-
facing surface, `/ess/login` for self-service), both issuing an
employee-scoped JWT.

### GPS Attendance Flow (Tier 1 — built)

```text
Employee Login
        |
        v
Browser/app requests GPS permission
        |
        v
POST /hr/check-in with current lat/long
        |
        v
Compare against employee_locations -> company_locations.allowed_radius_meters
        |
        v
Inside radius? --YES--> attendance_sessions row opened (status=open)
        |
        NO
        v
Reject: outside company location
```

### Live Employee Tracking (Tier 1 — built)

```text
Employee checked in (attendance_sessions.status = open)
        |
        v
POST /hr/location pings (periodic, while the app has GPS permission) ->
    employee_location_logs row + _maybe_auto_checkout() check (below)
        |
        v
GET /hr/live-locations -- HR dashboard reads the latest ping per employee
```

### Automatic Check-Out (Tier 1 — built, two mechanisms)

```text
Real-time (while the employee's device is still pinging):
POST /hr/location -> _maybe_auto_checkout(session) checks distance from
    the assigned company_locations row against allowed_radius_meters;
    outside past the grace period -> closes the session immediately,
    auto_checkout = true

Background (for a device that stops pinging entirely -- no further
/hr/location calls to trigger the check above):
Celery beat task hr.auto_checkout_stale_sessions (worker.py) periodically
    closes any attendance_sessions row whose last known ping is stale,
    same auto_checkout = true flag
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
GET    /api/v1/attendance/today          (optional ?date=YYYY-MM-DD, default today)
GET    /api/v1/attendance/trend          (optional ?period=YYYY-MM, else ?days=N)
GET    /api/v1/attendance/monthly-report
GET    /api/v1/attendance/employee-daily (?employee_id=&period=YYYY-MM -- proper
                                           session pairing, see §7 above)
GET    /api/v1/attendance/late-report    (per-employee late-arrival counts/minutes
                                           for a period, see §3's HR Reports tab)
POST   /api/v1/attendance/import-rows
GET    /api/v1/attendance/punches
DELETE /api/v1/attendance/punches/{id}
GET    /api/v1/attendance/summary
GET/POST /api/v1/attendance/devices
DELETE /api/v1/attendance/devices/{id}
POST   /api/v1/attendance/devices/{id}/test
POST   /api/v1/attendance/devices/{id}/biotime/sync
GET    /api/v1/attendance/bridge-script
GET    /api/v1/attendance/report-script

GET    /api/v1/payroll/employees      (active-only; basic_salary zeroed for a caller
                                        without employees:view_salary, see §9.4)
GET    /api/v1/payroll/runs
POST   /api/v1/payroll/generate
POST   /api/v1/payroll/runs/{run_id}/wps-batch

GET/POST /api/v1/leave/requests
POST     /api/v1/leave/requests/{id}/approve
POST     /api/v1/leave/requests/{id}/reject
DELETE   /api/v1/leave/requests/{id}
GET      /api/v1/leave/balance

POST     /api/v1/hr/login
POST     /api/v1/hr/logout
GET      /api/v1/hr/me
GET      /api/v1/hr/dashboard
GET/POST /api/v1/hr/roles
GET      /api/v1/hr/permissions
GET      /api/v1/hr/admin/permissions
GET/POST /api/v1/hr/admin/roles
PUT/DELETE /api/v1/hr/admin/roles/{role_id}
GET      /api/v1/hr/admin/employees
PUT/DELETE /api/v1/hr/admin/employees/{id}/portal-access
GET/PUT  /api/v1/hr/admin/employees/{id}/branch-access
GET/POST /api/v1/hr/company-locations
PUT/DELETE /api/v1/hr/company-locations/{id}
GET/POST/DELETE /api/v1/hr/employee-locations
POST     /api/v1/hr/check-in
POST     /api/v1/hr/check-out
POST     /api/v1/hr/location
GET      /api/v1/hr/live-locations

GET/POST /api/v1/branches
PUT/DELETE /api/v1/branches/{id}
POST     /api/v1/branches/login

POST   /api/v1/ess/login
GET    /api/v1/ess/me
POST   /api/v1/ess/change-password
GET    /api/v1/ess/attendance
GET    /api/v1/ess/payslips
GET/POST /api/v1/ess/leave    (own history / submit a new request)
GET    /api/v1/ess/leave-balance
GET    /api/v1/ess/announcements
GET    /api/v1/ess/tasks      (own assigned tasks only)
GET    /api/v1/ess/rota       (own upcoming shifts only, -7d/+30d window)
GET    /api/v1/ess/team       (own-department peers, active only)
GET    /api/v1/ess/team/today (own-department peers + today's attendance status)
GET    /api/v1/ess/team/leave (?month=YYYY-MM -- own-department approved leave)
GET    /api/v1/ess/team/rota  (?week=YYYY-MM-DD -- own-department roster + that
                                week's rotaAssignments, for the Department Rota view)
GET    /api/v1/ess/holidays

POST   /api/v1/ai/hr/cv-parse
POST   /api/v1/ai/hr/payroll-anomaly
POST   /api/v1/ai/hr/attrition-risk
POST   /api/v1/ai/hr/compliance-check
POST   /api/v1/ai/hr/leave-analysis
POST   /api/v1/ai/hr/jd-generate
POST   /api/v1/ai/hr/chatbot

(bridge, Tier 2 — used by everything not listed above: loans, salary
advances, recruitment, performance, training, assets, hr_settings, tasks —
no per-collection permission check, see §9.1)
GET/POST /api/v1/app-data
GET      /api/v1/app-data/records/{collection}
```

### Recommended (remaining Tier 1 migration targets)

```text
GET/POST /api/v1/hr/leave-types          (entitlement catalog — leave_requests
                                           itself is already Tier 1)
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
GET/POST /api/v1/hr/tasks
PUT/DELETE /api/v1/hr/tasks/{id}
```

## 9. Security & Tenant Isolation — Concrete Findings

These are specific, reproduced issues in the code as it stands today, not general aspirational controls. `docs/architecture.md` §19/§21 cover the platform-wide principles these fall under.

### 9.1 Tier 2 write path has no per-feature RBAC check (CONFIRMED)

`POST /api/v1/app-data?action=save` and `?action=delete` (`app_data.py::app_data_action()`) are gated only by:

- `Depends(get_current_principal)` — any authenticated identity (User, Employee, or Branch token), no permission check at all, and
- `assert_collection_module_enabled()` — checks the **company** has the relevant module toggled on, not that the **caller's role** has any specific permission for it.

Every Tier 2 collection — `employeeLoans`, `salaryAdvances`, `jobRequisitions`, `candidates`, `interviews`, `offerLetters`, `onboarding`, `performanceReviews`, `trainingPrograms`, `assets`, `hr_settings` (OT rules, weekend policy, leave policy, dept/branch/role config), and `tasks` — inherits this gap. An Employee whose role grants zero HR permissions can still call this endpoint directly (bypassing the frontend's role-based UI hiding entirely, since that's the only thing currently stopping them) to create/edit/delete records in any of these collections, including `hr_settings` itself.

This is the direct reason the "Rule going forward" in §1 pushes new features to Tier 1: a Tier 1 router gets `require_principal_permission(...)` per-route by construction; a Tier 2 collection gets none of that by default, and nothing currently adds it back collection-by-collection.

### 9.2 `hr_settings:edit` is a privilege-escalation path (CONFIRMED)

Three unrelated-sounding admin actions are all gated by the single permission `hr_settings:edit`:

1. `POST /hr/admin/roles` — create a new Role and grant it **any permission key the company has enabled** (the only check is company-level module enablement, not "does the caller already hold this permission").
2. `PUT /hr/admin/roles/{id}` — same, for editing an existing role's permission set.
3. `PUT /hr/admin/employees/{id}/portal-access` — assign a `role_id` to any employee, including the caller themself.

An employee granted only `hr_settings:edit` — plausible for someone administering Weekend Policy/OT rates/leave-type day-counts, which is what that permission name suggests it's for — can chain these three calls to create a role holding every permission the company has ever enabled, then assign that role to their own account. This is full privilege escalation from a single, narrow-sounding permission grant, not a hypothetical: all three endpoints are real, reachable, and use the identical permission string today.

Fix direction (not yet implemented): split role/permission administration onto its own permission (e.g. `roles:manage`), separate from `hr_settings:edit`'s actual intended scope (OT/weekend/leave-policy config), and require it specifically for all three call sites above.

### 9.3 Weak default credential: unset password defaults to `employee_no` (CONFIRMED)

Both `ess.py` (`/ess/login`) and `hr_access.py` (`/hr/login`) fall back to treating the employee's own `employee_no` as their password whenever `Employee.password_hash` is `None` — i.e. before an admin has ever set a real password via `PUT /hr/admin/employees/{id}/portal-access`. Employee numbers are small, often-sequential values (seen in this deployment's own live data: `"10"`, `"22"`, `"52"`, ...) that also appear on payslips, ID badges, and the Employee Directory table itself — not a secret, and easily enumerable by anyone who can see one real employee's number and guess nearby ones. Any employee whose portal access was created but whose password was never explicitly changed is reachable with a guessable credential for as long as that remains true.

This is a deliberate onboarding convenience (an admin can create portal access without immediately setting a password, and the employee can log in on day one), but it currently has no companion safeguard — no forced password change on first login, no expiry on the fallback, no distinguishable "must change password" state surfaced anywhere in the UI.

### 9.4 `employees:view_salary` — field-level salary redaction (SHIPPED this session)

A new permission key (`employees:view_salary`, alongside the existing `employees:view/edit/delete`) now gates salary/allowance visibility independently of general employee-record access — a role can see the Employee Directory without seeing pay figures. `Administrator`'s wildcard grant picks it up automatically (`_ensure_default_roles()`'s idempotent self-heal); every other default role (`HR Manager`, `Manager`, `Payroll Officer`, `Employee`) does not have it by default and must be granted it explicitly. Redaction is applied at three read paths (`GET /payroll/employees` zeroes `basic_salary` in-memory; `app_data.py`'s `bootstrap()` and `list_collection_records()` null out `salary`/`housing_allowance`/`transport_allowance`/`other_allowance` on the `employees` collection) and one write path (`sync_domain_model()`'s employees/staff branch drops those same fields from an incoming record when the caller lacks the permission, so a denied role can't write salary even via a hidden form field). Out of scope: Payroll module screens keep their own pre-existing `payroll:view_payroll` gate; bank/IBAN/WPS fields are untouched.

Implementing this closed two previously-unrelated, pre-existing gaps found along the way: `list_collection_records()` had **no** `employees:view` check at all for the `employees` collection (any Employee principal could call it directly regardless of role, bypassing the Directory UI's own filtering — Branch principals were already structurally blocked since "hrms" isn't in `BRANCH_ELIGIBLE_MODULES`); and the same handler's write-side employee/staff sync had no permission gate on the salary fields specifically, so they could be silently overwritten (including zeroed) by any caller who could reach the sync endpoint at all, independent of whether they were allowed to view salary. Both fixes are covered by `backend/tests/test_employees_salary_permission.py` (8 tests) alongside the redaction tests themselves. The broader finding that this same sync path has **no** `employees:edit` check at all is separate and still open — flagged here, not fixed by this change.

### General principles (still aspirational until the Tier 2 migration closes §9.1)

- `ess.py` issues a JWT scoped to `employee_id`, not the normal user/company JWT — routers reading employee-submitted data must not trust `company_id` from the token payload without re-validating the employee belongs to that company, since HRMS is the one module with a second, lower-trust auth path.
- Salary, IBAN, Emirates ID, passport, and insurance fields are PII/financial data — Tier 2 storage as unstructured JSON in `app_data_records` gives none of the column-level access control a real table could have, and (per §9.1) not even the same write-time permission check a Tier 1 route would enforce.
- Loan/advance approval and leave approval are exactly the kind of "high-risk action" `docs/architecture.md` §19 calls out. Leave approval is Tier 1 now and can get full `audit_logs` entries (old value/new value/approver/reason); loan/advance approval is still Tier 2 and does not reliably produce them.
- GPS coordinates and location logs are sensitive personal data: transmitted over HTTPS only (enforced platform-wide), `employee_location_logs` reads should stay scoped to HR/admin roles and the employee themself, and the auto-checkout mechanisms (§7) mean the platform does stop actively tracking a session once it closes.
- Geofence radius and grace period are configurable per company (`CompanyLocation.allowed_radius_meters`), not hardcoded — confirm the grace period used by `_maybe_auto_checkout()`/the Celery task is sourced the same way before assuming it's tunable too.
- Every login, logout, check-in, check-out, automatic check-out, and location-permission denial should write to `audit_logs`, since these are the events most likely to be disputed by an employee ("I was never late," "I did check out").

## 10. Build Priority

Items 1–6 from the original roadmap here are done — RBAC, GPS check-in/out, live tracking, automatic check-out, and role-based dashboards all shipped. What's left:

```text
1. Close the §9.1 Tier 2 RBAC gap and the §9.2 hr_settings:edit
   escalation path — both are live, reproducible issues in production
   code today, not roadmap items; fix before further Tier 2 features
   accumulate more of the same exposure (Task Management, added most
   recently, already inherits §9.1 as-is).
2. Migrate Leave's remaining Tier 2 half (leave_types / entitlement
   catalog) to Tier 1 — leave_requests itself already is (§5/§7); this
   closes the last gap in that migration.
3. Migrate Overtime + Loans/Advances to Tier 1 (highest remaining write
   volume, feeds payroll and ESS).
4. Migrate Recruitment (requisitions -> candidates -> interviews ->
   offers -> onboarding) as one connected pipeline, not five separate
   tables built independently.
5. Migrate Performance + Training + Assets + Tasks (lower write volume,
   can lag).
6. Extend the Employee ORM model with the remaining rich profile fields
   the UI already collects but has nowhere real to store (§6's
   employees block: Emirates ID, nationality, DOB, visa/passport/labor
   card details, etc.).
7. Turn Expiry Alerts into a backend query once Employee fields are real
   columns, and wire it into the notification system
   (docs/architecture.md §20.2 event system).
8. Add a forced-password-change (or at least a visible "never set a
   real password" flag) for the §9.3 employee_no-default-password state,
   rather than leaving it silently indefinite.
9. Split HRMS frontend logic out of the shared app.js into its own
   bundle, per the Phase 1 module-split guidance in docs/architecture.md.
```

This priority order follows the same principle as the main architecture doc: fix live security exposure and persist data correctly before layering more UI on top of it.

## 11. Overtime cool-off, eligibility list, ESS (2026-09-25)

**Cool-off.** `hr_settings` / `ot-rules-config` gained `otCooloffMinutes` (HRMS > HR Settings > Overtime, default 0). Counted OT for a day = `max(0, worked − standard − cooloff)` (`_ot_after_cooloff`, `attendance.py`). It applies to the daily report and monthly staff overview, which recompute OT from `total_seconds`; the `ot_seconds` column written at punch time by `attendance_store` still uses the old rule and is not read by these reports.

**Eligibility list.** `overtime_eligibility_rows()` (`attendance.py`) lists days with worked > standard, per employee: first clock-in / last clock-out (company local time), worked, extra, eligible OT, and a status — `Eligible - not yet requested`, `Requested - <status>`, or `Not eligible (within cool-off)`. Requests are matched to `overtimeRequests` records by `(employee_id | employee_no | name, date[:10])` — HRMS stores `Employee.id`, ESS stores `employee_no`.
- HRMS: `GET /attendance/overtime-eligibility?date_from&date_to` (`attendance:view`, department-scoped, ≤92 days, default last 14). UI: card above Overtime Requests with a "Request OT" button that creates a Pending record.
- ESS: `GET /ess/overtime-eligibility` (caller only, last 31 days). UI: card at the top of the Requests tab; "Request" opens the overtime form prefilled with date, eligible hours and type.

**ESS leave requests** now reject a start date >30 days in the past and days beyond the remaining balance (unpaid leave exempt). The "Contact HR" mailto (no recipient) was removed.

**Attendance tab chart.** "30-Day Attendance Trend" is `_renderAttendanceTrendChart()` (`app.js`): SVG smooth area chart, gridlines, weekend bands, average/peak/today markers, hover tooltip (`attTrendHover`). The Dashboard card still uses `_renderAttendanceLineChart`.
