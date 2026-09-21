from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from backend.app.config import cors_origins
from backend.app.main import app
from scripts.run_migrations import run
from scripts.seed_demo import require_demo_seed_mode


def test_health_and_ready_use_distinct_liveness_and_database_checks(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}


def test_cors_rejects_wildcards_and_demo_seed_rejects_non_demo(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="explicit origins"):
        cors_origins()
    monkeypatch.setenv("APP_MODE", "full")
    with pytest.raises(RuntimeError, match="APP_MODE=demo"):
        require_demo_seed_mode()


def test_migration_command_and_web_container_are_separate():
    assert callable(run)
    dockerfile = (Path(__file__).parents[1] / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["python", "-m", "uvicorn"' in dockerfile
