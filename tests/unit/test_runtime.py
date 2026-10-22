"""Tests for Render's migrate-before-serving startup launcher."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep
from types import SimpleNamespace
from typing import Any

import pytest
from alembic import command as alembic_command
from apps.api.processtwin_api import migration_runner, runtime

_RUNTIME_URL = "postgresql+psycopg://app_role:synthetic-app@db.example.test:5432/processtwin"
_ADMIN_URL = (
    "postgresql+psycopg://owner_role:synthetic-owner@db.example.test:5432/processtwin"
)


def _set_required_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", _RUNTIME_URL)
    monkeypatch.setenv("DATABASE_ADMIN_URL", _ADMIN_URL)
    monkeypatch.setenv("PORT", "10000")


def test_runtime_runs_migrations_before_exec_and_keeps_roles_separate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_environment(monkeypatch)
    events: list[tuple[str, Any]] = []

    def record_migration(
        command: list[str], *, check: bool, env: dict[str, str]
    ) -> None:
        events.append(("migration", (command, check, env)))

    class ExecReached(BaseException):
        pass

    def record_exec(
        executable: str, args: list[str], env: dict[str, str]
    ) -> None:
        events.append(("exec", (executable, args, env)))
        raise ExecReached

    monkeypatch.setattr(subprocess, "run", record_migration)
    monkeypatch.setattr(os, "execvpe", record_exec)

    with pytest.raises(ExecReached):
        runtime.start_api()

    assert [event[0] for event in events] == ["migration", "exec"]
    migration_command, check, migration_env = events[0][1]
    assert migration_command[1:] == ["-m", runtime._MIGRATION_MODULE]
    assert check is True
    assert migration_env["DATABASE_URL"] == _ADMIN_URL
    assert "DATABASE_ADMIN_URL" not in migration_env

    executable, command, api_env = events[1][1]
    assert executable == "gunicorn"
    assert command[1] == runtime._API_APP
    assert command[command.index("--bind") + 1] == "0.0.0.0:10000"
    assert command[command.index("--workers") + 1] == "1"
    assert api_env["DATABASE_URL"] == _RUNTIME_URL
    assert "DATABASE_ADMIN_URL" not in api_env


def test_runtime_fails_closed_when_migration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_environment(monkeypatch)
    server_started = False

    def fail_migration(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.CalledProcessError(1, "migration_runner")

    def record_exec(*_args: Any, **_kwargs: Any) -> None:
        nonlocal server_started
        server_started = True

    monkeypatch.setattr(subprocess, "run", fail_migration)
    monkeypatch.setattr(os, "execvpe", record_exec)

    with pytest.raises(subprocess.CalledProcessError):
        runtime.start_api()

    assert not server_started


def test_runtime_requires_both_postgresql_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", _RUNTIME_URL)
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL and DATABASE_ADMIN_URL"):
        runtime.start_api()


def test_runtime_rejects_same_database_role_for_migrations_and_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", _RUNTIME_URL)
    monkeypatch.setenv("DATABASE_ADMIN_URL", _RUNTIME_URL)

    with pytest.raises(RuntimeError, match="separate database roles"):
        runtime.start_api()


def test_runtime_accepts_same_database_role_when_explicitly_permitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", _RUNTIME_URL)
    monkeypatch.setenv("DATABASE_ADMIN_URL", _RUNTIME_URL)
    monkeypatch.setenv("ALLOW_SAME_DATABASE_ROLE", "true")
    monkeypatch.setenv("PORT", "10000")

    def record_migration(*_args: Any, **_kwargs: Any) -> None:
        pass

    class ExecReached(BaseException):
        pass

    def record_exec(*_args: Any, **_kwargs: Any) -> None:
        raise ExecReached

    monkeypatch.setattr(subprocess, "run", record_migration)
    monkeypatch.setattr(os, "execvpe", record_exec)

    with pytest.raises(ExecReached):
        runtime.start_api()


def test_runtime_allows_omitted_admin_url_when_same_role_permitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", _RUNTIME_URL)
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    monkeypatch.setenv("ALLOW_SAME_DATABASE_ROLE", "true")
    monkeypatch.setenv("PORT", "8000")

    def record_migration(*_args: Any, **_kwargs: Any) -> None:
        pass

    class ExecReached(BaseException):
        pass

    def record_exec(executable: str, args: list[str], env: dict[str, str]) -> None:
        assert executable == "gunicorn"
        assert env["DATABASE_URL"] == _RUNTIME_URL
        assert "DATABASE_ADMIN_URL" not in env
        raise ExecReached

    monkeypatch.setattr(subprocess, "run", record_migration)
    monkeypatch.setattr(os, "execvpe", record_exec)

    with pytest.raises(ExecReached):
        runtime.start_api()


def test_runtime_executes_custom_command_after_migrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_environment(monkeypatch)
    events: list[str] = []

    def record_migration(*_args: Any, **_kwargs: Any) -> None:
        events.append("migration")

    class ExecReached(BaseException):
        pass

    def record_exec(executable: str, args: list[str], env: dict[str, str]) -> None:
        events.append(f"exec:{executable}")
        assert args == ["custom-server", "--port", "8000"]
        raise ExecReached

    monkeypatch.setattr(subprocess, "run", record_migration)
    monkeypatch.setattr(os, "execvpe", record_exec)

    with pytest.raises(ExecReached):
        runtime.start_api(custom_command=["custom-server", "--port", "8000"])

    assert events == ["migration", "exec:custom-server"]


def test_concurrent_startup_migrations_are_serialized_by_advisory_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_lock = Lock()
    state_lock = Lock()
    active_upgrades = 0
    maximum_active_upgrades = 0
    upgrade_count = 0

    class Connection:
        def execute(self, statement: Any, _params: dict[str, int]) -> None:
            sql = str(statement)
            if "pg_advisory_lock" in sql:
                database_lock.acquire()
            elif "pg_advisory_unlock" in sql:
                database_lock.release()

    class ConnectionContext:
        def __enter__(self) -> Connection:
            return Connection()

        def __exit__(self, *_args: object) -> None:
            return None

    class Engine:
        def connect(self) -> ConnectionContext:
            return ConnectionContext()

        def dispose(self) -> None:
            pass

    def upgrade(*_args: object) -> None:
        nonlocal active_upgrades, maximum_active_upgrades, upgrade_count
        with state_lock:
            active_upgrades += 1
            upgrade_count += 1
            maximum_active_upgrades = max(maximum_active_upgrades, active_upgrades)
        sleep(0.02)
        with state_lock:
            active_upgrades -= 1

    monkeypatch.setattr(
        migration_runner,
        "get_settings",
        lambda: SimpleNamespace(database_url=_RUNTIME_URL),
    )
    monkeypatch.setattr(migration_runner, "_alembic_config", lambda: object())
    monkeypatch.setattr(migration_runner, "create_engine", lambda *_args, **_kwargs: Engine())
    monkeypatch.setattr(alembic_command, "upgrade", upgrade)

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(migration_runner.run_migrations) for _ in range(2)]
        for future in futures:
            future.result()

    assert upgrade_count == 2
    assert maximum_active_upgrades == 1
