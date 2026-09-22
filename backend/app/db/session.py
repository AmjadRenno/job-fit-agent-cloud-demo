from __future__ import annotations

import os
from collections.abc import Iterator
from threading import Lock

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

POOL_SIZE = 5
MAX_OVERFLOW = 0
POOL_TIMEOUT_SECONDS = 30
POOL_RECYCLE_SECONDS = 1800

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_engine_lock = Lock()


def database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL must be set outside source code")
    return value


def get_engine() -> Engine:
    """Return the single process-lifetime Engine for the configured database."""
    global _engine, _session_factory
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                url = database_url()
                options = {"pool_pre_ping": True}
                # SQLite test databases use their dialect's appropriate pool.
                if not url.startswith("sqlite"):
                    options.update(
                        pool_size=POOL_SIZE,
                        max_overflow=MAX_OVERFLOW,
                        pool_timeout=POOL_TIMEOUT_SECONDS,
                        pool_recycle=POOL_RECYCLE_SECONDS,
                    )
                _engine = create_engine(url, **options)
                _session_factory = sessionmaker(
                    bind=_engine, autoflush=False, expire_on_commit=False
                )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def create_session_factory() -> sessionmaker[Session]:
    """Compatibility entry point for callers that need the shared factory."""
    return get_session_factory()


def dispose_engine() -> None:
    """Release pooled connections; used during application shutdown and tests."""
    global _engine, _session_factory
    with _engine_lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None


def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
