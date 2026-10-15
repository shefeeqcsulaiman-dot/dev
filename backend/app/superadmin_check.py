"""Check (and repair) the Super Admin sign-in from the server console:

    python -m app.superadmin_check

Prints what sign-in would see for superadmin@etaxflow.com -- the account, its role, whether
it is active, whether its password matches SUPERADMIN_PASSWORD -- then applies
SUPERADMIN_PASSWORD (the same step every start runs, app.main.seed_initial_data()) and
checks again. Never prints the password.
"""
from __future__ import annotations

from app.config import get_settings
from app.database import SessionLocal
from app.models import Company, User
from app.security import authenticate_user, verify_password

EMAIL = "superadmin@etaxflow.com"


def _report(label: str) -> bool:
    password = get_settings().superadmin_password
    with SessionLocal() as db:
        users = db.query(User).filter(User.email == EMAIL).all()
        print(f"[{label}] accounts with this email: {len(users)}")
        for user in users:
            company = db.get(Company, user.company_id)
            print(f"  role={user.role} active={getattr(user, 'is_active', True)} "
                  f"company={company.name if company else '(missing)'} "
                  f"password matches SUPERADMIN_PASSWORD: {verify_password(password, user.password_hash)}")
        ok = authenticate_user(db, EMAIL, password) is not None
        print(f"[{label}] sign-in with SUPERADMIN_PASSWORD would {'SUCCEED' if ok else 'FAIL'}")
        return ok


def main() -> None:
    password = get_settings().superadmin_password
    print(f"SUPERADMIN_PASSWORD is set: {bool(password)}, length {len(password)}, "
          f"spaces at either end: {password != password.strip()}")
    if _report("before"):
        return
    from app.main import seed_initial_data

    seed_initial_data()
    print("applied SUPERADMIN_PASSWORD to the account")
    _report("after")


if __name__ == "__main__":
    main()
