"""Regression tests for deployment migration serialization."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from apps.api.processtwin_api import migration_runner


def test_detects_postgresql_sqlalchemy_urls() -> None:
    assert migration_runner._uses_postgresql("postgresql+psycopg://user:pass@db/process")
    assert not migration_runner._uses_postgresql("sqlite:///./processtwin.db")


def test_sqlite_migration_uses_alembic_without_postgresql_lock(monkeypatch: Any) -> None:
    config = object()
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(
        migration_runner, "get_settings", lambda: SimpleNamespace(database_url="sqlite:///test.db")
    )
    monkeypatch.setattr(migration_runner, "_alembic_config", lambda: config)
    monkeypatch.setattr(
        migration_runner.command,
        "upgrade",
        lambda received_config, revision: calls.append((received_config, revision)),
    )
    monkeypatch.setattr(
        migration_runner,
        "create_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not create a lock engine")),
    )

    migration_runner.run_migrations()

    assert calls == [(config, "head")]


def test_postgresql_migration_locks_and_unlocks_even_when_alembic_fails(monkeypatch: Any) -> None:
    executed: list[str] = []
    disposed: list[bool] = []

    class Connection:
        def execute(self, statement: Any, _params: dict[str, int]) -> None:
            executed.append(str(statement))

    class ConnectionContext:
        def __enter__(self) -> Connection:
            return Connection()

        def __exit__(self, *_args: object) -> None:
            return None

    class Engine:
        def connect(self) -> ConnectionContext:
            return ConnectionContext()

        def dispose(self) -> None:
            disposed.append(True)

    monkeypatch.setattr(
        migration_runner,
        "get_settings",
        lambda: SimpleNamespace(database_url="postgresql+psycopg://user:pass@db/process"),
    )
    monkeypatch.setattr(migration_runner, "_alembic_config", lambda: object())
    monkeypatch.setattr(migration_runner, "create_engine", lambda *_args, **_kwargs: Engine())

    def failing_upgrade(*_args: object) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(migration_runner.command, "upgrade", failing_upgrade)

    try:
        migration_runner.run_migrations()
    except RuntimeError as exc:
        assert str(exc) == "migration failed"
    else:
        raise AssertionError("migration failure should propagate")

    assert "pg_advisory_lock" in executed[0]
    assert "pg_advisory_unlock" in executed[1]
    assert disposed == [True]
