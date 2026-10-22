"""Integration test proving that runtime.py initializes an empty database before Gunicorn starts."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy import text

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_production_runtime_initializes_empty_db_before_gunicorn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof of lifecycle:

    1. Starts with an EMPTY database (0 tables).
    2. runtime.start_api() executes.
    3. Migration runner upgrades the empty database to head (all tables created).
    4. Database readiness check verifies 'users' table exists.
    5. ONLY THEN does the web server start (execvpe is reached).
    """
    from apps.api.processtwin_api import runtime

    # Step 1: Create a completely empty SQLite database file
    db_file = tmp_path / "empty_prod.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    # Verify database is currently empty (0 tables)
    probe_engine = sa.create_engine(db_url)
    try:
        inspector = sa.inspect(probe_engine)
        initial_tables = inspector.get_table_names()
        assert len(initial_tables) == 0, f"Expected 0 tables, found: {initial_tables}"
    finally:
        probe_engine.dispose()

    # Step 2: Set production-like environment variables
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DATABASE_ADMIN_URL", db_url)
    monkeypatch.setenv("ALLOW_SAME_DATABASE_ROLE", "true")
    monkeypatch.setenv("PORT", "8000")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_SECRET", "test-secret-not-for-deployment-0123456789")

    # Hook os.execvpe to capture the moment Gunicorn is about to be launched
    gunicorn_launched = False
    gunicorn_args: list[str] = []

    class ServerStarted(BaseException):
        pass

    def mock_execvpe(file: str, args: list[str], env: dict[str, str]) -> None:
        nonlocal gunicorn_launched, gunicorn_args
        gunicorn_launched = True
        gunicorn_args = args

        # At the exact moment Gunicorn is about to start, verify the database:
        verify_engine = sa.create_engine(db_url)
        try:
            insp = sa.inspect(verify_engine)
            post_tables = set(insp.get_table_names())

            # Verify core tables were created by migrations
            assert "users" in post_tables, "users table missing before server launch"
            assert "organizations" in post_tables, "organizations table missing"
            assert "plants" in post_tables, "plants table missing"
            assert "refresh_tokens" in post_tables, "refresh_tokens table missing"
            assert "alembic_version" in post_tables, "alembic_version table missing"

            # Verify alembic_version reached head
            with verify_engine.connect() as conn:
                version = conn.scalar(text("SELECT version_num FROM alembic_version;"))
                assert version == "0009_connector_tenant_hardening", f"Wrong version: {version}"
        finally:
            verify_engine.dispose()

        raise ServerStarted

    monkeypatch.setattr(os, "execvpe", mock_execvpe)

    # Step 3: Run runtime.start_api()
    with pytest.raises(ServerStarted):
        runtime.start_api()

    # Step 4: Confirm Gunicorn launch was reached ONLY after successful migrations
    assert gunicorn_launched is True
    assert gunicorn_args[0] == "gunicorn"
    assert "apps.api.processtwin_api.main:app" in gunicorn_args


def test_production_runtime_refuses_to_start_gunicorn_if_migration_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proof that if migrations fail, Gunicorn is NEVER started."""
    from apps.api.processtwin_api import runtime

    db_url = f"sqlite:///{(tmp_path / 'fail.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DATABASE_ADMIN_URL", db_url)
    monkeypatch.setenv("ALLOW_SAME_DATABASE_ROLE", "true")
    monkeypatch.setenv("PORT", "8000")

    def fail_migration(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.CalledProcessError(1, "migration_runner")

    server_reached = False

    def mock_execvpe(*_args: Any, **_kwargs: Any) -> None:
        nonlocal server_reached
        server_reached = True

    monkeypatch.setattr(subprocess, "run", fail_migration)
    monkeypatch.setattr(os, "execvpe", mock_execvpe)

    with pytest.raises(subprocess.CalledProcessError):
        runtime.start_api()

    assert server_reached is False, "Gunicorn must NOT start when migrations fail"

def test_production_runtime_prints_startup_banners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Proof that runtime.py prints clear, unbuffered startup banners for cloud logs."""
    from apps.api.processtwin_api import runtime

    db_file = tmp_path / "banners.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", db_url)
    monkeypatch.setenv("DATABASE_ADMIN_URL", db_url)
    monkeypatch.setenv("ALLOW_SAME_DATABASE_ROLE", "true")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("JWT_SECRET", "test-secret-not-for-deployment-0123456789")

    class ServerStarted(BaseException):
        pass

    def mock_execvpe(file: str, args: list[str], env: dict[str, str]) -> None:
        raise ServerStarted

    monkeypatch.setattr(os, "execvpe", mock_execvpe)

    with pytest.raises(ServerStarted):
        runtime.start_api()

    captured = capsys.readouterr()
    assert "[ProcessTwin Startup] Initializing runtime..." in captured.out
    assert "[ProcessTwin Startup] Executing database migrations to head..." in captured.out
    assert "[ProcessTwin Startup] Migrations completed." in captured.out
    assert "[ProcessTwin Startup] Database readiness verified successfully." in captured.out
    assert "[ProcessTwin Startup] Launching Gunicorn master on port 9000..." in captured.out

