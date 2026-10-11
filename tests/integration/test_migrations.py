from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory

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


def test_clean_database_migrates_to_head_with_connector_tables(tmp_path: Path) -> None:
    """A brand-new database must reach head and contain the full connector/telemetry schema."""
    database = tmp_path / "clean.db"
    url = f"sqlite:///{database.as_posix()}"
    result = _run_alembic(url, "upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr

    inspector = sa.inspect(sa.create_engine(url))
    tables = set(inspector.get_table_names())
    from apps.api.processtwin_api.database import Base

    assert set(Base.metadata.tables) <= tables, sorted(set(Base.metadata.tables) - tables)
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


def test_migration_is_idempotent_on_repeat_and_after_downgrade(tmp_path: Path) -> None:
    url = f"sqlite:///{(tmp_path / 'repeat.db').as_posix()}"
    assert _run_alembic(url, "upgrade", "head").returncode == 0
    assert _run_alembic(url, "upgrade", "head").returncode == 0
    down = _run_alembic(url, "downgrade", "0008_reconcile_schema")
    assert down.returncode == 0, down.stdout + down.stderr
    up = _run_alembic(url, "upgrade", "head")
    assert up.returncode == 0, up.stdout + up.stderr
