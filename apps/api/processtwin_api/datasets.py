from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import UTC, datetime
from statistics import mean, median, pstdev
from typing import Annotated, Any, NoReturn
from uuid import UUID

from connectors.historical import read_historical_source
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from packages.data_ingestion import HistoricalMapping, inspect_historical_rows
from packages.data_ingestion.pipeline import summarize_quality
from packages.units import si_unit
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .audit import append_audit
from .auth import SessionDependency, TenantContext, require_roles, tenant_context
from .config import get_settings
from .models import (
    Dataset,
    DatasetObservation,
    DatasetVersion,
    DataSource,
    Equipment,
    Plant,
    PlantTag,
    ProcessUnit,
    QualityStatusName,
    RoleName,
    TagMapping,
)

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])
EngineerContext = Annotated[
    TenantContext, Depends(require_roles(RoleName.OWNER, RoleName.ADMIN, RoleName.ENGINEER))
]
TenantDependency = Annotated[TenantContext, Depends(tenant_context)]


class ImportTagMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_tag: str = Field(min_length=1, max_length=128)
    canonical_name: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=32)
    plant_tag: str | None = Field(default=None, min_length=1, max_length=128)
    process_unit_id: UUID | None = None
    equipment_id: UUID | None = None
    minimum_si: float | None = None
    maximum_si: float | None = None
    expected_sampling_interval_s: int | None = Field(default=None, gt=0)
    max_rate_of_change_per_s: float | None = Field(default=None, gt=0)
    max_drift_per_hour: float | None = Field(default=None, gt=0)


class ImportMappingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mappings: list[ImportTagMapping] = Field(min_length=1, max_length=200)


@router.get("/hierarchy")
def dataset_hierarchy(
    plant_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    plant = session.scalar(
        select(Plant).where(Plant.id == plant_id, Plant.organization_id == context.organization_id)
    )
    if plant is None:
        _raise(404, "PLANT_NOT_FOUND", "Plant was not found in this organization")
    process_units = list(
        session.scalars(
            select(ProcessUnit)
            .where(
                ProcessUnit.organization_id == context.organization_id,
                ProcessUnit.plant_id == plant.id,
            )
            .order_by(ProcessUnit.name)
        )
    )
    equipment = list(
        session.scalars(
            select(Equipment)
            .where(
                Equipment.organization_id == context.organization_id,
                Equipment.plant_id == plant.id,
            )
            .order_by(Equipment.name)
        )
    )
    return {
        "plant_id": str(plant.id),
        "process_units": [
            {"id": str(item.id), "name": item.name, "unit_type": item.unit_type}
            for item in process_units
        ],
        "equipment": [
            {
                "id": str(item.id),
                "name": item.name,
                "tag": item.tag,
                "process_unit_id": str(item.process_unit_id) if item.process_unit_id else None,
            }
            for item in equipment
        ],
    }


def _raise(status_code: int, code: str, message: str) -> NoReturn:
    raise HTTPException(status_code, detail={"code": code, "message": message})


def _validate_mapping_scope(
    mapping: ImportTagMapping, organization_id: UUID, plant_id: UUID, session: Session
) -> tuple[UUID | None, UUID | None]:
    if mapping.process_unit_id is not None:
        unit = session.scalar(
            select(ProcessUnit).where(
                ProcessUnit.id == mapping.process_unit_id,
                ProcessUnit.organization_id == organization_id,
                ProcessUnit.plant_id == plant_id,
            )
        )
        if unit is None:
            _raise(404, "PROCESS_UNIT_NOT_FOUND", "Process unit was not found in this plant")
    if mapping.equipment_id is not None:
        equipment = session.scalar(
            select(Equipment).where(
                Equipment.id == mapping.equipment_id,
                Equipment.organization_id == organization_id,
                Equipment.plant_id == plant_id,
            )
        )
        if equipment is None:
            _raise(404, "EQUIPMENT_NOT_FOUND", "Equipment was not found in this plant")
        if mapping.process_unit_id and equipment.process_unit_id != mapping.process_unit_id:
            _raise(
                422,
                "INVALID_TAG_HIERARCHY",
                "Equipment does not belong to the selected process unit",
            )
    return mapping.process_unit_id, mapping.equipment_id


def _quality_mapping(mapping: ImportTagMapping) -> HistoricalMapping:
    return HistoricalMapping(
        source_tag=mapping.source_tag,
        canonical_name=mapping.canonical_name,
        source_unit=mapping.unit,
        minimum_si=mapping.minimum_si,
        maximum_si=mapping.maximum_si,
        expected_sampling_interval_s=mapping.expected_sampling_interval_s,
        max_rate_of_change_per_s=mapping.max_rate_of_change_per_s,
        max_drift_per_hour=mapping.max_drift_per_hour,
    )


@router.get("")
def list_datasets(context: TenantDependency, session: SessionDependency) -> dict[str, Any]:
    datasets = list(
        session.scalars(
            select(Dataset)
            .where(Dataset.organization_id == context.organization_id)
            .order_by(desc(Dataset.updated_at))
        )
    )
    items = []
    for dataset in datasets:
        latest = session.scalar(
            select(DatasetVersion)
            .where(
                DatasetVersion.dataset_id == dataset.id,
                DatasetVersion.organization_id == context.organization_id,
            )
            .order_by(desc(DatasetVersion.version))
            .limit(1)
        )
        items.append(
            {
                "id": str(dataset.id),
                "plant_id": str(dataset.plant_id),
                "name": dataset.name,
                "description": dataset.description,
                "latest_version": _serialize_version(latest) if latest is not None else None,
            }
        )
    return {"items": items}


def _serialize_version(version: DatasetVersion) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "version": version.version,
        "status": version.status,
        "source_filename": version.source_filename,
        "row_count": version.row_count,
        "measurement_count": version.measurement_count,
        "quality_summary": version.quality_summary,
        "created_at": version.created_at,
    }


