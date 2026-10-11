from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.orm import Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_initial_migration_is_discoverable() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("0001_initial_schema")
    assert revision is not None
    assert revision.down_revision is None


def test_historical_dataset_migration_follows_initial_schema() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("0002_historical_datasets")
    assert revision is not None
    assert revision.down_revision == "0001_initial_schema"


def test_calibration_registry_migration_follows_historical_datasets() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("0003_calibration_registry")
    assert revision is not None
    assert revision.down_revision == "0002_historical_datasets"


def test_security_migration_follows_live_readonly_workflow() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("0006_security_rls_refresh_tokens")
    assert revision is not None
    assert revision.down_revision == "0005_live_readonly_workflow"


def test_timescale_migration_follows_object_storage() -> None:
    config = Config("alembic.ini")
    scripts = ScriptDirectory.from_config(config)
    revision = scripts.get_revision("660c4eb0949d")
    assert revision is not None
    assert revision.down_revision == "0007_object_storage_chunked_imports"

def _run_alembic(database_url: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "DATABASE_URL": database_url,
        "ENVIRONMENT": "test",
        "JWT_SECRET": "test-secret-not-for-deployment-0123456789",
    }
    return subprocess.run(  # noqa: S603 - fixed argument vector, no shell
        [sys.executable, "-m", "alembic", *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_single_migration_head() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    assert scripts.get_heads() == ["0009_connector_tenant_hardening"]


def test_connector_hardening_follows_reconcile_schema() -> None:
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    revision = scripts.get_revision("0009_connector_tenant_hardening")
    assert revision is not None
    assert revision.down_revision == "0008_reconcile_schema"


def test_clean_database_migrates_to_head_and_accepts_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migrations must create login tables and allow login without missing-table errors."""
    database = tmp_path / "clean.db"
    url = f"sqlite:///{database.as_posix()}"
    result = _run_alembic(url, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr

    migrated_engine = sa.create_engine(url)
    inspector = sa.inspect(migrated_engine)
    tables = set(inspector.get_table_names())
    from apps.api.processtwin_api.database import Base

    assert set(Base.metadata.tables) <= tables, sorted(set(Base.metadata.tables) - tables)
    login_tables = {
        "users",
        "organizations",
        "organization_memberships",
        "refresh_tokens",
        "audit_logs",
    }
    assert login_tables <= tables
    for table in ("connector_tags", "connector_tag_mappings", "connector_validation_rules"):
        foreign_keys = inspector.get_foreign_keys(table)
        assert any(fk["referred_table"] == "organizations" for fk in foreign_keys), table
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert not columns["organization_id"]["nullable"]
        assert "created_at" in columns and "updated_at" in columns
    unique = {
        constraint["name"]: constraint["column_names"]
        for constraint in inspector.get_unique_constraints("connector_tags")
    }
    assert unique["uq_connector_tag_org_tag"] == ["organization_id", "tag"]
    reading_indexes = {index["name"] for index in inspector.get_indexes("sensor_readings")}
    assert {"ix_readings_sensor_timestamp", "ix_readings_source_timestamp"} <= reading_indexes

    current = _run_alembic(url, "current")
    assert "0009_connector_tenant_hardening" in current.stdout + current.stderr

    from apps.api.processtwin_api.auth import hash_password
    from apps.api.processtwin_api.database import get_session
    from apps.api.processtwin_api.main import app
    from apps.api.processtwin_api.models import (
        Organization,
        OrganizationMembership,
        RoleName,
        User,
    )
    from fastapi.testclient import TestClient

    migrated_sessions = sessionmaker(
        bind=migrated_engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )
    with migrated_sessions() as session:
        organization = Organization(name="Migrated Login Test")
        user = User(
            email="migrated-login@example.test",
            password_hash=hash_password("SyntheticMigrationPassword123!"),
        )
        session.add_all([organization, user])
        session.flush()
        session.add(
            OrganizationMembership(
                organization_id=organization.id,
                user_id=user.id,
                role=RoleName.ENGINEER,
            )
        )
        session.commit()

    def migrated_session() -> Iterator[Session]:
        with migrated_sessions() as session:
            yield session

    monkeypatch.setitem(app.dependency_overrides, get_session, migrated_session)
    response = TestClient(app).post(
        "/api/v1/auth/login",
        json={
            "email": "migrated-login@example.test",
            "password": "SyntheticMigrationPassword123!",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["access_token"]
    assert response.json()["refresh_token"]
    migrated_engine.dispose()


def test_migration_is_idempotent_on_repeat_and_after_downgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'repeat.db').as_posix()}"
    assert _run_alembic(url, "upgrade", "head").returncode == 0
    assert _run_alembic(url, "upgrade", "head").returncode == 0
    down = _run_alembic(url, "downgrade", "0008_reconcile_schema")
    assert down.returncode == 0, down.stdout + down.stderr
    up = _run_alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
