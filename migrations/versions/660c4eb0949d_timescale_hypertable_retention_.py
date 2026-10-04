"""TimescaleDB hypertable, retention, and compression policies for sensor_readings.

Revision ID: 660c4eb0949d
Revises: 0007_object_storage_chunked_imports
Create Date: 2026-10-03 20:31:10.183123
"""
from alembic import op
import sqlalchemy as sa

revision = '660c4eb0949d'
down_revision = '0007_object_storage_chunked_imports'
branch_labels = None
depends_on = None


def _is_timescale_available(connection: sa.Connection) -> bool:
    """Check if TimescaleDB extension is available."""
    try:
        result = connection.execute(sa.text(
            "SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'"
        ))
        return result.scalar() is not None
    except Exception:
        return False


def upgrade() -> None:
    bind = op.get_bind()
    if not _is_timescale_available(bind):
        # TimescaleDB not available (e.g., SQLite dev mode); skip silently.
        return

    # Convert sensor_readings to hypertable partitioned by timestamp
    # if_not_exists avoids errors if already converted
    bind.execute(sa.text(
        "SELECT create_hypertable('sensor_readings', 'timestamp', "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    ))

    # Add compression policy: compress chunks older than 7 days
    bind.execute(sa.text(
        "ALTER TABLE sensor_readings SET ("
        "timescaledb.compress, "
        "timescaledb.compress_segmentby = 'sensor_id, organization_id'"
        ")"
    ))
    bind.execute(sa.text(
        "SELECT add_compression_policy('sensor_readings', INTERVAL '7 days', "
        "if_not_exists => TRUE)"
    ))

    # Add retention policy: drop chunks older than 2 years (730 days)
    # This can be adjusted per deployment requirements
    bind.execute(sa.text(
        "SELECT add_retention_policy('sensor_readings', INTERVAL '730 days', "
        "if_not_exists => TRUE)"
    ))

    # Create continuous aggregate for hourly sensor reading averages
    # Useful for dashboarding and downsampling
    bind.execute(sa.text("""
        CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_readings_1h
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket('1 hour', timestamp) AS bucket,
            sensor_id,
            organization_id,
            avg(value) AS avg_value,
            min(value) AS min_value,
            max(value) AS max_value,
            count(*) AS sample_count
        FROM sensor_readings
        GROUP BY bucket, sensor_id, organization_id
        WITH NO DATA;
    """))
    bind.execute(sa.text(
        "SELECT add_continuous_aggregate_policy('sensor_readings_1h', "
        "start_offset => INTERVAL '3 hours', "
        "end_offset => INTERVAL '1 hour', "
        "schedule_interval => INTERVAL '1 hour', "
        "if_not_exists => TRUE)"
    ))

    # Create index on continuous aggregate for faster queries
    bind.execute(sa.text(
        "CREATE INDEX IF NOT EXISTS ix_sensor_readings_1h_bucket_sensor "
        "ON sensor_readings_1h (bucket, sensor_id)"
    ))


def downgrade() -> None:
    bind = op.get_bind()
    if not _is_timescale_available(bind):
        return

    # Remove continuous aggregate policy and view
    bind.execute(sa.text(
        "SELECT remove_continuous_aggregate_policy('sensor_readings_1h', if_exists => TRUE)"
    ))
    bind.execute(sa.text("DROP MATERIALIZED VIEW IF EXISTS sensor_readings_1h"))

    # Remove retention policy
    bind.execute(sa.text(
        "SELECT remove_retention_policy('sensor_readings', if_exists => TRUE)"
    ))

    # Remove compression policy
    bind.execute(sa.text(
        "SELECT remove_compression_policy('sensor_readings', if_exists => TRUE)"
    ))

    # Note: We don't revert hypertable conversion as it would require data migration.
    # In production, this migration should be considered one-way for the hypertable conversion.
