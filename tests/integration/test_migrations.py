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