@router.post("/import", status_code=201)
async def import_dataset(
    file: Annotated[UploadFile, File(...)],
    context: EngineerContext,
    session: SessionDependency,
    request: Request,
    plant_id: Annotated[UUID, Form()],
    dataset_name: Annotated[str, Form(min_length=1, max_length=200)],
    mappings: Annotated[str, Form()],
    timestamp_column: Annotated[str, Form()] = "timestamp",
    description: Annotated[str | None, Form(max_length=2000)] = None,
    dataset_id: Annotated[UUID | None, Form()] = None,
) -> dict[str, Any]:
    settings = get_settings()
    filename = file.filename
    if not filename:
        _raise(422, "INVALID_FILE_NAME", "An uploaded file name is required")
    if not filename.lower().endswith((".csv", ".parquet", ".pq")):
        _raise(422, "INVALID_FILE_TYPE", "Only CSV and Parquet files are supported")
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        _raise(413, "FILE_TOO_LARGE", "Historical file exceeds configured size limit")
    try:
        mapping_request = ImportMappingRequest.model_validate_json(mappings)
    except (ValidationError, ValueError) as exc:
        _raise(422, "INVALID_TAG_MAPPING", f"Tag mapping JSON is invalid: {exc}")
    if len({item.source_tag for item in mapping_request.mappings}) != len(mapping_request.mappings):
        _raise(422, "DUPLICATE_SOURCE_TAG", "Each uploaded source tag must be mapped once")

    plant = session.scalar(
        select(Plant).where(Plant.id == plant_id, Plant.organization_id == context.organization_id)
    )
    if plant is None:
        _raise(404, "PLANT_NOT_FOUND", "Plant was not found in this organization")
    try:
        rows = read_historical_source(filename, raw)
        if len(rows) > 100_000:
            _raise(
                413, "TOO_MANY_ROWS", "Historical imports are limited to 100,000 rows per version"
            )
        if len(rows) * len(mapping_request.mappings) > 500_000:
            _raise(
                413,
                "TOO_MANY_MEASUREMENTS",
                "Historical imports are limited to 500,000 tag observations per version",
            )
        observations = inspect_historical_rows(
            rows,
            [_quality_mapping(mapping) for mapping in mapping_request.mappings],
            timestamp_column=timestamp_column,
        )
    except RuntimeError as exc:
        _raise(422, "FORMAT_SUPPORT_UNAVAILABLE", str(exc))
    except ValueError as exc:
        _raise(422, "INVALID_HISTORICAL_DATA", str(exc))

    dataset: Dataset | None
    if dataset_id is None:
        dataset = Dataset(
            organization_id=context.organization_id,
            plant_id=plant.id,
            name=dataset_name.strip(),
            description=description,
        )
        session.add(dataset)
        session.flush()
        source = DataSource(
            organization_id=context.organization_id,
            plant_id=plant.id,
            name=f"{dataset_name.strip()} file upload",
            source_type="PARQUET" if filename.lower().endswith((".parquet", ".pq")) else "CSV",
            read_only=True,
            configuration={"upload_filename": filename},
        )
        session.add(source)
        session.flush()
        dataset.data_source_id = source.id
    else:
        dataset = session.scalar(
            select(Dataset).where(
                Dataset.id == dataset_id,
                Dataset.organization_id == context.organization_id,
                Dataset.plant_id == plant.id,
            )
        )
        if dataset is None:
            _raise(404, "DATASET_NOT_FOUND", "Dataset was not found in this organization and plant")

    plant_tags: dict[str, PlantTag] = {}
    mappings_by_source: dict[str, TagMapping] = {}
    for mapping in mapping_request.mappings:
        unit = si_unit(mapping.unit)
        process_unit_id, equipment_id = _validate_mapping_scope(
            mapping, context.organization_id, plant.id, session
        )
        tag_name = mapping.plant_tag or mapping.source_tag
        plant_tag = session.scalar(
            select(PlantTag).where(
                PlantTag.organization_id == context.organization_id,
                PlantTag.plant_id == plant.id,
                PlantTag.tag == tag_name,
            )
        )
        if plant_tag is None:
            plant_tag = PlantTag(
                organization_id=context.organization_id,
                plant_id=plant.id,
                process_unit_id=process_unit_id,
                equipment_id=equipment_id,
                tag=tag_name,
                canonical_name=mapping.canonical_name,
                engineering_unit=mapping.unit,
                normalized_unit=unit,
                minimum_si=mapping.minimum_si,
                maximum_si=mapping.maximum_si,
                expected_sampling_interval_s=mapping.expected_sampling_interval_s,
                max_rate_of_change_per_s=mapping.max_rate_of_change_per_s,
                max_drift_per_hour=mapping.max_drift_per_hour,
            )
            session.add(plant_tag)
            session.flush()
        elif (
            plant_tag.canonical_name != mapping.canonical_name or plant_tag.normalized_unit != unit
        ):
            _raise(
                409,
                "PLANT_TAG_MAPPING_CONFLICT",
                f"Plant tag {tag_name} already has a different canonical mapping",
            )
        tag_mapping = session.scalar(
            select(TagMapping).where(
                TagMapping.dataset_id == dataset.id,
                TagMapping.source_tag == mapping.source_tag,
            )
        )
        if tag_mapping is None:
            tag_mapping = TagMapping(
                organization_id=context.organization_id,
                dataset_id=dataset.id,
                plant_tag_id=plant_tag.id,
                source_tag=mapping.source_tag,
                source_unit=mapping.unit,
            )
            session.add(tag_mapping)
            session.flush()
        elif tag_mapping.plant_tag_id != plant_tag.id or tag_mapping.source_unit != mapping.unit:
            _raise(
                409,
                "DATASET_MAPPING_CONFLICT",
                f"Dataset mapping for {mapping.source_tag} differs from this version",
            )
        plant_tags[mapping.source_tag] = plant_tag
        mappings_by_source[mapping.source_tag] = tag_mapping

    current_version = (
        session.scalar(
            select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == dataset.id)
        )
        or 0
    )
    version = DatasetVersion(
        organization_id=context.organization_id,
        dataset_id=dataset.id,
        version=current_version + 1,
        status="IMPORTED",
        source_filename=filename[:255],
        checksum_sha256=hashlib.sha256(raw).hexdigest(),
        row_count=len(rows),
        measurement_count=len(observations),
        quality_summary=summarize_quality(observations),
    )
    session.add(version)
    session.flush()
    session.add_all(
        [
            DatasetObservation(
                organization_id=context.organization_id,
                dataset_version_id=version.id,
                tag_mapping_id=mappings_by_source[item.source_tag].id,
                timestamp=item.timestamp,
                original_timestamp=item.original_timestamp[:128],
                row_number=item.row_number,
                original_value=item.original_value,
                original_text=item.original_text,
                original_unit=item.original_unit,
                normalized_value=item.normalized_value,
                normalized_unit=item.normalized_unit,
                quality_status=item.quality_status,
                quality_reasons=list(item.quality_reasons),
            )
            for item in observations
        ]
    )
    append_audit(
        session,
        context.organization_id,
        "IMPORT_HISTORICAL_DATASET",
        f"dataset:{dataset.id}:version:{version.version}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={"rows": len(rows), "measurements": len(observations), "filename": filename},
    )
    session.commit()
    return {
        "dataset_id": str(dataset.id),
        "version": _serialize_version(version),
        "quality_report": version.quality_summary,
        "variables": [
            {
                "source_tag": tag_mapping.source_tag,
                "tag": plant_tags[tag_mapping.source_tag].tag,
                "canonical_name": plant_tags[tag_mapping.source_tag].canonical_name,
                "engineering_unit": tag_mapping.source_unit,
                "normalized_unit": plant_tags[tag_mapping.source_tag].normalized_unit,
            }
            for tag_mapping in mappings_by_source.values()
        ],
    }


