from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, desc, select

from .audit import append_audit
from .auth import SessionDependency, TenantContext, require_roles, tenant_context
from .config import get_settings
from .models import (
    ConnectorTag,
    ConnectorTagMapping,
    ConnectorValidationRule,
    DataSource,
    Plant,
    Sensor,
)
from .time_utils import as_utc

router = APIRouter(prefix="/api/v1/connectors", tags=["read-only-connectors"])
TenantDependency = Annotated[TenantContext, Depends(tenant_context)]
EngineerDependency = Annotated[
    TenantContext, Depends(require_roles("OWNER", "ADMIN", "ENGINEER"))
]


class ConnectorCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plant_id: UUID
    name: str = Field(min_length=1, max_length=200)
    source_type: Literal["MQTT", "OPCUA"]
    endpoint: str = Field(min_length=1, max_length=500)
    stale_after_s: int = Field(default=30, ge=1, le=86_400)
    sampling_interval_ms: int | None = Field(default=None, ge=100, le=60_000)

    @field_validator("endpoint")
    @classmethod
    def read_only_endpoint_scheme(cls, value: str, info: Any) -> str:
        value = value.strip()
        source_type = info.data.get("source_type")
        if source_type == "MQTT" and not value.startswith(("mqtt://", "mqtts://")):
            raise ValueError("MQTT endpoint must use mqtt:// or mqtts://")
        if source_type == "OPCUA" and not value.startswith(("opc.tcp://", "https://")):
            raise ValueError("OPC-UA endpoint must use opc.tcp:// or https://")
        if "@" in value:
            raise ValueError("Credentials must not be embedded in data-source endpoint URLs")
        if not value:
            raise ValueError("Data-source endpoint must include a hostname")
        return value


class ConnectorTagInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plant_tag: str = Field(min_length=1, max_length=128)
    canonical_name: str = Field(min_length=1, max_length=128)
    engineering_unit: str = Field(min_length=1, max_length=32)
    normalized_unit: str = Field(min_length=1, max_length=32)
    minimum_si: float | None = None
    maximum_si: float | None = None
    expected_sampling_interval_s: int | None = Field(default=None, ge=1, le=86_400)
    max_rate_of_change_per_s: float | None = None
    max_drift_per_hour: float | None = None

    @field_validator("plant_tag")
    @classmethod
    def explicit_plant_tag(cls, value: str) -> str:
        cleaned = value.strip()
        if any(character in cleaned for character in ("+", "#", "\x00", "\r", "\n")):
            raise ValueError("Plant tags must be explicit and may not contain wildcards or controls")
        return cleaned


class ConnectorTagMappingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_source_id: UUID
    connector_tag_id: UUID
    sensor_id: UUID
    source_key: str = Field(min_length=1, max_length=512)

    @field_validator("source_key")
    @classmethod
    def explicit_source_key(cls, value: str) -> str:
        cleaned = value.strip()
        if any(character in cleaned for character in ("+", "#", "\x00", "\r", "\n")):
            raise ValueError("Source keys must be explicit and may not contain wildcards or controls")
        return cleaned


class ConnectorValidationRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_tag_id: UUID
    rule_type: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)


def _raise(code: str, message: str, status: int = 422) -> None:
    raise HTTPException(status, detail={"code": code, "message": message})


