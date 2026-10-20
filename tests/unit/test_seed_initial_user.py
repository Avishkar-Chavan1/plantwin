"""Unit tests for scripts/seed_initial_user.py."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from apps.api.processtwin_api.auth import verify_password
from apps.api.processtwin_api.database import Base
from apps.api.processtwin_api.models import (
    Organization,
    OrganizationMembership,
    Plant,
    RoleName,
    User,
)
from scripts.seed_initial_user import (
    clean_input_url,
    main,
    mask_url,
    normalize_database_url,
    seed_user,
)
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


@pytest.fixture
def sqlite_engine() -> Generator[Engine, None, None]:
    """Create an in-memory SQLite engine with the full schema."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def test_clean_input_url() -> None:
    assert clean_input_url(None) == ""
    assert clean_input_url("") == ""
    assert clean_input_url("   ") == ""
    assert clean_input_url('"postgresql://db"') == "postgresql://db"
    assert clean_input_url("'postgresql://db'") == "postgresql://db"
    assert clean_input_url('  " postgresql://db " ') == "postgresql://db"


def test_normalize_database_url() -> None:
    assert (
        normalize_database_url("postgres://user:pass@host/db")
        == "postgresql://user:pass@host/db"
    )
    assert (
        normalize_database_url("postgresql://user:pass@host/db")
        == "postgresql://user:pass@host/db"
    )
    assert normalize_database_url("sqlite:///db.sqlite") == "sqlite:///db.sqlite"


def test_mask_url() -> None:
    assert mask_url(None) == "<not set>"
    assert mask_url("") == "<not set>"
    assert (
        mask_url("postgresql://admin:secret_pass@db.example.com:5432/plantwin")
        == "postgresql://admin:***@db.example.com:5432/plantwin"
    )


def test_seed_user_fresh_database(sqlite_engine: Engine) -> None:
    result = seed_user(
        sqlite_engine,
        email="test.engineer@example.com",
        password="SafePassword123!",
        org_name="Test Enterprise",
        plant_name="Plant Alpha",
        role=RoleName.OWNER,
    )

    assert result["organization_id"]
    assert result["user_id"]
    assert result["plant_id"]

    with Session(sqlite_engine) as session:
        user = session.scalar(select(User).where(User.email == "test.engineer@example.com"))
        assert user is not None
        assert user.is_active is True
        assert verify_password("SafePassword123!", user.password_hash)

        org = session.scalar(select(Organization).where(Organization.name == "Test Enterprise"))
        assert org is not None

        membership = session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org.id,
                OrganizationMembership.user_id == user.id,
            )
        )
        assert membership is not None
        assert membership.role == RoleName.OWNER

        plant = session.scalar(
            select(Plant).where(
                Plant.organization_id == org.id,
                Plant.name == "Plant Alpha",
            )
        )
        assert plant is not None


def test_seed_user_idempotent(sqlite_engine: Engine) -> None:
    first_result = seed_user(
        sqlite_engine,
        email="idempotent@example.com",
        password="SafePassword123!",
    )
    second_result = seed_user(
        sqlite_engine,
        email="idempotent@example.com",
        password="SafePassword123!",
    )

    assert first_result == second_result

    with Session(sqlite_engine) as session:
        users = list(session.scalars(select(User).where(User.email == "idempotent@example.com")))
        assert len(users) == 1


def test_seed_user_update_password(sqlite_engine: Engine) -> None:
    seed_user(
        sqlite_engine,
        email="update.pass@example.com",
        password="OldPassword123!",
    )

    # Without update_password flag, password should remain unchanged
    seed_user(
        sqlite_engine,
        email="update.pass@example.com",
        password="NewPassword456!",
        update_password=False,
    )
    with Session(sqlite_engine) as session:
        user = session.scalar(select(User).where(User.email == "update.pass@example.com"))
        assert user is not None
        assert verify_password("OldPassword123!", user.password_hash)

    # With update_password flag, password is updated
    seed_user(
        sqlite_engine,
        email="update.pass@example.com",
        password="NewPassword456!",
        update_password=True,
    )
    with Session(sqlite_engine) as session:
        user = session.scalar(select(User).where(User.email == "update.pass@example.com"))
        assert user is not None
        assert verify_password("NewPassword456!", user.password_hash)


def test_seed_user_rejects_invalid_email(sqlite_engine: Engine) -> None:
    with pytest.raises(ValueError, match="Invalid email"):
        seed_user(
            sqlite_engine,
            email="not-an-email",
            password="SafePassword123!",
        )


def test_seed_user_rejects_weak_password(sqlite_engine: Engine) -> None:
    with pytest.raises(ValueError, match="Password does not satisfy policy"):
        seed_user(
            sqlite_engine,
            email="valid@example.com",
            password="short",
        )


def test_main_cli_missing_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_ADMIN_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["seed_initial_user.py"])
    exit_code = main()
    assert exit_code == 1

