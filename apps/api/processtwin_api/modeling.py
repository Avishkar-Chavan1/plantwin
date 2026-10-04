from __future__ import annotations

import subprocess
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, NoReturn
from uuid import UUID, uuid4

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from numpy.typing import NDArray
from packages.calibration import (
    HistoricalCSTRSeries,
    calibrate_cstr,
    evaluate_cstr,
    parameter_catalog,
    parameter_set_from_values,
    simulate_historical_series,
    validate_parameter_records,
    volumetric_flow_m3_s,
)
from packages.ml.drift import compare_drift
from packages.physics import CSTRParameters
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .audit import append_audit
from .auth import SessionDependency, TenantContext, require_roles, tenant_context
from .config import get_settings
from .models import (
    CalibrationRun,
    DatasetObservation,
    DatasetVersion,
    ModelDriftEvent,
    ModelEvaluation,
    ModelVersion,
    PhysicsParameterSet,
    PlantTag,
    QualityStatusName,
    RoleName,
    TagMapping,
)
from .registry import log_model_metadata

router = APIRouter(tags=["calibration-and-models"])
EngineerContext = Annotated[
    TenantContext, Depends(require_roles(RoleName.OWNER, RoleName.ADMIN, RoleName.ENGINEER))
]
AdminContext = Annotated[TenantContext, Depends(require_roles(RoleName.OWNER, RoleName.ADMIN))]
TenantDependency = Annotated[TenantContext, Depends(tenant_context)]


class ParameterField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: float
    unit: str = Field(min_length=1, max_length=64)
    minimum: float
    maximum: float
    initial_value: float
    description: str = Field(min_length=1, max_length=500)
    source: str = Field(min_length=1, max_length=200)


class ParameterSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    source: str = Field(min_length=1, max_length=200)
    parameters: dict[str, ParameterField]


class EnvelopeRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum: float
    maximum: float

    @field_validator("maximum")
    @classmethod
    def increasing(cls, value: float, info: Any) -> float:
        minimum = info.data.get("minimum")
        if minimum is not None and value <= minimum:
            raise ValueError("Envelope maximum must exceed its minimum")
        return value


class CalibrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version_id: UUID
    parameter_set_id: UUID
    fit_bounds: dict[str, tuple[float, float]] = Field(min_length=1, max_length=11)
    method: str = Field(default="least_squares", pattern="^(least_squares|differential_evolution)$")
    max_evaluations: int = Field(default=100, ge=1, le=2000)
    operating_envelope: dict[str, EnvelopeRange] = Field(default_factory=dict)


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version_id: UUID


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_id: UUID
    acceptance_limits: dict[str, dict[str, dict[str, float | None]]] = Field(min_length=1)
    review_note: str = Field(min_length=30, max_length=4000)


class DriftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version_id: UUID
    psi_threshold: float = Field(default=0.2, gt=0)
    ks_pvalue_threshold: float = Field(default=0.01, gt=0, lt=1)
    js_threshold: float = Field(default=0.1, gt=0)


class CalibrationSeriesError(ValueError):
    pass


def _raise(status_code: int, code: str, message: str) -> NoReturn:
    raise HTTPException(status_code, detail={"code": code, "message": message})


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()[:80]
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _parameter_records(parameters: CSTRParameters, source: str) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for name, definition in parameter_catalog(parameters).items():
        record = definition.as_dict()
        record["source"] = source
        records[name] = record
    return records


def _get_dataset_version(
    session: Session, version_id: UUID, organization_id: UUID
) -> DatasetVersion:
    version = session.scalar(
        select(DatasetVersion).where(
            DatasetVersion.id == version_id,
            DatasetVersion.organization_id == organization_id,
        )
    )
    if version is None:
        _raise(
            404, "DATASET_VERSION_NOT_FOUND", "Dataset version was not found in this organization"
        )
    return version


