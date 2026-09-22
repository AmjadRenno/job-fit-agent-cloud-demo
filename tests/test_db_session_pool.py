from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.pool import QueuePool

import backend.app.db.session as database
from backend.app.dashboard.api import get_session
from backend.app.main import ready


@pytest.fixture(autouse=True)
def reset_shared_engine():
    database.dispose_engine()
    yield
    database.dispose_engine()


def test_repeated_factory_access_creates_one_engine_and_one_pool(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:password@db.example/jobfit")
    original_create_engine = database.create_engine
    calls = []

    def tracked_create_engine(*args, **kwargs):
        calls.append((args, kwargs))
        return original_create_engine("sqlite://", poolclass=QueuePool, **kwargs)

    monkeypatch.setattr(database, "create_engine", tracked_create_engine)
    first = database.get_session_factory()
    second = database.create_session_factory()

    assert first is second
    assert database.get_engine() is first.kw["bind"]
    assert len(calls) == 1


def test_postgresql_pool_is_bounded(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:password@db.example/jobfit")
    original_create_engine = database.create_engine

    def sqlite_engine_with_production_options(*args, **kwargs):
        return original_create_engine("sqlite://", poolclass=QueuePool, **kwargs)

    monkeypatch.setattr(database, "create_engine", sqlite_engine_with_production_options)

    engine = database.get_engine()

    assert engine.pool.size() == 5
    assert engine.pool._max_overflow == 0
    assert engine.pool._timeout == 30
    assert engine.pool._recycle == 1800
    assert engine.pool._pre_ping is True


def test_successful_dependency_cleanup_returns_connection(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'successful.db'}")
    dependency = get_session()
    session = next(dependency)
    session.execute(text("SELECT 1"))
    dependency.close()

    assert database.get_engine().pool.checkedout() == 0


def test_exception_dependency_cleanup_rolls_back_and_returns_connection(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'exception.db'}")
    dependency = get_session()
    session = next(dependency)
    session.execute(text("SELECT 1"))

    with pytest.raises(RuntimeError, match="request failed"):
        dependency.throw(RuntimeError("request failed"))

    assert database.get_engine().pool.checkedout() == 0


def test_readiness_reuses_shared_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'ready.db'}")
    engine = database.get_engine()

    assert ready() == {"status": "ready"}
    assert database.get_engine() is engine
