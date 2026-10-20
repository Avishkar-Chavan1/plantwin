import os

import apps.api.processtwin_api.models  # noqa: F401 - register model metadata
from alembic import context
from alembic.ddl import postgresql
from alembic.ddl.impl import _impls
from apps.api.processtwin_api.config import get_settings
from apps.api.processtwin_api.database import Base
from sqlalchemy import (
    Column,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    engine_from_config,
    pool,
)


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
admin_url = os.environ.get("DATABASE_ADMIN_URL", "").strip()
config_url = config.get_main_option("sqlalchemy.url")
default_ini_url = "sqlite:///./processtwin.db"

if admin_url:
    target_url = admin_url
elif config_url and config_url != default_ini_url:
    target_url = config_url
else:
    target_url = get_settings().database_url or config_url or default_ini_url

if target_url.startswith("postgres://"):
    target_url = "postgresql://" + target_url[len("postgres://") :]

config.set_main_option("sqlalchemy.url", target_url)
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
    url = config.get_main_option("sqlalchemy.url") or ""
    is_postgres = url.startswith(("postgresql", "postgres"))
    connect_args = {"options": "-c search_path=public"} if is_postgres else {}
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            version_table_schema="public" if is_postgres else None,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()