"""Run Alembic migrations with a PostgreSQL advisory lock.

Kubernetes starts an init container for every new API pod. Without a lock, a
rolling deploy or scale-out can run multiple ``alembic upgrade head`` commands
at the same time. The database, rather than a particular orchestrator,
serializes those schema changes here.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, create_engine, inspect, text

from .config import get_settings

logger = logging.getLogger("processtwin.migrations")

# Stable, application-specific signed bigint used only to serialize this
# application's schema changes. It is not an authorization boundary.
_MIGRATION_ADVISORY_LOCK = 4_817_413_926_519
_SAFE_ROLE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_CORE_TABLES = frozenset({"users", "organizations", "plants"})


def _uses_postgresql(database_url: str) -> bool:
    """Return whether the configured SQLAlchemy URL targets PostgreSQL."""
    return urlparse(database_url).scheme.startswith(("postgresql", "postgres"))


def _alembic_config() -> Config:
    """Locate the project-level Alembic configuration in source and images."""
    config_path = Path(__file__).resolve().parents[3] / "alembic.ini"
    return Config(str(config_path))


def _grant_app_role_permissions(connection: Connection, app_role: str) -> None:
    """Grant usage and DML on public schema to the application role."""
    clean_role = app_role.strip()
    if not clean_role or not _SAFE_ROLE_RE.match(clean_role):
        logger.warning("Skipping app role grant: invalid role identifier '%s'", clean_role)
        return

    safe_role = f'"{clean_role}"'
    connection.execute(text(f"GRANT USAGE ON SCHEMA public TO {safe_role};"))
    connection.execute(
        text(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {safe_role};"
        )
    )
    connection.execute(
        text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {safe_role};")
    )
    connection.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {safe_role};"
        )
    )
    connection.execute(
        text(
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {safe_role};"
        )
    )
    connection.commit()
    logger.info("Granted schema USAGE and table DML privileges to role '%s'", clean_role)


def run_migrations(override_url: str | None = None) -> None:
    """Upgrade to head, serializing PostgreSQL callers with an advisory lock."""
    admin_url = os.environ.get("DATABASE_ADMIN_URL", "").strip()
    database_url = override_url or admin_url or get_settings().database_url
    alembic_config = _alembic_config()
    if hasattr(alembic_config, "set_main_option"):
        alembic_config.set_main_option("sqlalchemy.url", database_url)

    # SQLite is used for local development and tests; advisory locks are a
    # PostgreSQL feature and a local migration command has no competing pods.
    if not _uses_postgresql(database_url):
        command.upgrade(alembic_config, "head")
        return

    lock_engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"options": "-c search_path=public"},
    )
    try:
        with lock_engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(:lock_id)"),
                {"lock_id": _MIGRATION_ADVISORY_LOCK},
            )
            try:
                # 1. Self-healing check for orphaned alembic_version on empty database
                try:
                    inspector = inspect(connection)
                    initial_tables = set(inspector.get_table_names(schema="public"))
                    if "alembic_version" in initial_tables and not (initial_tables & _CORE_TABLES):
                        logger.warning(
                            "alembic_version exists but core tables are missing from schema public; "
                            "clearing orphaned version marker to ensure migrations run."
                        )
                        connection.execute(text("DELETE FROM public.alembic_version;"))
                        connection.commit()
                except Exception as exc:
                    logger.debug("Pre-migration inspection skipped: %s", exc)

                # 2. Run migrations
                command.upgrade(alembic_config, "head")

                # 3. Post-migration schema verification
                try:
                    post_inspector = inspect(connection)
                    final_tables = set(post_inspector.get_table_names(schema="public"))
                    if "users" not in final_tables:
                        raise RuntimeError(
                            "Migration upgrade finished but required table 'users' does not exist in schema public."
                        )
                except Exception as exc:
                    if isinstance(exc, RuntimeError):
                        raise
                    logger.debug("Post-migration inspection skipped: %s", exc)

                # 4. Auto-grant non-owner application role permissions if configured
                app_role = os.environ.get("APP_ROLE", "").strip()
                if app_role:
                    _grant_app_role_permissions(connection, app_role)

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
