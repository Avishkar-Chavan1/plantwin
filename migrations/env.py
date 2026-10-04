import apps.api.processtwin_api.models  # noqa: F401 - register model metadata
from alembic import context
from alembic.ddl import postgresql
from alembic.ddl.impl import _impls
from apps.api.processtwin_api.config import get_settings
from apps.api.processtwin_api.database import Base
from sqlalchemy import Column, MetaData, PrimaryKeyConstraint, String, Table
from sqlalchemy import engine_from_config, pool


class ProcessTwinPostgresqlImpl(postgresql.PostgresqlImpl):
    """Custom PostgreSQL implementation with longer alembic_version.version_num column."""

    def version_table_impl(
        self,
        *,
        version_table: str,
        version_table_schema: str | None,
        version_table_pk: bool,
        **kw: object,
    ) -> Table:
        """Generate version table with version_num column supporting longer revision IDs."""
        vt = Table(
            version_table,
            MetaData(),
            Column("version_num", String(255), nullable=False),
            schema=version_table_schema,
        )
        if version_table_pk:
            vt.append_constraint(
                PrimaryKeyConstraint(
                    "version_num", name=f"{version_table}_pkc"
                )
            )
        return vt


# Register custom implementation for PostgreSQL dialect before any context configuration
_impls["postgresql"] = ProcessTwinPostgresqlImpl

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()