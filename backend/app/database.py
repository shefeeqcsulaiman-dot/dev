from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


settings = get_settings()

_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # Keep WAL from growing unbounded — checkpoint whenever it exceeds ~4MB (1000 pages × 4KB).
        # Without this the WAL accumulates indefinitely and slows every read.
        cursor.execute("PRAGMA wal_autocheckpoint=1000")
        cursor.execute("PRAGMA cache_size=-32000")   # 32MB page cache
        cursor.execute("PRAGMA busy_timeout=5000")   # 5s wait instead of SQLITE_BUSY error
        cursor.close()

else:
    engine = create_engine(
        settings.database_url,
        # Fail a connection attempt in 10 s instead of the OS default (~2 min per attempt), so an
        # unreachable host can't hold startup past the platform's health check.
        connect_args={"connect_timeout": 10},
        pool_pre_ping=True,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=300,
        pool_timeout=settings.db_pool_timeout,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# Read replica (DATABASE_READ_URL): only report reads use it, via app.read_replica.
read_engine = None
ReadSessionLocal = None
if settings.database_read_url:
    if settings.database_read_url.startswith("sqlite"):
        read_engine = create_engine(settings.database_read_url, connect_args={"check_same_thread": False}, pool_pre_ping=True)
    else:
        read_engine = create_engine(
            settings.database_read_url,
            connect_args={"connect_timeout": 10},
            pool_pre_ping=True,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_recycle=300,
            pool_timeout=settings.db_pool_timeout,
        )
    ReadSessionLocal = sessionmaker(bind=read_engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
