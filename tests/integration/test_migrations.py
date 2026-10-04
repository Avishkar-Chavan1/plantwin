from __future__ import annotations

from alembic.config import Config
from alembic.script import ScriptDirectory


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
