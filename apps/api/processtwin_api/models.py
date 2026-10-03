"""Tenant-scoped relational schema. Sensor readings become a Timescale hypertable in production."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class RoleName(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    ENGINEER = "ENGINEER"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class QualityStatusName(StrEnum):
    GOOD = "GOOD"
    SUSPECT = "SUSPECT"
    BAD = "BAD"
    MISSING = "MISSING"


class AlertSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class Timestamped:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Organization(Timestamped, Base):
    __tablename__ = "organizations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class User(Timestamped, Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    memberships: Mapped[list[OrganizationMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RefreshToken(Base):
    """Server-side refresh-token state enables rotation and incident revocation."""

    __tablename__ = "refresh_tokens"
    __table_args__ = (Index("ix_refresh_tokens_user_id", "user_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL")
    )


class OrganizationMembership(Timestamped, Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
        Index("ix_membership_organization_id", "organization_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[RoleName] = mapped_column(Enum(RoleName), nullable=False)
    organization: Mapped[Organization] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="memberships")


class Role(Timestamped, Base):
    """Role catalog is persisted for integrations; membership authorization uses RoleName."""

    __tablename__ = "roles"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[RoleName] = mapped_column(Enum(RoleName), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class Site(Timestamped, Base):
    __tablename__ = "sites"
    __table_args__ = (Index("ix_sites_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))


class Plant(Timestamped, Base):
    __tablename__ = "plants"
    __table_args__ = (Index("ix_plants_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[UUID | None] = mapped_column(ForeignKey("sites.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))
    equipment: Mapped[list[Equipment]] = relationship(
        back_populates="plant", cascade="all, delete-orphan"
    )
    process_units: Mapped[list[ProcessUnit]] = relationship(cascade="all, delete-orphan")


class ProcessUnit(Timestamped, Base):
    __tablename__ = "process_units"
    __table_args__ = (
        Index("ix_process_units_organization_id", "organization_id"),
        Index("ix_process_units_plant_id", "plant_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    plant_id: Mapped[UUID] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_type: Mapped[str] = mapped_column(String(80), nullable=False)


class Equipment(Timestamped, Base):
    __tablename__ = "equipment"
    __table_args__ = (
        Index("ix_equipment_organization_id", "organization_id"),
        Index("ix_equipment_plant_id", "plant_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    plant_id: Mapped[UUID] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), nullable=False
    )
    process_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("process_units.id", ondelete="SET NULL")
    )
    tag: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    equipment_type: Mapped[str] = mapped_column(String(80), nullable=False)
    plant: Mapped[Plant] = relationship(back_populates="equipment")
    sensors: Mapped[list[Sensor]] = relationship(
        back_populates="equipment", cascade="all, delete-orphan"
    )


class Sensor(Timestamped, Base):
    __tablename__ = "sensors"
    __table_args__ = (
        Index("ix_sensors_organization_id", "organization_id"),
        UniqueConstraint("organization_id", "tag", name="uq_sensor_org_tag"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    equipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    tag: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    measurement_type: Mapped[str] = mapped_column(String(80), nullable=False)
    sampling_interval_s: Mapped[int] = mapped_column(Integer, default=60, nullable=False)
    min_valid_value: Mapped[float | None] = mapped_column(Float)
    max_valid_value: Mapped[float | None] = mapped_column(Float)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    equipment: Mapped[Equipment] = relationship(back_populates="sensors")


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    __table_args__ = (
        Index("ix_readings_org_timestamp", "organization_id", "timestamp"),
        Index("ix_readings_sensor_timestamp", "sensor_id", "timestamp"),
        UniqueConstraint("sensor_id", "timestamp", name="uq_sensor_timestamp"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    sensor_id: Mapped[UUID] = mapped_column(
        ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    original_value: Mapped[float | None] = mapped_column(Float)
    original_unit: Mapped[str | None] = mapped_column(String(32))
    normalized_value: Mapped[float | None] = mapped_column(Float)
    normalized_unit: Mapped[str | None] = mapped_column(String(32))
    quality_status: Mapped[QualityStatusName] = mapped_column(
        Enum(QualityStatusName), nullable=False
    )
    quality_reasons: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="SIMULATED", nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class DataSource(Timestamped, Base):
    __tablename__ = "data_sources"
    __table_args__ = (Index("ix_data_sources_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    plant_id: Mapped[UUID | None] = mapped_column(ForeignKey("plants.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(500))
    read_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="DISCONNECTED", nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_rate_per_minute: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    error_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    freshness_s: Mapped[float | None] = mapped_column(Float)
    last_error: Mapped[str | None] = mapped_column(Text)


class SourceTagMapping(Timestamped, Base):
    __tablename__ = "source_tag_mappings"
    __table_args__ = (
        UniqueConstraint("data_source_id", "source_key", name="uq_source_tag_key"),
        Index("ix_source_tag_mappings_organization_id", "organization_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[UUID] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    sensor_id: Mapped[UUID] = mapped_column(
        ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False
    )
    plant_tag_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("plant_tags.id", ondelete="SET NULL")
    )
    source_key: Mapped[str] = mapped_column(String(512), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(128), nullable=False)
    source_unit: Mapped[str] = mapped_column(String(32), nullable=False)


class Dataset(Timestamped, Base):
    __tablename__ = "datasets"
    __table_args__ = (Index("ix_datasets_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    plant_id: Mapped[UUID] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class DatasetVersion(Timestamped, Base):
    __tablename__ = "dataset_versions"
    __table_args__ = (UniqueConstraint("dataset_id", "version", name="uq_dataset_version"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="IMPORTED", nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    measurement_count: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class PlantTag(Timestamped, Base):
    __tablename__ = "plant_tags"
    __table_args__ = (
        UniqueConstraint("organization_id", "plant_id", "tag", name="uq_plant_tag"),
        Index("ix_plant_tags_organization_id", "organization_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    plant_id: Mapped[UUID] = mapped_column(
        ForeignKey("plants.id", ondelete="CASCADE"), nullable=False
    )
    process_unit_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("process_units.id", ondelete="SET NULL")
    )
    equipment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("equipment.id", ondelete="SET NULL")
    )
    tag: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(128), nullable=False)
    engineering_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    minimum_si: Mapped[float | None] = mapped_column(Float)
    maximum_si: Mapped[float | None] = mapped_column(Float)
    expected_sampling_interval_s: Mapped[int | None] = mapped_column(Integer)
    max_rate_of_change_per_s: Mapped[float | None] = mapped_column(Float)
    max_drift_per_hour: Mapped[float | None] = mapped_column(Float)


class TagMapping(Timestamped, Base):
    __tablename__ = "tag_mappings"
    __table_args__ = (
        UniqueConstraint("dataset_id", "source_tag", name="uq_dataset_source_tag"),
        Index("ix_tag_mappings_organization_id", "organization_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    plant_tag_id: Mapped[UUID] = mapped_column(
        ForeignKey("plant_tags.id", ondelete="CASCADE"), nullable=False
    )
    source_tag: Mapped[str] = mapped_column(String(128), nullable=False)
    source_unit: Mapped[str] = mapped_column(String(32), nullable=False)


class DatasetObservation(Base):
    __tablename__ = "dataset_observations"
    __table_args__ = (
        Index(
            "ix_dataset_observations_version_tag_time",
            "dataset_version_id",
            "tag_mapping_id",
            "timestamp",
        ),
        Index("ix_dataset_observations_organization_id", "organization_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="CASCADE"), nullable=False
    )
    tag_mapping_id: Mapped[UUID] = mapped_column(
        ForeignKey("tag_mappings.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    original_timestamp: Mapped[str] = mapped_column(String(128), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    original_value: Mapped[float | None] = mapped_column(Float)
    original_text: Mapped[str | None] = mapped_column(Text)
    original_unit: Mapped[str | None] = mapped_column(String(32))
    normalized_value: Mapped[float | None] = mapped_column(Float)
    normalized_unit: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_status: Mapped[QualityStatusName] = mapped_column(
        Enum(QualityStatusName), nullable=False
    )
    quality_reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class PhysicsParameterSet(Timestamped, Base):
    __tablename__ = "physics_parameter_sets"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "name", "version", name="uq_physics_parameter_set_version"
        ),
        Index("ix_parameter_sets_organization_id", "organization_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="INITIAL", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class CalibrationRun(Timestamped, Base):
    __tablename__ = "calibration_runs"
    __table_args__ = (Index("ix_calibrations_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"), nullable=False
    )
    parameter_set_id: Mapped[UUID] = mapped_column(
        ForeignKey("physics_parameter_sets.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL")
    )
    method: Mapped[str] = mapped_column(String(40), nullable=False)
    objective_name: Mapped[str] = mapped_column(String(80), nullable=False)
    initial_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    calibrated_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    bounds: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    code_version: Mapped[str] = mapped_column(String(80), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)


class ModelEvaluation(Timestamped, Base):
    __tablename__ = "model_evaluations"
    __table_args__ = (Index("ix_model_evaluations_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False
    )
    dataset_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    evaluation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    residual_distribution: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    comparisons: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class ModelDriftEvent(Base):
    __tablename__ = "model_drift_events"
    __table_args__ = (Index("ix_model_drift_events_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False
    )
    dataset_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class QualityEvent(Base):
    __tablename__ = "quality_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    sensor_id: Mapped[UUID] = mapped_column(
        ForeignKey("sensors.id", ondelete="CASCADE"), nullable=False
    )
    reading_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class TwinState(Timestamped, Base):
    __tablename__ = "twin_states"
    __table_args__ = (Index("ix_twin_states_org_timestamp", "organization_id", "timestamp"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    equipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    health_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_mode: Mapped[str] = mapped_column(String(24), default="SIMULATION", nullable=False)
    source_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL")
    )
    physics_parameter_set_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("physics_parameter_sets.id", ondelete="SET NULL")
    )
    data_quality: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    prediction_status: Mapped[str] = mapped_column(String(40), default="PREDICTED", nullable=False)
    uncertainty: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    health_factors: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ModelVersion(Timestamped, Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        Index("ix_models_organization_id", "organization_id"),
        UniqueConstraint("organization_id", "name", "version", name="uq_model_version"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="DEVELOPMENT", nullable=False)
    feature_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    target_schema: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    dataset_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("dataset_versions.id", ondelete="SET NULL")
    )
    physics_parameter_set_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("physics_parameter_sets.id", ondelete="SET NULL")
    )
    training_period: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validation_period: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    test_period: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    hyperparameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    operating_envelope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    git_sha: Mapped[str | None] = mapped_column(String(80))
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    artifact_path: Mapped[str | None] = mapped_column(String(500))


class TrainingRun(Timestamped, Base):
    __tablename__ = "training_runs"
    __table_args__ = (Index("ix_training_runs_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL")
    )
    dataset_description: Mapped[str] = mapped_column(Text, nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class Simulation(Timestamped, Base):
    __tablename__ = "simulations"
    __table_args__ = (Index("ix_simulations_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    equipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    results: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL")
    )
    physics_parameter_set_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("physics_parameter_sets.id", ondelete="SET NULL")
    )
    operating_envelope: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)


class OptimizationRun(Timestamped, Base):
    __tablename__ = "optimization_runs"
    __table_args__ = (Index("ix_optimization_runs_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    equipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    objective: Mapped[str] = mapped_column(String(80), nullable=False)
    baseline: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL")
    )
    physics_parameter_set_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("physics_parameter_sets.id", ondelete="SET NULL")
    )
    algorithm: Mapped[str] = mapped_column(
        String(80), default="differential_evolution", nullable=False
    )
    bounds: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    constraints: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    uncertainty: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class Recommendation(Timestamped, Base):
    __tablename__ = "recommendations"
    __table_args__ = (Index("ix_recommendations_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    equipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    expected_impact: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="GENERATED", nullable=False)
    simulation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("simulations.id", ondelete="SET NULL")
    )
    optimization_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("optimization_runs.id", ondelete="SET NULL")
    )
    model_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="SET NULL")
    )
    baseline: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    proposed_change: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    energy_impact: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    uncertainty: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    constraint_status: Mapped[str] = mapped_column(String(32), default="UNKNOWN", nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecommendationReview(Base):
    __tablename__ = "recommendation_reviews"
    __table_args__ = (
        Index("ix_recommendation_reviews_org_timestamp", "organization_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    recommendation_id: Mapped[UUID] = mapped_column(
        ForeignKey("recommendations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class PotentialAnomaly(Base):
    __tablename__ = "potential_anomalies"
    __table_args__ = (
        Index("ix_potential_anomalies_org_timestamp", "organization_id", "timestamp"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    equipment_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipment.id", ondelete="CASCADE"), nullable=False
    )
    twin_state_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("twin_states.id", ondelete="SET NULL")
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    label: Mapped[str] = mapped_column(String(40), default="POTENTIAL ANOMALY", nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    potential_contributing_variables: Mapped[list[str]] = mapped_column(JSON, nullable=False)


class Alert(Timestamped, Base):
    __tablename__ = "alerts"
    __table_args__ = (Index("ix_alerts_organization_id", "organization_id"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    equipment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("equipment.id", ondelete="SET NULL")
    )
    severity: Mapped[AlertSeverity] = mapped_column(Enum(AlertSeverity), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="OPEN", nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_org_timestamp", "organization_id", "timestamp"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource: Mapped[str] = mapped_column(String(200), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    ip_address: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)


class ImportJob(Timestamped, Base):
    """Tracks chunked historical data import jobs for large datasets."""
    __tablename__ = "import_jobs"
    __table_args__ = (
        Index("ix_import_jobs_organization_id", "organization_id"),
        Index("ix_import_jobs_dataset_id", "dataset_id"),
        Index("ix_import_jobs_status", "status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    dataset_id: Mapped[UUID] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False
    )
    data_source_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    source_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    total_bytes: Mapped[int | None] = mapped_column(BigInteger)
    total_rows: Mapped[int | None] = mapped_column(Integer)
    processed_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, default=10000, nullable=False)
    total_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completed_chunks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    mappings_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    timestamp_column: Mapped[str] = mapped_column(String(128), default="timestamp", nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportChunk(Timestamped, Base):
    """Individual chunk within an import job for idempotent retry."""
    __tablename__ = "import_chunks"
    __table_args__ = (
        UniqueConstraint("import_job_id", "chunk_index", name="uq_import_chunk_job_index"),
        Index("ix_import_chunks_job_id", "import_job_id"),
        Index("ix_import_chunks_status", "status"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    import_job_id: Mapped[UUID] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    start_row: Mapped[int] = mapped_column(Integer, nullable=False)
    end_row: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False)
    object_path: Mapped[str | None] = mapped_column(String(512))
    object_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    object_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    rows_processed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows_failed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    quality_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ObjectStorageArtifact(Timestamped, Base):
    """Tracks files stored in object storage (MinIO/S3) for audit and deduplication."""
    __tablename__ = "object_storage_artifacts"
    __table_args__ = (
        UniqueConstraint("bucket", "object_key", name="uq_object_bucket_key"),
        Index("ix_object_artifacts_organization_id", "organization_id"),
        Index("ix_object_artifacts_checksum", "checksum_sha256"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    etag: Mapped[str | None] = mapped_column(String(128))
    version_id: Mapped[str | None] = mapped_column(String(128))
    artifact_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    uploaded_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