def _percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


@router.get("/{version_id}/variables")
def list_dataset_variables(
    version_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    version = session.scalar(
        select(DatasetVersion).where(
            DatasetVersion.id == version_id,
            DatasetVersion.organization_id == context.organization_id,
        )
    )
    if version is None:
        _raise(404, "DATASET_VERSION_NOT_FOUND", "Dataset version was not found")
    rows = session.execute(
        select(TagMapping, PlantTag)
        .join(PlantTag, PlantTag.id == TagMapping.plant_tag_id)
        .where(
            TagMapping.dataset_id == version.dataset_id,
            TagMapping.organization_id == context.organization_id,
        )
        .order_by(PlantTag.canonical_name)
    ).all()
    return {
        "dataset_version_id": str(version.id),
        "items": [
            {
                "source_tag": mapping.source_tag,
                "tag": plant_tag.tag,
                "canonical_name": plant_tag.canonical_name,
                "engineering_unit": mapping.source_unit,
                "normalized_unit": plant_tag.normalized_unit,
                "plant_id": str(plant_tag.plant_id),
                "process_unit_id": str(plant_tag.process_unit_id)
                if plant_tag.process_unit_id
                else None,
                "equipment_id": str(plant_tag.equipment_id) if plant_tag.equipment_id else None,
            }
            for mapping, plant_tag in rows
        ],
    }


@router.get("/{version_id}/exploration")
def explore_dataset(
    version_id: UUID,
    context: TenantDependency,
    session: SessionDependency,
    plant_id: UUID | None = None,
    process_unit_id: UUID | None = None,
    equipment_id: UUID | None = None,
    canonical_name: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    if not 1 <= limit <= 5000:
        _raise(422, "INVALID_LIMIT", "limit must be between 1 and 5000")
    if start and (start.tzinfo is None or start.utcoffset() is None):
        _raise(422, "INVALID_TIME_RANGE", "start must include a timezone")
    if end and (end.tzinfo is None or end.utcoffset() is None):
        _raise(422, "INVALID_TIME_RANGE", "end must include a timezone")
    if start and end and start >= end:
        _raise(422, "INVALID_TIME_RANGE", "start must precede end")
    version = session.scalar(
        select(DatasetVersion).where(
            DatasetVersion.id == version_id,
            DatasetVersion.organization_id == context.organization_id,
        )
    )
    if version is None:
        _raise(404, "DATASET_VERSION_NOT_FOUND", "Dataset version was not found")

    query = (
        select(DatasetObservation, TagMapping, PlantTag)
        .join(TagMapping, TagMapping.id == DatasetObservation.tag_mapping_id)
        .join(PlantTag, PlantTag.id == TagMapping.plant_tag_id)
        .where(
            DatasetObservation.dataset_version_id == version.id,
            DatasetObservation.organization_id == context.organization_id,
            TagMapping.organization_id == context.organization_id,
        )
    )
    if plant_id:
        query = query.where(PlantTag.plant_id == plant_id)
    if process_unit_id:
        query = query.where(PlantTag.process_unit_id == process_unit_id)
    if equipment_id:
        query = query.where(PlantTag.equipment_id == equipment_id)
    if canonical_name:
        query = query.where(PlantTag.canonical_name == canonical_name)
    if start:
        query = query.where(DatasetObservation.timestamp >= start.astimezone(UTC))
    if end:
        query = query.where(DatasetObservation.timestamp <= end.astimezone(UTC))
    result_rows = session.execute(
        query.order_by(DatasetObservation.timestamp).limit(limit * 200)
    ).all()
    by_variable: dict[str, dict[str, Any]] = {}
    pairs: dict[tuple[str, str], dict[datetime, float]] = defaultdict(dict)
    for observation, mapping, tag in result_rows:
        key = tag.canonical_name
        entry = by_variable.setdefault(
            key,
            {
                "source_tags": set(),
                "engineering_unit": mapping.source_unit,
                "normalized_unit": tag.normalized_unit,
                "expected_sampling_interval_s": tag.expected_sampling_interval_s,
                "values": [],
                "timestamps": [],
                "quality_counts": {status.value: 0 for status in QualityStatusName},
                "missing_count": 0,
                "unavailable_count": 0,
                "observation_count": 0,
                "outliers": [],
            },
        )
        entry["source_tags"].add(mapping.source_tag)
        entry["observation_count"] += 1
        entry["quality_counts"][observation.quality_status.value] += 1
        if observation.quality_status is QualityStatusName.MISSING:
            entry["missing_count"] += 1
        if observation.normalized_value is None or observation.timestamp is None:
            entry["unavailable_count"] += 1
            continue
        entry["values"].append(observation.normalized_value)
        entry["timestamps"].append(observation.timestamp)
        pairs[(key, mapping.source_tag)][observation.timestamp] = observation.normalized_value
        if any(
            reason in {"SUDDEN_SPIKE", "UNREALISTIC_RATE_OF_CHANGE", "SENSOR_DRIFT"}
            for reason in observation.quality_reasons
        ):
            entry["outliers"].append(
                {
                    "timestamp": observation.timestamp,
                    "value": observation.normalized_value,
                    "reasons": observation.quality_reasons,
                }
            )

    variables = []
    gaps: list[dict[str, Any]] = []
    trend_limit = 500
    trends: dict[str, list[dict[str, Any]]] = {}
    for key, entry in by_variable.items():
        values: list[float] = entry["values"]
        times: list[datetime] = entry["timestamps"]
        intervals = sorted(
            (right - left).total_seconds()
            for left, right in zip(sorted(set(times)), sorted(set(times))[1:], strict=False)
            if right > left
        )
        sampling = median(intervals) if intervals else entry["expected_sampling_interval_s"]
        expected = entry["expected_sampling_interval_s"] or sampling
        if expected:
            for left, right in zip(sorted(set(times)), sorted(set(times))[1:], strict=False):
                seconds = (right - left).total_seconds()
                if seconds > expected * 1.5:
                    gaps.append(
                        {
                            "variable": key,
                            "start": left,
                            "end": right,
                            "duration_s": seconds,
                            "expected_interval_s": expected,
                        }
                    )
        count = len(values)
        variables.append(
            {
                "canonical_name": key,
                "source_tags": sorted(entry["source_tags"]),
                "engineering_unit": entry["engineering_unit"],
                "normalized_unit": entry["normalized_unit"],
                "sample_count": count,
                "missing_count": entry["missing_count"],
                "unavailable_count": entry["unavailable_count"],
                "missingness_pct": (entry["missing_count"] / entry["observation_count"] * 100)
                if entry["observation_count"]
                else 0.0,
                "sampling_rate_hz": (1.0 / sampling) if sampling and sampling > 0 else None,
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "mean": mean(values) if values else None,
                "standard_deviation": pstdev(values) if count > 1 else (0.0 if count else None),
                "percentiles": {
                    "p05": _percentile(values, 0.05),
                    "p25": _percentile(values, 0.25),
                    "p50": _percentile(values, 0.5),
                    "p75": _percentile(values, 0.75),
                    "p95": _percentile(values, 0.95),
                },
                "quality_counts": entry["quality_counts"],
                "outliers": entry["outliers"][:200],
            }
        )
        trend_indices = sorted(range(len(times)), key=lambda index: times[index])[-trend_limit:]
        trends[key] = [
            {"timestamp": times[index], "value": values[index]} for index in trend_indices
        ]

    correlation: dict[str, dict[str, float | None]] = {}
    series_by_name: dict[str, dict[datetime, float]] = {}
    for (name, _), values_by_time in pairs.items():
        series_by_name.setdefault(name, {}).update(values_by_time)
    for left_name, left_values in series_by_name.items():
        correlation[left_name] = {}
        for right_name, right_values in series_by_name.items():
            shared = sorted(left_values.keys() & right_values.keys())
            if left_name == right_name:
                coefficient = 1.0 if shared else None
            elif len(shared) < 2:
                coefficient = None
            else:
                xs = [left_values[timestamp] for timestamp in shared]
                ys = [right_values[timestamp] for timestamp in shared]
                sx, sy = pstdev(xs), pstdev(ys)
                coefficient = (
                    sum((x - mean(xs)) * (y - mean(ys)) for x, y in zip(xs, ys, strict=True))
                    / (len(shared) * sx * sy)
                    if sx and sy
                    else None
                )
            correlation[left_name][right_name] = coefficient
    return {
        "dataset_version": _serialize_version(version),
        "variables": variables,
        "correlation": correlation,
        "trends": trends,
        "data_gaps": gaps[:1000],
        "quality_report": version.quality_summary,
        "returned_observations": len(result_rows),
    }