def _historical_series(
    session: Session,
    version: DatasetVersion,
    organization_id: UUID,
    density_kg_m3: float = 1000.0,
) -> HistoricalCSTRSeries:
    rows = session.execute(
        select(DatasetObservation, PlantTag)
        .join(TagMapping, TagMapping.id == DatasetObservation.tag_mapping_id)
        .join(PlantTag, PlantTag.id == TagMapping.plant_tag_id)
        .where(
            DatasetObservation.dataset_version_id == version.id,
            DatasetObservation.organization_id == organization_id,
            DatasetObservation.quality_status == QualityStatusName.GOOD,
            DatasetObservation.timestamp.is_not(None),
            DatasetObservation.normalized_value.is_not(None),
        )
        .order_by(DatasetObservation.timestamp, DatasetObservation.row_number)
    ).all()
    by_timestamp: dict[datetime, dict[str, float]] = defaultdict(dict)
    canonical_units: dict[str, str] = {}
    for observation, tag in rows:
        timestamp = observation.timestamp
        if timestamp is None or observation.normalized_value is None:
            continue
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        canonical = tag.canonical_name
        existing_unit = canonical_units.get(canonical)
        if existing_unit is not None and existing_unit != tag.normalized_unit:
            raise CalibrationSeriesError(
                f"Canonical signal {canonical} has inconsistent normalized units"
            )
        canonical_units[canonical] = tag.normalized_unit
        if canonical in by_timestamp[timestamp]:
            raise CalibrationSeriesError(
                f"Dataset maps more than one GOOD value to {canonical} at {timestamp.isoformat()}"
            )
        by_timestamp[timestamp][canonical] = float(observation.normalized_value)

    required_inputs = {
        "reactor.feed_flow",
        "reactor.feed_temperature",
        "reactor.feed_concentration",
        "reactor.cooling_temperature",
        "reactor.temperature",
    }
    common = [
        (timestamp, values)
        for timestamp, values in sorted(by_timestamp.items())
        if required_inputs.issubset(values)
    ]
    if len(common) < 10:
        raise CalibrationSeriesError(
            "At least 10 aligned GOOD samples for feed flow, feed temperature, feed concentration, "
            "cooling temperature, and reactor temperature are required. Configure these canonical tag mappings."
        )
    expected_units = {
        "reactor.feed_flow": {"m3/s", "kg/s"},
        "reactor.feed_temperature": {"K"},
        "reactor.feed_concentration": {"mol/m3"},
        "reactor.cooling_temperature": {"K"},
        "reactor.temperature": {"K"},
        "reactor.pressure": {"Pa"},
        "reactor.concentration_a": {"mol/m3"},
        "reactor.product_b": {"mol/m3"},
        "reactor.product_c": {"mol/m3"},
    }
    for canonical, unit in canonical_units.items():
        if canonical in expected_units and unit not in expected_units[canonical]:
            raise CalibrationSeriesError(
                f"Canonical signal {canonical} must be normalized to {sorted(expected_units[canonical])}; got {unit}"
            )
    columns: dict[str, NDArray[np.float64]] = {
        name: np.asarray([values[name] for _, values in common], dtype=np.float64)
        for name in required_inputs
    }
    optional_targets = {
        "reactor.concentration_a": "measured_concentration_a_mol_m3",
        "reactor.product_b": "measured_concentration_b_mol_m3",
        "reactor.product_c": "measured_concentration_c_mol_m3",
    }
    optional: dict[str, NDArray[np.float64]] = {
        field: np.asarray([values[name] for _, values in common], dtype=np.float64)
        for name, field in optional_targets.items()
        if all(name in values for _, values in common)
    }
    pressure: NDArray[np.float64] | None = (
        np.asarray([values["reactor.pressure"] for _, values in common], dtype=np.float64)
        if all("reactor.pressure" in values for _, values in common)
        else None
    )
    feed_flow_unit = canonical_units["reactor.feed_flow"]
    try:
        feed_flow_m3_s = volumetric_flow_m3_s(
            columns["reactor.feed_flow"], feed_flow_unit, density_kg_m3
        )
    except ValueError as exc:
        raise CalibrationSeriesError(str(exc)) from exc
    try:
        return HistoricalCSTRSeries(
            timestamps=tuple(timestamp for timestamp, _ in common),
            feed_flow_m3_s=feed_flow_m3_s,
            feed_temperature_k=columns["reactor.feed_temperature"],
            feed_concentration_a_mol_m3=columns["reactor.feed_concentration"],
            cooling_temperature_k=columns["reactor.cooling_temperature"],
            measured_temperature_k=columns["reactor.temperature"],
            pressure_pa=pressure,
            feed_flow_source_unit=feed_flow_unit,
            **optional,
        )
    except ValueError as exc:
        raise CalibrationSeriesError(str(exc)) from exc


def _version_payload(version: PhysicsParameterSet) -> dict[str, Any]:
    return {
        "id": str(version.id),
        "name": version.name,
        "version": version.version,
        "status": version.status,
        "description": version.description,
        "source": version.source,
        "parameters": version.parameters,
        "created_at": version.created_at,
        "created_by": str(version.created_by) if version.created_by else None,
    }


def _calibration_payload(run: CalibrationRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "dataset_version_id": str(run.dataset_version_id),
        "parameter_set_id": str(run.parameter_set_id),
        "model_version_id": str(run.model_version_id) if run.model_version_id else None,
        "user_id": str(run.user_id) if run.user_id else None,
        "method": run.method,
        "objective": run.objective_name,
        "initial_parameters": run.initial_parameters,
        "calibrated_parameters": run.calibrated_parameters,
        "bounds": run.bounds,
        "metrics": run.metrics,
        "status": run.status,
        "code_version": run.code_version,
        "observation_count": run.observation_count,
        "created_at": run.created_at,
    }


@router.get("/api/v1/calibrations")
def list_calibrations(context: TenantDependency, session: SessionDependency) -> dict[str, Any]:
    items = list(
        session.scalars(
            select(CalibrationRun)
            .where(CalibrationRun.organization_id == context.organization_id)
            .order_by(desc(CalibrationRun.created_at))
            .limit(200)
        )
    )
    return {"items": [_calibration_payload(item) for item in items]}


