"""Shared module-key vocabulary — the single source of truth for both
Company.modules_enabled (superadmin-controlled) and Branch.modules_enabled
(Branch Login Phase 1). Split out from routers/superadmin.py (which
originally owned ALL_MODULES) so core auth code (auth_principal.py,
dependencies.py) and branches.py don't need a reverse import into a router
module — routers/superadmin.py re-exports ALL_MODULES from here for every
existing call site that imports it from there.
"""

# "settings" and "branches" are deliberately NOT wired into require_module()
# anywhere (see dependencies.py) — they only hide their sidebar link.
# Settings covers core account admin (Users & Roles, password/profile,
# backup) and Branches covers core company structure (locations, branch
# login); a hard server-side block on either would risk locking a company
# out of its own account/branch management if something upstream went
# wrong. Every other key here has a matching require_module("<key>")
# somewhere in the routers.
ALL_MODULES = [
    "sales", "quotations", "pos", "purchase", "inventory", "expense",
    "bank", "accounting", "corporate", "reports", "hrms", "ess",
    "notifications", "expert", "exception", "ai", "settings", "branches",
]

# Branch Login Phase 1: the subset of ALL_MODULES a Branch entity's own
# module toggle (Branch.modules_enabled) can restrict. Deliberately excludes
# "hrms"/"ess" (HR/attendance/payroll access stays governed purely by an
# Employee's own Role, never by which branch they're assigned to) and
# "settings" (account admin, same reasoning as ALL_MODULES' own comment
# above — never gated by module toggles at all).
BRANCH_ELIGIBLE_MODULES = [
    "sales", "quotations", "pos", "purchase", "inventory", "expense",
    "bank", "accounting", "corporate", "reports", "notifications",
    "expert", "exception", "ai",
]
