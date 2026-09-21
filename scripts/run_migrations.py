"""Explicit deployment migration command; web processes never run this automatically."""
from __future__ import annotations

from alembic import command
from alembic.config import Config


def run() -> None:
    command.upgrade(Config("alembic.ini"), "head")


if __name__ == "__main__":
    run()