@router.get("/api/v1/models/{model_id}/evaluations")
def list_model_evaluations(
    model_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    model = session.scalar(
        select(ModelVersion).where(
            ModelVersion.id == model_id,
            ModelVersion.organization_id == context.organization_id,
        )
    )
    if model is None:
        _raise(404, "MODEL_NOT_FOUND", "Model version was not found")
    items = list(
        session.scalars(
            select(ModelEvaluation)
            .where(
                ModelEvaluation.organization_id == context.organization_id,
                ModelEvaluation.model_version_id == model.id,
            )
            .order_by(desc(ModelEvaluation.created_at))
            .limit(200)
        )
    )
    return {
        "items": [
            {
                "id": str(item.id),
                "dataset_version_id": str(item.dataset_version_id),
                "evaluation_type": item.evaluation_type,
                "metrics": item.metrics,
                "residual_distribution": item.residual_distribution,
                "comparisons": item.comparisons,
                "observation_count": item.observation_count,
                "status": item.status,
                "created_at": item.created_at,
            }
            for item in items
        ]
    }


@router.get("/api/v1/models/{model_id}/drift-events")
def list_model_drift_events(
    model_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    model = session.scalar(
        select(ModelVersion).where(
            ModelVersion.id == model_id,
            ModelVersion.organization_id == context.organization_id,
        )
    )
    if model is None:
        _raise(404, "MODEL_NOT_FOUND", "Model version was not found")
    items = list(
        session.scalars(
            select(ModelDriftEvent)
            .where(
                ModelDriftEvent.organization_id == context.organization_id,
                ModelDriftEvent.model_version_id == model.id,
            )
            .order_by(desc(ModelDriftEvent.created_at))
            .limit(200)
        )
    )
    return {
        "items": [
            {
                "id": str(item.id),
                "dataset_version_id": str(item.dataset_version_id)
                if item.dataset_version_id
                else None,
                "status": item.status,
                "metrics": item.metrics,
                "reasons": item.reasons,
                "created_at": item.created_at,
            }
            for item in items
        ]
    }


def _envelope_outside(
    series: HistoricalCSTRSeries,
    envelope: dict[str, Any],
    density_kg_m3: float,
) -> dict[str, int]:
    if not envelope:
        raise CalibrationSeriesError(
            "Model has no configured operating envelope; evaluation predictions are disabled"
        )
    inputs = {
        "temperature_k": series.measured_temperature_k,
        "temperature_c": series.measured_temperature_k - 273.15,
        "pressure_pa": series.pressure_pa,
        "pressure_bar": series.pressure_pa / 100_000.0 if series.pressure_pa is not None else None,
        "feed_flow_m3_s": series.feed_flow_m3_s,
        "feed_flow_kg_s": series.feed_flow_m3_s * density_kg_m3,
        "feed_flow_kg_h": series.feed_flow_m3_s * density_kg_m3 * 3600.0,
    }
    outside: dict[str, int] = {}
    for name, bounds in envelope.items():
        values = inputs.get(name)
        if values is None:
            raise CalibrationSeriesError(f"Envelope signal {name} is unavailable in the dataset")
        minimum, maximum = float(bounds["minimum"]), float(bounds["maximum"])
        if minimum >= maximum:
            raise CalibrationSeriesError(f"Envelope range for {name} must be increasing")
        count = int(np.count_nonzero((values < minimum) | (values > maximum)))
        if count:
            outside[name] = count
    return outside


def _residual_reference(
    series: HistoricalCSTRSeries, parameters: CSTRParameters
) -> dict[str, list[float]]:
    prediction = simulate_historical_series(series, parameters)
    train_end = int(len(series.timestamps) * 0.6)
    indices = np.linspace(0, train_end - 1, min(train_end, 1000), dtype=int)
    targets: dict[str, NDArray[np.float64]] = {
        "feature:feed_temperature_k": series.feed_temperature_k,
        "feature:pressure_pa": series.pressure_pa
        if series.pressure_pa is not None
        else np.full(len(series.timestamps), 101_325.0, dtype=np.float64),
        "feature:feed_flow_m3_s": series.feed_flow_m3_s,
        "feature:feed_concentration_a_mol_m3": series.feed_concentration_a_mol_m3,
        "feature:cooling_temperature_k": series.cooling_temperature_k,
        "target:temperature_k": series.measured_temperature_k,
        "physics_residual:temperature_k": series.measured_temperature_k
        - prediction["temperature_k"],
        "prediction_error:temperature_k": prediction["temperature_k"]
        - series.measured_temperature_k,
    }
    result: dict[str, list[float]] = {}
    for key, values in targets.items():
        result[key] = np.asarray(values)[indices].astype(float).tolist()
    return result


@router.get("/api/v1/physics/parameter-catalog")
def get_parameter_catalog(context: TenantDependency) -> dict[str, Any]:
    return {
        "units": "SI",
        "parameters": {name: item.as_dict() for name, item in parameter_catalog().items()},
        "note": "Reference-model initial values are engineering defaults, not plant measurements or calibrated values.",
    }


@router.get("/api/v1/physics/parameter-sets")
def list_parameter_sets(context: TenantDependency, session: SessionDependency) -> dict[str, Any]:
    items = list(
        session.scalars(
            select(PhysicsParameterSet)
            .where(PhysicsParameterSet.organization_id == context.organization_id)
            .order_by(PhysicsParameterSet.name, PhysicsParameterSet.version)
        )
    )
    return {"items": [_version_payload(item) for item in items]}


@router.post("/api/v1/physics/parameter-sets", status_code=201)
def create_parameter_set(
    payload: ParameterSetRequest,
    context: EngineerContext,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    if set(payload.parameters) != set(parameter_catalog()):
        _raise(
            422,
            "INCOMPLETE_PARAMETER_SET",
            "Provide definitions for every CSTR parameter in the catalog",
        )
    try:
        normalized = {
            name: ParameterField.model_validate(value).model_dump()
            for name, value in payload.parameters.items()
        }
        validate_parameter_records(normalized)
        expected_units = {name: value.unit for name, value in parameter_catalog().items()}
        for name, definition in normalized.items():
            if definition["unit"] != expected_units[name]:
                raise ValueError(f"Parameter {name} unit must be {expected_units[name]}")
    except (ValueError, TypeError) as exc:
        _raise(422, "INVALID_PARAMETER_SET", str(exc))
    if session.scalar(
        select(PhysicsParameterSet.id).where(
            PhysicsParameterSet.organization_id == context.organization_id,
            PhysicsParameterSet.name == payload.name,
        )
    ):
        _raise(
            409,
            "PARAMETER_SET_EXISTS",
            "Parameter sets are immutable; choose a new name for a new set",
        )
    item = PhysicsParameterSet(
        organization_id=context.organization_id,
        name=payload.name.strip(),
        version=1,
        status="INITIAL",
        description=payload.description,
        parameters=normalized,
        source=payload.source,
        created_by=context.user.id,
    )
    session.add(item)
    session.flush()
    append_audit(
        session,
        context.organization_id,
        "CREATE_PHYSICS_PARAMETER_SET",
        f"parameter_set:{item.id}:v1",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={"parameter_names": sorted(normalized)},
    )
    session.commit()
    return _version_payload(item)


@router.post("/api/v1/calibrations", status_code=201)
def create_calibration(
    payload: CalibrationRequest,
    context: EngineerContext,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    version = _get_dataset_version(session, payload.dataset_version_id, context.organization_id)
    parameter_set = session.scalar(
        select(PhysicsParameterSet).where(
            PhysicsParameterSet.id == payload.parameter_set_id,
            PhysicsParameterSet.organization_id == context.organization_id,
        )
    )
    if parameter_set is None:
        _raise(404, "PARAMETER_SET_NOT_FOUND", "Parameter set was not found")
    try:
        parameters = parameter_set_from_values(parameter_set.parameters)
        series = _historical_series(
            session, version, context.organization_id, parameters.density_kg_m3
        )
        result = calibrate_cstr(
            series,
            parameters,
            dict(payload.fit_bounds),
            method=payload.method,
            max_evaluations=payload.max_evaluations,
        )
    except (CalibrationSeriesError, ValueError, RuntimeError) as exc:
        _raise(422, "CALIBRATION_NOT_POSSIBLE", str(exc))

    calibration_id = uuid4()
    calibrated_parameter_set_id = uuid4()
    new_version = (
        session.scalar(
            select(func.max(PhysicsParameterSet.version)).where(
                PhysicsParameterSet.organization_id == context.organization_id,
                PhysicsParameterSet.name == parameter_set.name,
            )
        )
        or parameter_set.version
    ) + 1
    calibrated_records = {name: dict(value) for name, value in parameter_set.parameters.items()}
    for name, value in result.calibrated_parameters.items():
        calibrated_records[name]["value"] = value
        calibrated_records[name]["source"] = f"calibration_run:{calibration_id}"
    if result.optimizer_success:
        calibrated_set = PhysicsParameterSet(
            id=calibrated_parameter_set_id,
            organization_id=context.organization_id,
            name=parameter_set.name,
            version=new_version,
            status="CALIBRATED",
            description=f"Calibrated from dataset version {version.version}; parent set {parameter_set.id}",
            parameters=calibrated_records,
            source=f"calibration_run:{calibration_id}",
            created_by=context.user.id,
        )
        session.add(calibrated_set)
        session.flush()
    else:
        calibrated_set = None

    model_version = None
    if calibrated_set is not None:
        model_version = ModelVersion(
            organization_id=context.organization_id,
            name=f"CSTR Physics — {parameter_set.name}",
            version=f"v{new_version}",
            model_type="physics_cstr",
            status="CALIBRATED",
            feature_schema={
                "inputs": [
                    "reactor.feed_flow",
                    "reactor.feed_temperature",
                    "reactor.feed_concentration",
                    "reactor.cooling_temperature",
                ],
                "units": "SI",
            },
            target_schema={
                "outputs": [
                    "reactor.temperature",
                    "reactor.concentration_a",
                    "reactor.product_b",
                    "reactor.product_c",
                ]
            },
            metrics={
                "calibration_objective": result.objective,
                "optimizer_success": result.optimizer_success,
                "evaluations": {
                    name: {
                        "metrics": value.metrics,
                        "residual_distribution": value.residual_distribution,
                        "observation_count": value.observation_count,
                    }
                    for name, value in result.evaluations.items()
                },
                "drift_reference": _residual_reference(series, result.parameters),
            },
            dataset_version_id=version.id,
            physics_parameter_set_id=calibrated_set.id,
            training_period={
                "start": series.timestamps[0].isoformat(),
                "end": series.timestamps[result.training_indices[1] - 1].isoformat(),
                "count": result.training_indices[1],
            },
            validation_period={
                "start": series.timestamps[result.validation_indices[0]].isoformat(),
                "end": series.timestamps[result.validation_indices[1] - 1].isoformat(),
                "count": result.validation_indices[1] - result.validation_indices[0],
            },
            test_period={
                "start": series.timestamps[result.test_indices[0]].isoformat(),
                "end": series.timestamps[-1].isoformat(),
                "count": result.test_indices[1] - result.test_indices[0],
            },
            hyperparameters={
                "method": payload.method,
                "fit_bounds": {name: list(value) for name, value in payload.fit_bounds.items()},
                "max_evaluations": payload.max_evaluations,
            },
            operating_envelope={
                name: bounds.model_dump() for name, bounds in payload.operating_envelope.items()
            },
            git_sha=_git_sha(),
            created_by=context.user.id,
        )
        session.add(model_version)
        session.flush()
        mlflow_run_id, registry_sync = log_model_metadata(
            tracking_uri=get_settings().mlflow_tracking_uri,
            organization_id=str(context.organization_id),
            model_name=model_version.name,
            model_version=model_version.version,
            model_id=str(model_version.id),
            dataset_version_id=str(version.id),
            parameter_set_id=str(calibrated_set.id),
            git_sha=model_version.git_sha or "unknown",
            parameters=calibrated_records,
            metrics=model_version.metrics,
        )
        model_version.mlflow_run_id = mlflow_run_id
        model_version.metrics = {**model_version.metrics, "mlflow_registry_sync": registry_sync}
    run = CalibrationRun(
        id=calibration_id,
        organization_id=context.organization_id,
        dataset_version_id=version.id,
        parameter_set_id=parameter_set.id,
        user_id=context.user.id,
        model_version_id=model_version.id if model_version else None,
        method=payload.method,
        objective_name="mean_squared_scaled_physics_residual",
        initial_parameters=result.initial_parameters,
        calibrated_parameters=result.calibrated_parameters,
        bounds={name: list(bound) for name, bound in result.bounds.items()},
        metrics={
            "objective": result.objective,
            "optimizer_success": result.optimizer_success,
            "optimizer_message": result.optimizer_message,
            "evaluations": {
                name: {
                    "metrics": value.metrics,
                    "residual_distribution": value.residual_distribution,
                    "observation_count": value.observation_count,
                }
                for name, value in result.evaluations.items()
            },
        },
        status="CALIBRATED" if result.optimizer_success else "FAILED",
        code_version=_git_sha(),
        observation_count=len(series.timestamps),
    )
    session.add(run)
    append_audit(
        session,
        context.organization_id,
        "CALIBRATE_PHYSICS_MODEL",
        f"calibration:{run.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={
            "dataset_version_id": str(version.id),
            "method": payload.method,
            "status": run.status,
        },
    )
    session.commit()
    if not result.optimizer_success:
        _raise(422, "CALIBRATION_DID_NOT_CONVERGE", result.optimizer_message)
    if calibrated_set is None:
        _raise(
            500,
            "CALIBRATION_RESULT_INCOMPLETE",
            "Successful calibration did not produce a parameter-set version",
        )
    return {
        "calibration_run_id": str(run.id),
        "dataset_version_id": str(version.id),
        "source_parameter_set": _version_payload(parameter_set),
        "calibrated_parameter_set": _version_payload(calibrated_set),
        "model_version_id": str(model_version.id) if model_version else None,
        "status": "CALIBRATED",
        "optimizer": {
            "method": result.optimizer,
            "success": result.optimizer_success,
            "objective": result.objective,
            "message": result.optimizer_message,
        },
        "parameters": result.calibrated_parameters,
        "bounds": {name: list(bound) for name, bound in result.bounds.items()},
        "evaluations": {
            name: {
                "metrics": value.metrics,
                "residual_distribution": value.residual_distribution,
                "measured": value.measured,
                "physics_prediction": value.predicted,
                "residual": value.residuals,
                "observation_count": value.observation_count,
            }
            for name, value in result.evaluations.items()
        },
        "note": "CALIBRATED is not EVALUATED or VALIDATED. Calibration fit uses the chronological first 60%; later windows are reported separately.",
    }


@router.post("/api/v1/models/hybrid/train", status_code=201)
def train_hybrid_model(
    payload: CalibrationRequest,
    context: EngineerContext,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    """Train physics-residual and direct-ML comparators on an imported time-series dataset."""
    from packages.ml import train_residual_model

    version = _get_dataset_version(session, payload.dataset_version_id, context.organization_id)
    parameter_set = session.scalar(
        select(PhysicsParameterSet).where(
            PhysicsParameterSet.id == payload.parameter_set_id,
            PhysicsParameterSet.organization_id == context.organization_id,
        )
    )
    if parameter_set is None:
        _raise(404, "PARAMETER_SET_NOT_FOUND", "Parameter set was not found")
    try:
        parameters = parameter_set_from_values(parameter_set.parameters)
        series = _historical_series(
            session, version, context.organization_id, parameters.density_kg_m3
        )
        simulated = simulate_historical_series(series, parameters)
    except (CalibrationSeriesError, ValueError, RuntimeError) as exc:
        _raise(422, "HYBRID_TRAINING_NOT_POSSIBLE", str(exc))
    measured_product_b = series.measured_concentration_b_mol_m3
    if measured_product_b is None:
        _raise(
            422,
            "HYBRID_TARGET_MISSING",
            "Map reactor.product_b (mol/m³) to compare measured yield against the CSTR physics prediction",
        )
    if np.any(series.feed_concentration_a_mol_m3 <= 0):
        _raise(
            422,
            "HYBRID_TARGET_INVALID",
            "Feed concentration must be positive to calculate measured yield",
        )
    targets = measured_product_b / series.feed_concentration_a_mol_m3
    baseline = simulated["concentration_b_mol_m3"] / series.feed_concentration_a_mol_m3
    features: Any = np.column_stack(
        (
            series.feed_temperature_k,
            series.pressure_pa
            if series.pressure_pa is not None
            else np.full(len(targets), 101_325.0),
            series.feed_flow_m3_s,
            series.feed_concentration_a_mol_m3,
            series.cooling_temperature_k,
            np.divide(
                parameter_set_from_values(parameter_set.parameters).volume_m3,
                series.feed_flow_m3_s,
                out=np.zeros_like(series.feed_flow_m3_s),
                where=series.feed_flow_m3_s > 0,
            ),
        )
    )
    feature_names = (
        "feed_temperature_k",
        "pressure_pa",
        "feed_flow_m3_s",
        "feed_concentration_a_mol_m3",
        "cooling_temperature_k",
        "residence_time_s",
    )
    try:
        result = train_residual_model(features, targets, baseline, feature_names)
    except ValueError as exc:
        _raise(422, "HYBRID_TRAINING_NOT_POSSIBLE", str(exc))

    training = result.split.train_end
    validation = result.split.validation_end
    content_hash = uuid4().hex[:16]
    drift_reference = _residual_reference(series, parameters)
    train_indices = np.arange(result.split.train_end)
    _, training_hybrid, _ = result.model.predict(features[train_indices], baseline[train_indices])
    drift_reference["target:yield_fraction"] = targets[train_indices].astype(float).tolist()
    drift_reference["physics_residual:yield_fraction"] = (
        (targets[train_indices] - baseline[train_indices]).astype(float).tolist()
    )
    drift_reference["hybrid_residual:yield_fraction"] = (
        (targets[train_indices] - training_hybrid).astype(float).tolist()
    )
    drift_reference["prediction_error:yield_fraction"] = (
        (training_hybrid - targets[train_indices]).astype(float).tolist()
    )
    model_version = ModelVersion(
        organization_id=context.organization_id,
        name="CSTR physics residual hybrid",
        version=f"{datetime.now(UTC):%Y%m%d%H%M%S}-{content_hash}",
        model_type="physics_plus_ml_residual",
        status="VALIDATION",
        feature_schema={"names": feature_names, "units": ["K", "Pa", "m3/s", "mol/m3", "K", "s"]},
        target_schema={
            "name": "yield_fraction",
            "unit": "1",
            "source": "measured_product_b/feed_concentration",
        },
        metrics={
            "training_comparisons": {
                name: value.as_dict() for name, value in result.training_comparisons.items()
            },
            "validation_comparisons": {
                name: value.as_dict() for name, value in result.validation_comparisons.items()
            },
            "test_comparisons": {
                name: value.as_dict() for name, value in result.test_comparisons.items()
            },
            "residual_standard_deviation": result.model.residual_standard_deviation,
            "drift_reference": drift_reference,
        },
        dataset_version_id=version.id,
        physics_parameter_set_id=parameter_set.id,
        training_period={
            "start": series.timestamps[0].isoformat(),
            "end": series.timestamps[training - 1].isoformat(),
            "count": training,
        },
        validation_period={
            "start": series.timestamps[training].isoformat(),
            "end": series.timestamps[validation - 1].isoformat(),
            "count": validation - training,
        },
        test_period={
            "start": series.timestamps[validation].isoformat(),
            "end": series.timestamps[-1].isoformat(),
            "count": len(series.timestamps) - validation,
        },
        hyperparameters={
            "algorithm": result.model.algorithm,
            "ordered_split": {"train": 0.6, "validation": 0.2, "test": 0.2},
        },
        operating_envelope={
            name: item.model_dump() for name, item in payload.operating_envelope.items()
        },
        git_sha=_git_sha(),
        created_by=context.user.id,
    )
    path = Path("data/models") / str(context.organization_id) / f"{model_version.id}.joblib"
    result.model.save(path)
    model_version.artifact_path = str(path)
    session.add(model_version)
    session.flush()
    mlflow_run_id, registry_sync = log_model_metadata(
        tracking_uri=get_settings().mlflow_tracking_uri,
        organization_id=str(context.organization_id),
        model_name=model_version.name,
        model_version=model_version.version,
        model_id=str(model_version.id),
        dataset_version_id=str(version.id),
        parameter_set_id=str(parameter_set.id),
        git_sha=model_version.git_sha or "unknown",
        parameters=model_version.hyperparameters,
        metrics=model_version.metrics,
    )
    model_version.mlflow_run_id = mlflow_run_id
    model_version.metrics = {**model_version.metrics, "mlflow_registry_sync": registry_sync}
    append_audit(
        session,
        context.organization_id,
        "TRAIN_HYBRID_MODEL",
        f"model:{model_version.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={"dataset_version_id": str(version.id), "parameter_set_id": str(parameter_set.id)},
    )
    session.commit()
    return {
        "model_version_id": str(model_version.id),
        "status": model_version.status,
        "dataset_version_id": str(version.id),
        "parameter_set_id": str(parameter_set.id),
        "training_period": model_version.training_period,
        "validation_period": model_version.validation_period,
        "test_period": model_version.test_period,
        "training_comparisons": model_version.metrics["training_comparisons"],
        "validation_comparisons": model_version.metrics["validation_comparisons"],
        "test_comparisons": model_version.metrics["test_comparisons"],
        "note": "Metrics are computed from this imported dataset's chronological partitions; this is not a claim of plant validation.",
    }


@router.post("/api/v1/models/{model_id}/evaluate", status_code=201)
def evaluate_model(
    model_id: UUID,
    payload: EvaluationRequest,
    context: EngineerContext,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    model = session.scalar(
        select(ModelVersion).where(
            ModelVersion.id == model_id,
            ModelVersion.organization_id == context.organization_id,
        )
    )
    if model is None:
        _raise(404, "MODEL_NOT_FOUND", "Model version was not found")
    if model.status in {"PRODUCTION", "RETIRED"}:
        _raise(
            409, "MODEL_VERSION_IMMUTABLE", "Production and retired model versions are immutable"
        )
    version = _get_dataset_version(session, payload.dataset_version_id, context.organization_id)
    if model.dataset_version_id == version.id:
        _raise(
            422,
            "INDEPENDENT_DATASET_REQUIRED",
            "Evaluation requires a dataset version independent of the model's training dataset",
        )
    if model.dataset_version_id is not None:
        training_version = session.scalar(
            select(DatasetVersion).where(
                DatasetVersion.id == model.dataset_version_id,
                DatasetVersion.organization_id == context.organization_id,
            )
        )
        if (
            training_version is not None
            and training_version.checksum_sha256 == version.checksum_sha256
        ):
            _raise(
                422,
                "INDEPENDENT_DATASET_REQUIRED",
                "Evaluation data has the same content checksum as the model's training dataset",
            )
    parameter_set = (
        session.get(PhysicsParameterSet, model.physics_parameter_set_id)
        if model.physics_parameter_set_id
        else None
    )
    if parameter_set is None or parameter_set.organization_id != context.organization_id:
        _raise(
            422,
            "PHYSICS_PARAMETERS_UNAVAILABLE",
            "Model has no tenant-scoped physics parameter set",
        )
    try:
        parameters = parameter_set_from_values(parameter_set.parameters)
        series = _historical_series(
            session, version, context.organization_id, parameters.density_kg_m3
        )
        outside = _envelope_outside(series, model.operating_envelope, parameters.density_kg_m3)
    except (CalibrationSeriesError, ValueError) as exc:
        _raise(422, "EVALUATION_NOT_POSSIBLE", str(exc))
    if outside:
        evaluation = ModelEvaluation(
            organization_id=context.organization_id,
            model_version_id=model.id,
            dataset_version_id=version.id,
            user_id=context.user.id,
            evaluation_type="PHYSICS_ONLY" if model.model_type == "physics_cstr" else "HYBRID",
            metrics={},
            residual_distribution={},
            comparisons=[],
            observation_count=len(series.timestamps),
            status="OUTSIDE_VALIDATED_MODEL_RANGE",
        )
        session.add(evaluation)
        session.commit()
        return {
            "evaluation_id": str(evaluation.id),
            "status": "OUTSIDE VALIDATED MODEL RANGE",
            "outside_counts": outside,
            "predictions": None,
            "note": "Predictions were not generated; no extrapolation was performed.",
        }
    try:
        if model.model_type == "physics_cstr":
            result = evaluate_cstr(series, parameters)
            comparisons = [{"name": "physics_only", "metrics": result.metrics}]
            measured = result.measured
            predicted = result.predicted
            residuals = result.residuals
            distributions = result.residual_distribution
        elif model.artifact_path:
            from packages.ml.pipeline import HybridResidualModel

            artifact = HybridResidualModel.load(Path(model.artifact_path))
            simulated = simulate_historical_series(series, parameters)
            targets = series.measured_concentration_b_mol_m3
            if targets is None:
                _raise(
                    422,
                    "HYBRID_TARGET_MISSING",
                    "Evaluation data must include mapped reactor.product_b",
                )
            physics = simulated["concentration_b_mol_m3"] / series.feed_concentration_a_mol_m3
            features = np.column_stack(
                (
                    series.feed_temperature_k,
                    series.pressure_pa
                    if series.pressure_pa is not None
                    else np.full(len(targets), 101_325.0),
                    series.feed_flow_m3_s,
                    series.feed_concentration_a_mol_m3,
                    series.cooling_temperature_k,
                    parameters.volume_m3 / np.maximum(series.feed_flow_m3_s, 1e-12),
                )
            )
            _, hybrid, _ = artifact.predict(features, physics)
            from packages.ml.pipeline import metrics as score

            physics_metrics = score(targets / series.feed_concentration_a_mol_m3, physics).as_dict()
            hybrid_metrics = score(targets / series.feed_concentration_a_mol_m3, hybrid).as_dict()
            target_values = (targets / series.feed_concentration_a_mol_m3).tolist()
            measured = {"yield_fraction": target_values}
            predicted = {
                "physics_only": physics.tolist(),
                "physics_plus_ml_residual": hybrid.tolist(),
            }
            residuals = {
                "physics_only": (physics - np.asarray(target_values)).tolist(),
                "physics_plus_ml_residual": (hybrid - np.asarray(target_values)).tolist(),
            }
            comparisons = [
                {"name": "physics_only", "metrics": physics_metrics},
                {"name": "physics_plus_ml_residual", "metrics": hybrid_metrics},
            ]
            distributions = {
                "physics_only": _residual_distribution(np.asarray(residuals["physics_only"])),
                "physics_plus_ml_residual": _residual_distribution(
                    np.asarray(residuals["physics_plus_ml_residual"])
                ),
            }
            result_metrics = {
                "physics_only": physics_metrics,
                "physics_plus_ml_residual": hybrid_metrics,
            }
        else:
            _raise(422, "MODEL_ARTIFACT_UNAVAILABLE", "Model artifact is unavailable")
    except (ValueError, RuntimeError, OSError) as exc:
        _raise(422, "EVALUATION_FAILED", str(exc))
    if model.model_type == "physics_cstr":
        result_metrics = result.metrics
    evaluation = ModelEvaluation(
        organization_id=context.organization_id,
        model_version_id=model.id,
        dataset_version_id=version.id,
        user_id=context.user.id,
        evaluation_type="PHYSICS_ONLY" if model.model_type == "physics_cstr" else "HYBRID",
        metrics=result_metrics,
        residual_distribution=distributions,
        comparisons=comparisons,
        observation_count=len(series.timestamps),
        status="EVALUATED",
    )
    session.add(evaluation)
    if model.status != "PRODUCTION":
        if model.status in {"CALIBRATED", "VALIDATION"}:
            model.status = "EVALUATED"
        model.metrics = {
            **model.metrics,
            "last_independent_evaluation_id": str(evaluation.id),
            "last_evaluation_metrics": result_metrics,
        }
    append_audit(
        session,
        context.organization_id,
        "EVALUATE_MODEL",
        f"model:{model.id}:evaluation:{evaluation.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={
            "dataset_version_id": str(version.id),
            "observation_count": evaluation.observation_count,
        },
    )
    session.commit()
    return {
        "evaluation_id": str(evaluation.id),
        "model_version_id": str(model.id),
        "dataset_version_id": str(version.id),
        "status": evaluation.status,
        "metrics": result_metrics,
        "residual_distribution": distributions,
        "comparisons": comparisons,
        "measured": measured,
        "physics_prediction": predicted,
        "residual": residuals,
        "note": "Evaluation is an independent measurement comparison, not automatic validation or certification.",
    }


def _residual_distribution(values: NDArray[np.float64]) -> dict[str, float]:
    return {
        "mean": float(np.mean(values)),
        "standard_deviation": float(np.std(values, ddof=1)) if len(values) > 1 else 0.0,
        "p05": float(np.quantile(values, 0.05)),
        "p50": float(np.quantile(values, 0.5)),
        "p95": float(np.quantile(values, 0.95)),
    }


@router.post("/api/v1/models/{model_id}/validate")
def validate_model(
    model_id: UUID,
    payload: ValidationRequest,
    context: AdminContext,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    model = session.scalar(
        select(ModelVersion).where(
            ModelVersion.id == model_id,
            ModelVersion.organization_id == context.organization_id,
        )
    )
    if model is None:
        _raise(404, "MODEL_NOT_FOUND", "Model version was not found")
    if model.status in {"PRODUCTION", "RETIRED"}:
        _raise(
            409, "MODEL_VERSION_IMMUTABLE", "Production and retired model versions are immutable"
        )
    evaluation = session.scalar(
        select(ModelEvaluation).where(
            ModelEvaluation.id == payload.evaluation_id,
            ModelEvaluation.model_version_id == model.id,
            ModelEvaluation.organization_id == context.organization_id,
            ModelEvaluation.status == "EVALUATED",
        )
    )
    if evaluation is None or evaluation.observation_count < 10:
        _raise(
            422,
            "INDEPENDENT_EVALUATION_REQUIRED",
            "Validation requires at least 10 measured observations from a successful independent evaluation",
        )
    failed: list[str] = []
    for signal, limits in payload.acceptance_limits.items():
        actual = evaluation.metrics.get(signal)
        if not isinstance(actual, dict):
            failed.append(f"{signal}:not_evaluated")
            continue
        for metric_name, thresholds in limits.items():
            value = actual.get(metric_name)
            if not isinstance(value, (float, int)):
                failed.append(f"{signal}.{metric_name}:limit_not_met")
                continue
            minimum = thresholds.get("minimum")
            maximum = thresholds.get("maximum")
            if minimum is None and maximum is None:
                failed.append(f"{signal}.{metric_name}:missing_acceptance_bound")
            elif minimum is not None and float(value) < minimum:
                failed.append(f"{signal}.{metric_name}:below_minimum")
            elif maximum is not None and float(value) > maximum:
                failed.append(f"{signal}.{metric_name}:above_maximum")
    if failed:
        _raise(
            422, "VALIDATION_CRITERIA_NOT_MET", f"Acceptance criteria failed: {', '.join(failed)}"
        )
    if not model.operating_envelope:
        _raise(
            422,
            "VALIDATION_ENVELOPE_REQUIRED",
            "Configure and review the model operating envelope before validation",
        )
    model.status = "VALIDATED"
    model.metrics = {
        **model.metrics,
        "validation_review": {
            "reviewer_id": str(context.user.id),
            "reviewed_at": datetime.now(UTC).isoformat(),
            "evaluation_id": str(evaluation.id),
            "acceptance_limits": payload.acceptance_limits,
            "review_note": payload.review_note,
        },
    }
    append_audit(
        session,
        context.organization_id,
        "VALIDATE_MODEL_VERSION",
        f"model:{model.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={"evaluation_id": str(evaluation.id)},
    )
    session.commit()
    return {
        "model_version_id": str(model.id),
        "status": model.status,
        "evaluation_id": str(evaluation.id),
        "operating_envelope": model.operating_envelope,
    }


@router.post("/api/v1/models/{model_id}/drift")
def monitor_model_drift(
    model_id: UUID,
    payload: DriftRequest,
    context: EngineerContext,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    model = session.scalar(
        select(ModelVersion).where(
            ModelVersion.id == model_id,
            ModelVersion.organization_id == context.organization_id,
        )
    )
    if model is None:
        _raise(404, "MODEL_NOT_FOUND", "Model version was not found")
    reference = model.metrics.get("drift_reference")
    if not isinstance(reference, dict) or not reference:
        _raise(
            422,
            "DRIFT_REFERENCE_UNAVAILABLE",
            "Model has no recorded training reference distributions",
        )
    version = _get_dataset_version(session, payload.dataset_version_id, context.organization_id)
    try:
        parameter_set = (
            session.get(PhysicsParameterSet, model.physics_parameter_set_id)
            if model.physics_parameter_set_id
            else None
        )
        if parameter_set is None or parameter_set.organization_id != context.organization_id:
            _raise(
                422, "PHYSICS_PARAMETERS_UNAVAILABLE", "Model physics parameter set is unavailable"
            )
        parameters = parameter_set_from_values(parameter_set.parameters)
        series = _historical_series(
            session, version, context.organization_id, parameters.density_kg_m3
        )
        predicted = simulate_historical_series(series, parameters)
        current = {
            "feature:feed_temperature_k": series.feed_temperature_k,
            "feature:pressure_pa": series.pressure_pa
            if series.pressure_pa is not None
            else np.full(len(series.timestamps), 101_325.0),
            "feature:feed_flow_m3_s": series.feed_flow_m3_s,
            "feature:feed_concentration_a_mol_m3": series.feed_concentration_a_mol_m3,
            "feature:cooling_temperature_k": series.cooling_temperature_k,
            "target:temperature_k": series.measured_temperature_k,
            "physics_residual:temperature_k": series.measured_temperature_k
            - predicted["temperature_k"],
            "prediction_error:temperature_k": predicted["temperature_k"]
            - series.measured_temperature_k,
        }
        if model.model_type != "physics_cstr" and model.artifact_path:
            from packages.ml.pipeline import HybridResidualModel

            artifact = HybridResidualModel.load(Path(model.artifact_path))
            actual = series.measured_concentration_b_mol_m3
            if actual is not None:
                physics_yield = (
                    predicted["concentration_b_mol_m3"] / series.feed_concentration_a_mol_m3
                )
                features = np.column_stack(
                    (
                        series.feed_temperature_k,
                        current["feature:pressure_pa"],
                        series.feed_flow_m3_s,
                        series.feed_concentration_a_mol_m3,
                        series.cooling_temperature_k,
                        parameters.volume_m3 / np.maximum(series.feed_flow_m3_s, 1e-12),
                    )
                )
                _, hybrid, _ = artifact.predict(features, physics_yield)
                actual_yield = actual / series.feed_concentration_a_mol_m3
                current["target:yield_fraction"] = actual_yield
                current["physics_residual:yield_fraction"] = actual_yield - physics_yield
                current["physics_prediction:yield_fraction"] = physics_yield
                current["hybrid_residual:yield_fraction"] = actual_yield - hybrid
                current["prediction_error:yield_fraction"] = hybrid - actual_yield
        expected = set(reference) & set(current)
        if not expected:
            _raise(
                422,
                "DRIFT_SIGNALS_UNAVAILABLE",
                "No configured reference signals are available from this dataset",
            )
        outcome = compare_drift(
            {name: np.asarray(reference[name], dtype=float) for name in expected},
            {name: np.asarray(current[name], dtype=float) for name in expected},
            psi_threshold=payload.psi_threshold,
            ks_pvalue_threshold=payload.ks_pvalue_threshold,
            js_threshold=payload.js_threshold,
        )
    except (CalibrationSeriesError, ValueError, RuntimeError, OSError) as exc:
        _raise(422, "DRIFT_MONITORING_FAILED", str(exc))
    event = ModelDriftEvent(
        organization_id=context.organization_id,
        model_version_id=model.id,
        dataset_version_id=version.id,
        status=outcome.status,
        metrics=outcome.metrics,
        reasons=list(outcome.reasons),
    )
    session.add(event)
    append_audit(
        session,
        context.organization_id,
        "MONITOR_MODEL_DRIFT",
        f"model:{model.id}:drift:{event.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={"status": outcome.status, "dataset_version_id": str(version.id)},
    )
    session.commit()
    return {
        "event_id": str(event.id),
        "model_version_id": str(model.id),
        "dataset_version_id": str(version.id),
        "status": outcome.status,
        "metrics": outcome.metrics,
        "reasons": outcome.reasons,
        "action": "No automatic retraining or production model changes were performed.",
    }
