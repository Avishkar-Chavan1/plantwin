"""Run Alembic migrations with a PostgreSQL advisory lock.

Kubernetes starts an init container for every new API pod. Without a lock, a
rolling deploy or scale-out can run multiple ``alembic upgrade head`` commands
at the same time. The database, rather than a particular orchestrator,
serializes those schema changes here.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from .config import get_settings

# Stable, application-specific signed bigint used only to serialize this
# application's schema changes. It is not an authorization boundary.
_MIGRATION_ADVISORY_LOCK = 4_817_413_926_519


def _uses_postgresql(database_url: str) -> bool:
    """Return whether the configured SQLAlchemy URL targets PostgreSQL."""
    return urlparse(database_url).scheme.startswith("postgresql")


def _alembic_config() -> Config:
    """Locate the project-level Alembic configuration in source and images."""
    config_path = Path(__file__).resolve().parents[3] / "alembic.ini"
    return Config(str(config_path))


def run_migrations() -> None:
    """Upgrade to head, serializing PostgreSQL callers with an advisory lock."""
    database_url = get_settings().database_url
    alembic_config = _alembic_config()

    # SQLite is used for local development and tests; advisory locks are a
    # PostgreSQL feature and a local migration command has no competing pods.
    if not _uses_postgresql(database_url):
        command.upgrade(alembic_config, "head")
        return

    lock_engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with lock_engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": _MIGRATION_ADVISORY_LOCK},
            )
            try:
                command.upgrade(alembic_config, "head")
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"),
                    {"lock_id": _MIGRATION_ADVISORY_LOCK},
                )
    finally:
        lock_engine.dispose()


def main() -> None:
    """CLI entry point used by Compose and Kubernetes migration workloads."""
    run_migrations()


if __name__ == "__main__":
    main()