def _connector_source_payload(source: DataSource, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    freshness = (
        max(0.0, (as_utc(current) - as_utc(source.last_success_at)).total_seconds())
        if source.last_success_at
        else None
    )
    status = source.status
    stale_after = float(source.configuration.get("stale_after_s", 30))
    if source.last_error:
        status = "ERROR"
    elif not source.last_success_at:
        status = source.status if source.status in {"CONNECTED", "DISCONNECTED", "ERROR"} else "DISCONNECTED"
    elif freshness is not None and freshness > stale_after:
        status = "STALE"
    return {
        "id": str(source.id),
        "plant_id": str(source.plant_id) if source.plant_id else None,
        "name": source.name,
        "source_type": source.source_type,
        "endpoint": source.endpoint,
        "read_only": source.read_only,
        "status": status,
        "last_success_at": as_utc(source.last_success_at) if source.last_success_at else None,
        "last_message_at": as_utc(source.last_message_at) if source.last_message_at else None,
        "message_rate_per_minute": source.message_rate_per_minute,
        "latency_ms": source.latency_ms,
        "error_count": source.error_count,
        "freshness_s": freshness,
        "last_error": source.last_error,
        "mappings": source.configuration.get("mapping_count", 0),
    }


@router.get("")
def list_connectors(context: TenantDependency, session: SessionDependency) -> dict[str, Any]:
    sources = list(
        session.scalars(
            select(DataSource)
            .where(DataSource.organization_id == context.organization_id)
            .order_by(desc(DataSource.updated_at))
        )
    )
    return {"items": [_connector_source_payload(source) for source in sources]}


@router.post("", status_code=201)
def create_connector_source(
    payload: ConnectorCreateRequest,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.is_production:
        _raise(
            "CONNECTOR_CONFIGURATION_PROD_RESTRICTED",
            "Connector configuration changes are restricted in production",
        )
    plant = session.scalar(
        select(Plant).where(
            and_(
                Plant.id == payload.plant_id,
                Plant.organization_id == context.organization_id,
            )
        )
    )
    if plant is None:
        _raise("PLANT_NOT_FOUND", "Plant was not found in this organization", 404)
    assert plant is not None
    config: dict[str, Any] = {
        "stale_after_s": payload.stale_after_s,
        "sampling_interval_ms": payload.sampling_interval_ms,
        "mapping_count": 0,
    }
    source = DataSource(
        organization_id=context.organization_id,
        plant_id=plant.id,
        name=payload.name.strip(),
        source_type=payload.source_type,
        endpoint=payload.endpoint,
        read_only=True,
        configuration=config,
        status="DISCONNECTED",
    )
    session.add(source)
    session.flush()
    append_audit(
        session,
        context.organization_id,
        "CONFIGURE_READ_ONLY_CONNECTOR",
        f"connector:{source.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={
            "source_type": source.source_type,
            "read_only": True,
        },
    )
    session.commit()
    return _connector_source_payload(source)


@router.get("/{source_id}")
def get_connector_source(
    source_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    source = session.scalar(
        select(DataSource).where(
            and_(
                DataSource.id == source_id,
                DataSource.organization_id == context.organization_id,
            )
        )
    )
    if source is None:
        _raise("CONNECTOR_NOT_FOUND", "Connector source was not found", 404)
    assert source is not None
    return _connector_source_payload(source)


@router.get("/{source_id}/health")
def connector_health(
    source_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    source = session.scalar(
        select(DataSource).where(
            and_(
                DataSource.id == source_id,
                DataSource.organization_id == context.organization_id,
            )
        )
    )
    if source is None:
        _raise("CONNECTOR_NOT_FOUND", "Connector source was not found", 404)
    assert source is not None
    freshness = (
        max(0.0, (as_utc(datetime.now(UTC)) - as_utc(source.last_success_at)).total_seconds())
        if source.last_success_at
        else None
    )
    status = source.status
    stale_after = float(source.configuration.get("stale_after_s", 30))
    if source.last_error:
        status = "ERROR"
    elif not source.last_success_at:
        status = source.status if source.status in {"CONNECTED", "DISCONNECTED", "ERROR"} else "DISCONNECTED"
    elif freshness is not None and freshness > stale_after:
        status = "STALE"
    return {
        "id": str(source.id),
        "status": status,
        "read_only": source.read_only,
        "last_success_at": as_utc(source.last_success_at) if source.last_success_at else None,
        "last_message_at": as_utc(source.last_message_at) if source.last_message_at else None,
        "message_rate_per_minute": source.message_rate_per_minute,
        "latency_ms": source.latency_ms,
        "error_count": source.error_count,
        "freshness_s": freshness,
        "last_error": source.last_error,
    }


@router.post("/tags", status_code=201)
def create_connector_tag(
    payload: ConnectorTagInput,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.is_production:
        _raise(
            "CONNECTOR_CONFIGURATION_PROD_RESTRICTED",
            "Connector configuration changes are restricted in production",
        )
    plant = session.scalar(
        select(Plant).where(
            and_(
                Plant.id == payload.plant_id,
                Plant.organization_id == context.organization_id,
            )
        )
    )
    if plant is None:
        _raise("PLANT_NOT_FOUND", "Plant was not found in this organization", 404)
    assert plant is not None
    existing = session.scalar(
        select(ConnectorTag).where(
            and_(
                ConnectorTag.organization_id == context.organization_id,
                ConnectorTag.plant_tag == payload.plant_tag,
            )
        )
    )
    if existing is not None:
        _raise(
            "CONNECTOR_TAG_EXISTS",
            "A connector tag with this plant tag already exists in this organization",
        )
    tag = ConnectorTag(
        organization_id=context.organization_id,
        plant_id=plant.id,
        tag=payload.plant_tag.strip(),
        canonical_name=payload.canonical_name.strip(),
        engineering_unit=payload.engineering_unit.strip(),
        normalized_unit=payload.normalized_unit.strip(),
        minimum_si=payload.minimum_si,
        maximum_si=payload.maximum_si,
        expected_sampling_interval_s=payload.expected_sampling_interval_s,
        max_rate_of_change_per_s=payload.max_rate_of_change_per_s,
        max_drift_per_hour=payload.max_drift_per_hour,
    )
    session.add(tag)
    session.flush()
    append_audit(
        session,
        context.organization_id,
        "CONFIGURE_CONNECTOR_TAG",
        f"connector_tag:{tag.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={
            "plant_tag": tag.tag,
            "canonical_name": tag.canonical_name,
            "read_only": True,
        },
    )
    session.commit()
    return {
        "id": str(tag.id),
        "plant_id": str(tag.plant_id),
        "tag": tag.tag,
        "canonical_name": tag.canonical_name,
        "engineering_unit": tag.engineering_unit,
        "normalized_unit": tag.normalized_unit,
        "minimum_si": tag.minimum_si,
        "maximum_si": tag.maximum_si,
        "expected_sampling_interval_s": tag.expected_sampling_interval_s,
        "max_rate_of_change_per_s": tag.max_rate_of_change_per_s,
        "max_drift_per_hour": tag.max_drift_per_hour,
    }


@router.post("/tag-mappings", status_code=201)
def create_connector_tag_mapping(
    payload: ConnectorTagMappingInput,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.is_production:
        _raise(
            "CONNECTOR_CONFIGURATION_PROD_RESTRICTED",
            "Connector configuration changes are restricted in production",
        )
    source = session.scalar(
        select(DataSource).where(
            and_(
                DataSource.id == payload.data_source_id,
                DataSource.organization_id == context.organization_id,
                DataSource.read_only.is_(True),
            )
        )
    )
    if source is None:
        _raise("CONNECTOR_NOT_FOUND", "Read-only connector source was not found", 404)
    assert source is not None
    sensor = session.scalar(
        select(Sensor).where(
            and_(
                Sensor.id == payload.sensor_id,
                Sensor.organization_id == context.organization_id,
                Sensor.enabled.is_(True),
            )
        )
    )
    if sensor is None:
        _raise("SENSOR_NOT_FOUND", "Enabled sensor was not found in this organization", 404)
    assert sensor is not None
    tag = session.scalar(
        select(ConnectorTag).where(
            and_(
                ConnectorTag.id == payload.connector_tag_id,
                ConnectorTag.organization_id == context.organization_id,
            )
        )
    )
    if tag is None:
        _raise("CONNECTOR_TAG_NOT_FOUND", "Connector tag was not found in this organization", 404)
    assert tag is not None
    existing = session.scalar(
        select(ConnectorTagMapping).where(
            and_(
                ConnectorTagMapping.data_source_id == source.id,
                ConnectorTagMapping.connector_tag_id == tag.id,
            )
        )
    )
    if existing is not None:
        _raise(
            "CONNECTOR_TAG_MAPPING_EXISTS",
            "This connector tag is already mapped to this data source",
        )
    mapping = ConnectorTagMapping(
        organization_id=context.organization_id,
        data_source_id=source.id,
        connector_tag_id=tag.id,
        sensor_id=sensor.id,
        source_key=payload.source_key.strip(),
    )
    session.add(mapping)
    source.configuration["mapping_count"] = (source.configuration.get("mapping_count") or 0) + 1
    session.flush()
    append_audit(
        session,
        context.organization_id,
        "CONFIGURE_CONNECTOR_TAG_MAPPING",
        f"connector_tag_mapping:{mapping.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={
            "data_source_id": str(source.id),
            "connector_tag_id": str(tag.id),
            "sensor_id": str(sensor.id),
            "source_key": mapping.source_key,
            "read_only": True,
        },
    )
    session.commit()
    return {
        "id": str(mapping.id),
        "data_source_id": str(mapping.data_source_id),
        "connector_tag_id": str(mapping.connector_tag_id),
        "sensor_id": str(mapping.sensor_id),
        "source_key": mapping.source_key,
    }


@router.post("/validation-rules", status_code=201)
def create_connector_validation_rule(
    payload: ConnectorValidationRuleInput,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.is_production:
        _raise(
            "CONNECTOR_CONFIGURATION_PROD_RESTRICTED",
            "Connector configuration changes are restricted in production",
        )
    tag = session.scalar(
        select(ConnectorTag).where(
            and_(
                ConnectorTag.id == payload.connector_tag_id,
                ConnectorTag.organization_id == context.organization_id,
            )
        )
    )
    if tag is None:
        _raise("CONNECTOR_TAG_NOT_FOUND", "Connector tag was not found in this organization", 404)
    assert tag is not None
    existing = session.scalar(
        select(ConnectorValidationRule).where(
            and_(
                ConnectorValidationRule.organization_id == context.organization_id,
                ConnectorValidationRule.connector_tag_id == tag.id,
                ConnectorValidationRule.rule_type == payload.rule_type,
            )
        )
    )
    if existing is not None:
        _raise(
            "CONNECTOR_VALIDATION_RULE_EXISTS",
            "A validation rule of this type already exists for this connector tag",
        )
    rule = ConnectorValidationRule(
        organization_id=context.organization_id,
        connector_tag_id=tag.id,
        rule_type=payload.rule_type.strip(),
        parameters=payload.parameters,
    )
    session.add(rule)
    session.flush()
    append_audit(
        session,
        context.organization_id,
        "CONFIGURE_CONNECTOR_VALIDATION_RULE",
        f"connector_validation_rule:{rule.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={
            "connector_tag_id": str(tag.id),
            "rule_type": rule.rule_type,
            "read_only": True,
        },
    )
    session.commit()
    return {
        "id": str(rule.id),
        "connector_tag_id": str(rule.connector_tag_id),
        "rule_type": rule.rule_type,
        "parameters": rule.parameters,
    }
