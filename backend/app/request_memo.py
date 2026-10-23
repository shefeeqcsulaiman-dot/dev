"""Read-once memory for one request's database session.

Company settings (time zone, leave policy, weekend, holidays, late rules...) used to be read
from the database every time a function needed them, often many times in one request: the
HRMS leave balance read the leave policy once per employee, and ESS's first load read the
company row 27 times (2026-10-23). memo() keeps the first answer for the rest of the request.

It never outlives the request (it lives on the request's own Session) and it is emptied as
soon as the session writes anything, so a value read before a save is never used after it.
Answers are deep-copied on the way out, so a caller that changes what it got back can't
change what the next caller gets.
"""
import copy
from typing import Any, Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

_KEY = "_request_memo"


def memo(db: Session, key: tuple, compute: Callable[[], Any]) -> Any:
    store = db.info.setdefault(_KEY, {})
    if key not in store:
        store[key] = compute()
    return copy.deepcopy(store[key])


def forget(db: Session) -> None:
    db.info.pop(_KEY, None)


@event.listens_for(Session, "before_flush")
def _forget_on_write(session: Session, flush_context, instances) -> None:
    if session.new or session.dirty or session.deleted:
        forget(session)


@event.listens_for(Session, "after_rollback")
def _forget_on_rollback(session: Session) -> None:
    forget(session)
