import os

import pytest


@pytest.fixture(autouse=True)
def normal_mode_for_existing_regressions(monkeypatch):
    """Existing domain tests exercise mutable behavior outside the public demo."""
    monkeypatch.setenv("APP_MODE", "full")

