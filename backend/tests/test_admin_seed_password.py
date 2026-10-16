"""The built-in admin (admin@taxflowapp.com) gets its password from ADMIN_PASSWORD only when
the account is created or ADMIN_PASSWORD changes -- not on every start, which undid a
password changed in the app at every deploy."""
from app import main
from app.models import User
from app.security import hash_password, verify_password


def _admin(db):
    db.expire_all()
    return db.query(User).filter(User.email == "admin@taxflowapp.com").first()


def test_admin_password_is_not_reset_on_restart(db, monkeypatch):
    monkeypatch.setattr(main.settings, "admin_password", "seed-pass-one")
    main.seed_initial_data()
    assert verify_password("seed-pass-one", _admin(db).password_hash)

    # changed in the app, then the server restarts (a deploy): the change stays
    admin = _admin(db)
    admin.password_hash = hash_password("changed-in-app")
    db.commit()
    main.seed_initial_data()
    assert verify_password("changed-in-app", _admin(db).password_hash)

    # a new ADMIN_PASSWORD is applied once
    monkeypatch.setattr(main.settings, "admin_password", "seed-pass-two")
    main.seed_initial_data()
    assert verify_password("seed-pass-two", _admin(db).password_hash)
    admin = _admin(db)
    admin.password_hash = hash_password("changed-again")
    db.commit()
    main.seed_initial_data()
    assert verify_password("changed-again", _admin(db).password_hash)
