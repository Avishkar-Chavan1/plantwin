from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, select

from .audit import append_audit
from .auth import SessionDependency, TenantContext, require_roles, tenant_context
from .models import (
    DataSource,
    Equipment,
    Plant,
    Sensor,
    SourceTagMapping,
)
from .time_utils import as_utc

router = APIRouter(prefix="/api/v1/data-sources", tags=["live-read-only-ingestion"])
TenantDependency = Annotated[TenantContext, Depends(tenant_context)]
EngineerDependency = Annotated[
    TenantContext, Depends(require_roles("OWNER", "ADMIN", "ENGINEER"))
]


class SourceMappingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_key: str = Field(min_length=1, max_length=512)
    sensor_id: UUID
    canonical_name: str = Field(min_length=1, max_length=128)
    source_unit: str = Field(min_length=1, max_length=32)


class CreateSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plant_id: UUID
    name: str = Field(min_length=1, max_length=200)
    source_type: Literal["MQTT", "OPCUA"]
    endpoint: str = Field(min_length=1, max_length=500)
    mappings: list[SourceMappingInput] = Field(min_length=1, max_length=200)
    stale_after_s: int = Field(default=30, ge=1, le=86_400)
    mqtt_port: int = Field(default=1883, ge=1, le=65_535)
    mqtt_client_id: str = Field(default="processtwin-readonly", min_length=1, max_length=100)
    opcua_sampling_interval_ms: int = Field(default=1000, ge=100, le=60_000)

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
        parsed = urlsplit(value)
        if parsed.query or parsed.fragment:
            raise ValueError("Credential and secret query parameters are not accepted in endpoints")
        if not parsed.hostname:
            raise ValueError("Data-source endpoint must include a hostname")
        return value


def _raise(code: str, message: str, status: int = 422) -> None:
    raise HTTPException(status, detail={"code": code, "message": message})


def _source_payload(source: DataSource, now: datetime | None = None) -> dict[str, Any]:
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
def list_sources(context: TenantDependency, session: SessionDependency) -> dict[str, Any]:
    sources = list(
        session.scalars(
            select(DataSource)
            .where(DataSource.organization_id == context.organization_id)
            .order_by(desc(DataSource.updated_at))
        )
    )
    return {"items": [_source_payload(source) for source in sources]}


@router.post("", status_code=201)
def create_source(
    payload: CreateSourceRequest,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    plant = session.scalar(
        select(Plant).where(
            Plant.id == payload.plant_id,
            Plant.organization_id == context.organization_id,
        )
    )
    if plant is None:
        _raise("PLANT_NOT_FOUND", "Plant was not found in this organization", 404)
    if len({item.source_key for item in payload.mappings}) != len(payload.mappings):
        _raise("DUPLICATE_SOURCE_KEY", "Each connector source key must be mapped once")
    for item in payload.mappings:
        sensor = session.scalar(
            select(Sensor)
            .join(Equipment, Equipment.id == Sensor.equipment_id)
            .where(
                Sensor.id == item.sensor_id,
                Sensor.organization_id == context.organization_id,
                Sensor.enabled.is_(True),
                Equipment.organization_id == context.organization_id,
                Equipment.plant_id == plant.id,
            )
        )
        if sensor is None:
            _raise("SENSOR_NOT_FOUND", f"Enabled sensor {item.sensor_id} was not found in this plant", 404)
        try:
            from packages.units import convert

            convert(1.0, item.source_unit, sensor.unit)
        except ValueError:
            _raise("INVALID_SOURCE_UNIT", f"Source unit for {item.source_key} is incompatible with sensor unit {sensor.unit}")
    config: dict[str, Any] = {
        "stale_after_s": payload.stale_after_s,
        "mqtt_port": payload.mqtt_port,
        "mqtt_client_id": payload.mqtt_client_id,
        "opcua_sampling_interval_ms": payload.opcua_sampling_interval_ms,
        "mapping_count": len(payload.mappings),
        "subscription_keys": [item.source_key for item in payload.mappings],
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
    for item in payload.mappings:
        session.add(
            SourceTagMapping(
                organization_id=context.organization_id,
                data_source_id=source.id,
                sensor_id=item.sensor_id,
                source_key=item.source_key,
                canonical_name=item.canonical_name,
                source_unit=item.source_unit,
            )
        )
    append_audit(
        session,
        context.organization_id,
        "CONFIGURE_READ_ONLY_DATA_SOURCE",
        f"data_source:{source.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={"source_type": source.source_type, "mapping_count": len(payload.mappings), "read_only": True},
    )
    session.commit()
    return _source_payload(source)


@router.get("/{source_id}")
def get_source(
    source_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    source = session.scalar(
        select(DataSource).where(
            DataSource.id == source_id,
            DataSource.organization_id == context.organization_id,
        )
    )
    if source is None:
        _raise("DATA_SOURCE_NOT_FOUND", "Data source was not found", 404)
    return _source_payload(source)


@router.get("/{source_id}/readings")
def source_readings(
    source_id: UUID, context: TenantDependency, session: SessionDependency, limit: int = 200
) -> dict[str, Any]:
    source = session.scalar(
        select(DataSource).where(
            DataSource.id == source_id,
            DataSource.organization_id == context.organization_id,
        )
    )
    if source is None:
        _raise("DATA_SOURCE_NOT_FOUND", "Data source was not found", 404)
    mappings = list(
        session.scalars(
            select(SourceTagMapping).where(
                SourceTagMapping.data_source_id == source.id,
                SourceTagMapping.organization_id == context.organization_id,
            )
        )
    )
    mapping_by_id = {str(mapping.sensor_id): mapping for mapping in mappings}
    from .models import SensorReading

    rows = list(
        session.scalars(
            select(SensorReading)
            .where(
                SensorReading.organization_id == context.organization_id,
                SensorReading.data_source_id == source.id,
            )
            .order_by(desc(SensorReading.timestamp))
            .limit(min(max(limit, 1), 1000))
        )
    )
    return {
        "source": _source_payload(source),
        "items": [
            {
                "sensor_id": str(row.sensor_id),
                "canonical_name": mapping_by_id[str(row.sensor_id)].canonical_name,
                "timestamp": row.timestamp,
                "value": row.value,
                "unit": row.unit,
                "normalized_value": row.normalized_value,
                "normalized_unit": row.normalized_unit,
                "quality_status": row.quality_status.value,
                "quality_reasons": row.quality_reasons,
            }
            for row in rows
            if str(row.sensor_id) in mapping_by_id
        ],
    }
