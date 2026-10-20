"""Read-only connector configuration API.

Every endpoint here configures *acquisition* of measurements. Nothing in this module can
write to, command, or set values on a plant system; connector sources are always created with
``read_only=True`` and no route exposes a write, setpoint or control operation.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, NoReturn
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from .audit import append_audit
from .auth import SessionDependency, TenantContext, require_roles, tenant_context
from .live_api import enforce_endpoint_policy, list_source_payloads, source_payload
from .models import (
    ConnectorTag,
    ConnectorTagMapping,
    ConnectorValidationRule,
    DataSource,
    Plant,
    Sensor,
    SourceTagMapping,
)

router = APIRouter(prefix="/api/v1/connectors", tags=["read-only-connectors"])
TenantDependency = Annotated[TenantContext, Depends(tenant_context)]
EngineerDependency = Annotated[
    TenantContext, Depends(require_roles("OWNER", "ADMIN", "ENGINEER"))
]

#: Validation rule types the ingestion path understands. Anything else is rejected so a
#: typo cannot silently disable a data-quality check.
VALIDATION_RULE_TYPES = frozenset(
    {"RANGE", "RATE_OF_CHANGE", "DRIFT", "STALE_AFTER", "FLATLINE", "REQUIRED"}
)


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
        parsed = urlsplit(value)
        if parsed.query or parsed.fragment:
            raise ValueError("Credential and secret query parameters are not accepted in endpoints")
        if not parsed.hostname:
            raise ValueError("Data-source endpoint must include a hostname")
        return value


class ConnectorTagInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plant_id: UUID
    plant_tag: str = Field(min_length=1, max_length=128)
    canonical_name: str = Field(min_length=1, max_length=128)
    engineering_unit: str = Field(min_length=1, max_length=32)
    normalized_unit: str = Field(min_length=1, max_length=32)
    minimum_si: float | None = Field(default=None, allow_inf_nan=False)
    maximum_si: float | None = Field(default=None, allow_inf_nan=False)
    expected_sampling_interval_s: int | None = Field(default=None, ge=1, le=86_400)
    max_rate_of_change_per_s: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    max_drift_per_hour: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("plant_tag")
    @classmethod
    def explicit_plant_tag(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or any(c in cleaned for c in ("+", "#", "\x00", "\r", "\n")):
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
        if not cleaned or any(c in cleaned for c in ("+", "#", "\x00", "\r", "\n")):
            raise ValueError("Source keys must be explicit and may not contain wildcards or controls")
        return cleaned


class ConnectorValidationRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_tag_id: UUID
    rule_type: str = Field(min_length=1, max_length=64)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("rule_type")
    @classmethod
    def known_rule_type(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if cleaned not in VALIDATION_RULE_TYPES:
            raise ValueError(f"rule_type must be one of {', '.join(sorted(VALIDATION_RULE_TYPES))}")
        return cleaned


def _raise(code: str, message: str, status: int = 422) -> NoReturn:
    raise HTTPException(status, detail={"code": code, "message": message})


def _tag_payload(tag: ConnectorTag) -> dict[str, Any]:
    return {
        "id": str(tag.id),
        "plant_id": str(tag.plant_id) if tag.plant_id else None,
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


def _tenant_source(session: Session, source_id: UUID, organization_id: UUID) -> DataSource:
    source = session.scalar(
        select(DataSource).where(
            and_(DataSource.id == source_id, DataSource.organization_id == organization_id)
        )
    )
    if source is None:
        _raise("CONNECTOR_NOT_FOUND", "Connector source was not found", 404)
    return source


# NOTE: static paths (/tags, /tag-mappings, /validation-rules) are declared before the
# parameterised "/{source_id}" routes so they are never parsed as a UUID.


@router.get("")
def list_connectors(context: TenantDependency, session: SessionDependency) -> dict[str, Any]:
    return {"items": list_source_payloads(session, context.organization_id)}


@router.post("", status_code=201)
def create_connector_source(
    payload: ConnectorCreateRequest,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    enforce_endpoint_policy(payload.source_type, payload.endpoint)
    plant = session.scalar(
        select(Plant).where(
            and_(Plant.id == payload.plant_id, Plant.organization_id == context.organization_id)
        )
    )
    if plant is None:
        _raise("PLANT_NOT_FOUND", "Plant was not found in this organization", 404)
    source = DataSource(
        organization_id=context.organization_id,
        plant_id=plant.id,
        name=payload.name.strip(),
        source_type=payload.source_type,
        endpoint=payload.endpoint,
        read_only=True,
        configuration={
            "stale_after_s": payload.stale_after_s,
            "opcua_sampling_interval_ms": payload.sampling_interval_ms or 1000,
            "mapping_count": 0,
        },
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
        metadata={"source_type": source.source_type, "read_only": True},
    )
    session.commit()
    return source_payload(source)


@router.get("/tags")
def list_connector_tags(
    context: TenantDependency,
    session: SessionDependency,
    plant_id: UUID | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    query = select(ConnectorTag).where(ConnectorTag.organization_id == context.organization_id)
    if plant_id is not None:
        query = query.where(ConnectorTag.plant_id == plant_id)
    tags = list(session.scalars(query.order_by(ConnectorTag.tag).limit(min(max(limit, 1), 500))))
    return {"items": [_tag_payload(tag) for tag in tags]}


@router.post("/tags", status_code=201)
def create_connector_tag(
    payload: ConnectorTagInput,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    plant = session.scalar(
        select(Plant).where(
            and_(Plant.id == payload.plant_id, Plant.organization_id == context.organization_id)
        )
    )
    if plant is None:
        _raise("PLANT_NOT_FOUND", "Plant was not found in this organization", 404)
    if (
        payload.minimum_si is not None
        and payload.maximum_si is not None
        and payload.minimum_si >= payload.maximum_si
    ):
        _raise("INVALID_TAG_RANGE", "minimum_si must be lower than maximum_si")
    try:
        from packages.units import convert

        convert(1.0, payload.engineering_unit, payload.normalized_unit)
    except ValueError:
        _raise(
            "INVALID_TAG_UNITS",
            f"Engineering unit {payload.engineering_unit} cannot be converted to "
            f"{payload.normalized_unit}",
        )
    existing = session.scalar(
        select(ConnectorTag.id).where(
            and_(
                ConnectorTag.organization_id == context.organization_id,
                ConnectorTag.tag == payload.plant_tag,
            )
        )
    )
    if existing is not None:
        _raise(
            "CONNECTOR_TAG_EXISTS",
            "A connector tag with this plant tag already exists in this organization",
            409,
        )
    tag = ConnectorTag(
        organization_id=context.organization_id,
        plant_id=plant.id,
        tag=payload.plant_tag,
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
        metadata={"plant_tag": tag.tag, "canonical_name": tag.canonical_name, "read_only": True},
    )
    session.commit()
    return _tag_payload(tag)


@router.get("/tag-mappings")
def list_connector_tag_mappings(
    context: TenantDependency, session: SessionDependency, data_source_id: UUID | None = None
) -> dict[str, Any]:
    query = select(ConnectorTagMapping).where(
        ConnectorTagMapping.organization_id == context.organization_id
    )
    if data_source_id is not None:
        query = query.where(ConnectorTagMapping.data_source_id == data_source_id)
    rows = list(session.scalars(query.order_by(ConnectorTagMapping.source_key).limit(500)))
    return {
        "items": [
            {
                "id": str(row.id),
                "data_source_id": str(row.data_source_id),
                "connector_tag_id": str(row.connector_tag_id),
                "sensor_id": str(row.sensor_id),
                "source_key": row.source_key,
            }
            for row in rows
        ]
    }


@router.post("/tag-mappings", status_code=201)
def create_connector_tag_mapping(
    payload: ConnectorTagMappingInput,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
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
    try:
        from packages.units import convert

        convert(1.0, tag.engineering_unit, sensor.unit)
    except ValueError:
        _raise(
            "INVALID_SOURCE_UNIT",
            f"Tag unit {tag.engineering_unit} is incompatible with sensor unit {sensor.unit}",
        )
    duplicate = session.scalar(
        select(ConnectorTagMapping.id).where(
            and_(
                ConnectorTagMapping.data_source_id == source.id,
                ConnectorTagMapping.connector_tag_id == tag.id,
            )
        )
    )
    duplicate_key = session.scalar(
        select(SourceTagMapping.id).where(
            and_(
                SourceTagMapping.data_source_id == source.id,
                SourceTagMapping.source_key == payload.source_key,
            )
        )
    )
    if duplicate is not None or duplicate_key is not None:
        _raise(
            "CONNECTOR_TAG_MAPPING_EXISTS",
            "This connector tag or source key is already mapped on this data source",
            409,
        )
    mapping = ConnectorTagMapping(
        organization_id=context.organization_id,
        data_source_id=source.id,
        connector_tag_id=tag.id,
        sensor_id=sensor.id,
        source_key=payload.source_key,
    )
    session.add(mapping)
    # The gateway ingests through SourceTagMapping; keep the runtime mapping in lock-step so a
    # configured tag mapping is actually consumed instead of being catalogue-only metadata.
    session.add(
        SourceTagMapping(
            organization_id=context.organization_id,
            data_source_id=source.id,
            sensor_id=sensor.id,
            source_key=payload.source_key,
            canonical_name=tag.canonical_name,
            source_unit=tag.engineering_unit,
        )
    )
    session.flush()
    count = session.scalar(
        select(func.count())
        .select_from(SourceTagMapping)
        .where(SourceTagMapping.data_source_id == source.id)
    )
    source.configuration = {**source.configuration, "mapping_count": int(count or 0)}
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


@router.get("/validation-rules")
def list_connector_validation_rules(
    context: TenantDependency, session: SessionDependency, connector_tag_id: UUID | None = None
) -> dict[str, Any]:
    query = select(ConnectorValidationRule).where(
        ConnectorValidationRule.organization_id == context.organization_id
    )
    if connector_tag_id is not None:
        query = query.where(ConnectorValidationRule.connector_tag_id == connector_tag_id)
    rows = list(session.scalars(query.limit(500)))
    return {
        "items": [
            {
                "id": str(row.id),
                "connector_tag_id": str(row.connector_tag_id),
                "rule_type": row.rule_type,
                "parameters": row.parameters,
            }
            for row in rows
        ]
    }


@router.post("/validation-rules", status_code=201)
def create_connector_validation_rule(
    payload: ConnectorValidationRuleInput,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
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
    existing = session.scalar(
        select(ConnectorValidationRule.id).where(
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
            409,
        )
    rule = ConnectorValidationRule(
        organization_id=context.organization_id,
        connector_tag_id=tag.id,
        rule_type=payload.rule_type,
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
        metadata={"connector_tag_id": str(tag.id), "rule_type": rule.rule_type, "read_only": True},
    )
    session.commit()
    return {
        "id": str(rule.id),
        "connector_tag_id": str(rule.connector_tag_id),
        "rule_type": rule.rule_type,
        "parameters": rule.parameters,
    }


@router.get("/{source_id}")
def get_connector_source(
    source_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    return source_payload(_tenant_source(session, source_id, context.organization_id))


@router.get("/{source_id}/health")
def connector_health(
    source_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    payload = source_payload(_tenant_source(session, source_id, context.organization_id))
    return {
        key: payload[key]
        for key in (
            "id",
            "status",
            "read_only",
            "last_success_at",
            "last_message_at",
            "message_rate_per_minute",
            "latency_ms",
            "error_count",
            "freshness_s",
            "last_error",
        )
    }
