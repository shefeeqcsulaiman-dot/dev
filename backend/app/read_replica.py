"""Report reads on a read replica (DATABASE_READ_URL), when one is configured.

Only the heavy report builders use it (dashboard, summary, trial balance, branch
performance in routers/reports.py); everything else, including lists a user expects to
show what they just saved, stays on the primary. Two safety rules:

- A company that wrote anything in the last READ_REPLICA_WRITE_WINDOW_SECONDS (marked
  by cache.invalidate_company(), which runs after every write) reads its reports from
  the primary, so a report rebuilt right after a save can't cache figures the replica
  hasn't received yet.
- If the replica is unreachable or overloaded, the report is built on the primary.
"""
from __future__ import annotations

import logging
from typing import Callable, TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.orm import Session

from app import cache

log = logging.getLogger("taxflow")

T = TypeVar("T")


def run_report(primary: Session, company_id: str, build: Callable[[Session], T]) -> T:
    """build(session) on the replica when that's safe, otherwise on `primary`."""
    from app import database

    if database.ReadSessionLocal is None or cache.recently_written(company_id):
        return build(primary)
    replica = database.ReadSessionLocal()
    try:
        return build(replica)
    except (OperationalError, PoolTimeoutError) as exc:
        log.warning("read replica unavailable (%s); building report on the primary", type(exc).__name__)
        return build(primary)
    finally:
        replica.close()
