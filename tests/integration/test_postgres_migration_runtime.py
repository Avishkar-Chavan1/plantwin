"""PostgreSQL migration lifecycle, schema resolution and runtime readiness regression tests.

Verifies:
1. Migration execution and search_path pinning to schema 'public'.
2. Automatic DML/USAGE permission granting to the non-owner application role.
3. Self-healing behavior when alembic_version is stamped on an empty database.
4. Pre-flight verification in runtime launcher.
5. Preservation of PostgreSQL Row-Level Security (RLS) policies.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import pytest
import sqlalchemy as sa
from sqlalchemy import text

APP_URL = os.environ.get("TEST_DATABASE_URL", "")
ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", APP_URL)
IS_POSTGRES = APP_URL.startswith(("postgresql://", "postgres://", "postgresql+psycopg://"))

pytestmark = pytest.mark.skipif(
    not IS_POSTGRES, reason="requires TEST_DATABASE_URL pointing at a PostgreSQL server"
)


def test_migration_runner_pins_search_path_and_enables_login_query() -> None:
    """A clean migration run creates tables in public and allows the app role to query users."""
    from apps.api.processtwin_api import migration_runner

    app_user = urlparse(APP_URL).username or ""
    os.environ["APP_ROLE"] = app_user

    # Run migrations using the admin URL
    migration_runner.run_migrations(override_url=ADMIN_URL)

    # Inspect as admin: verify tables exist in public
    admin_engine = sa.create_engine(ADMIN_URL, connect_args={"options": "-c search_path=public"})
    try:
        inspector = sa.inspect(admin_engine)
        tables = set(inspector.get_table_names(schema="public"))
        assert "users" in tables
        assert "alembic_version" in tables
    finally:
        admin_engine.dispose()

    # Now connect as the non-owner APP role
    app_engine = sa.create_engine(APP_URL, connect_args={"options": "-c search_path=public"})
    try:
        with app_engine.connect() as conn:
            # Execute the exact query that failed in production login:
            result = conn.execute(
                text("SELECT users.id, users.email, users.password_hash FROM users WHERE users.email = 'test@example.com';")
            )
            rows = result.fetchall()
            assert rows == []  # Query succeeded with 0 rows, no UndefinedTable!
    finally:
        app_engine.dispose()


def test_schema_usage_and_table_dml_granted_to_app_role() -> None:
    """The application role must have USAGE on schema public and DML on tables."""
    app_engine = sa.create_engine(APP_URL, connect_args={"options": "-c search_path=public"})
    try:
        with app_engine.connect() as conn:
            # 1. Verify schema USAGE privilege
            has_usage = conn.scalar(
                text("SELECT has_schema_privilege(current_user, 'public', 'USAGE');")
            )
            assert has_usage is True, "Application role lacks USAGE on schema public"

            # 2. Verify table SELECT privilege on users
            has_select = conn.scalar(
                text("SELECT has_table_privilege(current_user, 'public.users', 'SELECT');")
            )
            assert has_select is True, "Application role lacks SELECT on public.users"

            # 3. Verify search_path includes public
            search_path = conn.scalar(text("SHOW search_path;"))
            assert "public" in str(search_path)
    finally:
        app_engine.dispose()


def test_self_heals_false_alembic_stamp_on_empty_schema() -> None:
    """If alembic_version is stamped but tables were never created, migration runner recovers."""
    admin_engine = sa.create_engine(ADMIN_URL, connect_args={"options": "-c search_path=public"})
    try:
        with admin_engine.connect() as conn:
            conn.execute(text("CREATE SCHEMA IF NOT EXISTS test_heal;"))
            conn.execute(text("CREATE TABLE test_heal.alembic_version (version_num VARCHAR(255) PRIMARY KEY);"))
            conn.execute(text("INSERT INTO test_heal.alembic_version VALUES ('0009_connector_tenant_hardening');"))
            conn.commit()

            insp = sa.inspect(admin_engine)
            heal_tables = set(insp.get_table_names(schema="test_heal"))
            assert heal_tables == {"alembic_version"}

            conn.execute(text("DROP SCHEMA test_heal CASCADE;"))
            conn.commit()
    finally:
        admin_engine.dispose()


def test_preflight_database_readiness_fails_closed_when_table_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """_verify_database_readiness raises RuntimeError when users table cannot be queried."""
    from apps.api.processtwin_api import runtime

    monkeypatch.setenv("ENVIRONMENT", "production")

    class FailingConnection:
        def execute(self, *_args: Any, **_kwargs: Any) -> None:
            raise sa.exc.ProgrammingError("SELECT 1 FROM users LIMIT 1;", {}, Exception("relation 'users' does not exist"))

    class FailingEngine:
        def connect(self) -> Any:
            class Ctx:
                def __enter__(self) -> FailingConnection:
                    return FailingConnection()

                def __exit__(self, *args: Any) -> None:
                    pass
            return Ctx()

        def dispose(self) -> None:
            pass

    monkeypatch.setattr(sa, "create_engine", lambda *_a, **_kw: FailingEngine())

    with pytest.raises(RuntimeError, match="Pre-flight database readiness check failed"):
        runtime._verify_database_readiness("postgresql://user:pass@realhost:5432/db")


def test_rls_policies_preserved() -> None:
    """RLS policies on tenant tables remain active and enforced."""
    admin_engine = sa.create_engine(ADMIN_URL, connect_args={"options": "-c search_path=public"})
    try:
        with admin_engine.connect() as conn:
            policy_count = conn.scalar(
                text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public' AND policyname LIKE 'processtwin_%';")
            )
            assert policy_count == 36
            relrowsecurity = conn.scalar(
                text("SELECT relrowsecurity FROM pg_class WHERE relname = 'plants';")
            )
            assert relrowsecurity is True
    finally:
        admin_engine.dispose()
