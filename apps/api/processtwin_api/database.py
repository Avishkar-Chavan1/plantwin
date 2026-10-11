from __future__ import annotations

from collections.abc import Generator
from typing import Any, cast

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _engine_url() -> str:
    return get_settings().database_url


settings = get_settings()
_url = _engine_url()
_is_sqlite = _url.startswith("sqlite")
engine = create_engine(
    _url,
    pool_pre_ping=True,
    pool_recycle=1_800,
    pool_size=settings.database_pool_size if not _is_sqlite else 5,
    max_overflow=settings.database_max_overflow if not _is_sqlite else 0,
    pool_timeout=settings.database_pool_timeout_s,
    connect_args=cast(
        dict[str, Any],
        (
            {"check_same_thread": False}
            if _is_sqlite
            else {
                "connect_timeout": settings.database_connect_timeout_s,
                "application_name": "processtwin-api",
            }
        ),
    ),
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@event.listens_for(SessionLocal, "after_begin")
def _reapply_rls_context(session: Session, _transaction: object, connection: Any) -> None:
    """Re-assert the RLS principal/tenant on every new transaction of a session.

    ``set_config(..., true)`` is transaction-local, so after any ``commit()`` the settings
    vanish and later statements in the same request would silently see (or write) no rows
    under row-level security. The values are remembered on the session and re-applied.
    """
    if connection.dialect.name != "postgresql":
        return
    for key, setting in (
        ("rls_user_id", "app.current_user_id"),
        ("rls_organization_id", "app.current_organization_id"),
    ):
        value = session.info.get(key)
        if value:
            connection.execute(
                text("SELECT set_config(:setting, :value, true)"),
                {"setting": setting, "value": value},
            )


def set_request_principal(session: Session, user_id: str) -> None:
    """Set the PostgreSQL RLS principal for the transaction, when RLS is enabled."""
    session.info["rls_user_id"] = user_id
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT set_config('app.current_user_id', :user_id, true)"), {"user_id": user_id}
        )


def set_tenant_context(session: Session, organization_id: str) -> None:
    """Set the PostgreSQL tenant guard for the transaction, after membership verification."""
    session.info["rls_organization_id"] = organization_id
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT set_config('app.current_organization_id', :organization_id, true)"),
            {"organization_id": organization_id},
        )


def database_is_ready() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
