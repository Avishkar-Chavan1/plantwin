"""Regression tests for idempotent demo initialization."""

from __future__ import annotations

import pytest
from apps.api.processtwin_api.config import Settings
from apps.api.processtwin_api.database import Base, SessionLocal, engine
from apps.api.processtwin_api.models import ModelVersion, Organization
from apps.simulator.processtwin_simulator.seed_demo import seed
from apps.worker.processtwin_worker.train import train_all
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _override_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Override settings for test isolation."""
    monkeypatch.setenv("DEMO_EMAIL", "engineer@processtwin.demo")
    monkeypatch.setenv("DEMO_PASSWORD", "demo-password-123!")
    monkeypatch.setenv("JWT_SECRET", "test-secret-key-for-testing-only-123456789012345678901234")
    monkeypatch.setattr(
        "apps.api.processtwin_api.config.get_settings",
        lambda: Settings(
            database_url="sqlite:///./test.db",
            demo_email="engineer@processtwin.demo",
            demo_password="demo-password-123!",
            jwt_secret="test-secret-key-for-testing-only-123456789012345678901234",
            is_production=False,
            auto_create_schema=True,
        ),
    )


def test_seed_demo_idempotent(tmp_path) -> None:
    """Running seed_demo twice should not create duplicate organizations."""
    Base.metadata.create_all(bind=engine)

    # First run
    seed(1.0)

    with SessionLocal() as session:
        orgs = session.scalars(select(Organization).where(Organization.name == "ProcessTwin Demonstration")).all()
        assert len(orgs) == 1
        first_org_id = orgs[0].id

    # Second run should not create a new organization
    seed(1.0)

    with SessionLocal() as session:
        orgs = session.scalars(select(Organization).where(Organization.name == "ProcessTwin Demonstration")).all()
        assert len(orgs) == 1
        assert orgs[0].id == first_org_id


def test_train_idempotent(tmp_path) -> None:
    """Running train twice should create version 1 then version 2, not duplicate version 1."""
    Base.metadata.create_all(bind=engine)

    # First run: seed demo data
    seed(1.0)

    with SessionLocal() as session:
        org = session.scalar(select(Organization).where(Organization.name == "ProcessTwin Demonstration"))
        assert org is not None

    # First training run
    trained_first = train_all(org.id)
    assert trained_first == 1

    with SessionLocal() as session:
        models = session.scalars(
            select(ModelVersion).where(
                ModelVersion.organization_id == org.id,
                ModelVersion.name == "CSTR Yield Hybrid",
            )
        ).all()
        assert len(models) == 1
        assert models[0].version == "yield-residual-1"

    # Second training run should create version 2, not fail on duplicate
    trained_second = train_all(org.id)
    assert trained_second == 1

    with SessionLocal() as session:
        models = session.scalars(
            select(ModelVersion).where(
                ModelVersion.organization_id == org.id,
                ModelVersion.name == "CSTR Yield Hybrid",
            )
        ).all()
        assert len(models) == 2
        versions = {m.version for m in models}
        assert versions == {"yield-residual-1", "yield-residual-2"}