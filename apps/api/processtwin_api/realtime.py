from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime
from statistics import median
from typing import Any
from uuid import UUID

import numpy as np
from connectors.telemetry import TelemetryMessage
from packages.physics import CSTRInputs, CSTRParameters, CSTRPhysicsModel
from packages.units import convert, si_unit, to_si
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    Alert,
    AlertSeverity,
    DataSource,
    ModelDriftEvent,
    ModelVersion,
    PhysicsParameterSet,
    PotentialAnomaly,
    QualityEvent,
    QualityStatusName,
    Sensor,
    SensorReading,
    SourceTagMapping,
    TwinState,
)
from .quality import DataQualityService, QualityDecision
from .time_utils import as_utc

logger = logging.getLogger(__name__)


def parameter_set_from_values(values: dict[str, Any]) -> CSTRParameters:
    field_map = {
        "k0_main_s": "pre_exponential_factor_s",
        "Ea_main_j_mol": "activation_energy_j_mol",
        "deltaH_main_j_mol": "reaction_enthalpy_j_mol",
        "k0_side_s": "side_pre_exponential_factor_s",
        "Ea_side_j_mol": "side_activation_energy_j_mol",
        "deltaH_side_j_mol": "side_reaction_enthalpy_j_mol",
        "U_w_m2_k": "heat_transfer_coefficient_w_m2_k",
        "A_m2": "heat_transfer_area_m2",
        "V_m3": "volume_m3",
        "rho_kg_m3": "density_kg_m3",
        "Cp_j_kg_k": "heat_capacity_j_kg_k",
    }
    changes = {
        field_map[name]: float(record["value"] if isinstance(record, dict) else record)
        for name, record in values.items()
        if name in field_map
    }
    return CSTRParameters(**changes)


def _model_in_envelope(
    model: ModelVersion | None, inputs: CSTRInputs, density: float
) -> bool | None:
    if model is None or not model.operating_envelope:
        return None
    values = {
        "temperature_k": inputs.feed_temperature_k,
        "temperature_c": convert(inputs.feed_temperature_k, "K", "degC"),
        "pressure_pa": inputs.pressure_pa,
        "pressure_bar": convert(inputs.pressure_pa, "Pa", "bar"),
        "feed_flow_m3_s": inputs.feed_flow_m3_s,
        "feed_flow_kg_s": inputs.feed_flow_m3_s * density,
        "feed_flow_kg_h": inputs.feed_flow_m3_s * density * 3600.0,
    }
    return all(
        name in values and float(bounds["minimum"]) <= values[name] <= float(bounds["maximum"])
        for name, bounds in model.operating_envelope.items()
    )


def source_mode(source: str) -> str:
    if source == "SIMULATED":
        return "SIMULATION"
    if source in {"MQTT", "OPCUA"}:
        return "LIVE_READ_ONLY"
    return "HISTORICAL"


def _health_factors(
    *,
    ages: list[float],
    good_fraction: float,
    missing: list[str],
    residual_k: float | None,
    ml_residual: float | None,
    envelope: bool | None,
    drift: bool,
    stale_after_s: float,
) -> tuple[str, float | None, dict[str, Any]]:
    factors: dict[str, dict[str, Any]] = {}
    if ages:
        freshness = max(ages)
        factors["freshness"] = {
            "value": freshness,
            "unit": "s",
            "score": max(0.0, 1.0 - freshness / (4 * stale_after_s)),
            "status": "PASS" if freshness <= stale_after_s else "STALE",
        }
    factors["sensor_quality"] = {
        "value": good_fraction,
        "unit": "fraction",
        "score": good_fraction,
        "status": "PASS" if good_fraction >= 0.95 else "DEGRADED",
    }
    factors["missing_data"] = {
        "value": missing,
        "score": 1.0 if not missing else 0.0,
        "status": "PASS" if not missing else "MISSING",
    }
    if residual_k is not None:
        factors["physics_residual"] = {
            "value": residual_k,
            "unit": "K",
            "score": max(0.0, 1.0 - residual_k / 15.0),
            "status": "PASS" if residual_k <= 5.0 else "DEGRADED",
        }
    if ml_residual is not None:
        factors["hybrid_residual"] = {
            "value": ml_residual,
            "unit": "yield_fraction",
            "tolerance": 0.1,
            "score": max(0.0, 1.0 - ml_residual / 0.3),
            "status": "PASS" if ml_residual <= 0.1 else "DEGRADED",
        }
    if envelope is not None:
        factors["operating_envelope"] = {
            "value": envelope,
            "score": 1.0 if envelope else 0.0,
            "status": "PASS" if envelope else "OUTSIDE_RANGE",
        }
    factors["model_drift"] = {
        "value": drift,
        "score": 0.0 if drift else 1.0,
        "status": "DRIFT_DETECTED" if drift else "NO_DRIFT_RECORDED",
    }
    if not ages or missing or len(factors) < 4:
        return "INSUFFICIENT_EVIDENCE", None, factors
    score = sum(float(factor["score"]) for factor in factors.values()) / len(factors) * 100
    all_pass = all(factor["status"] in {"PASS", "NO_DRIFT_RECORDED"} for factor in factors.values())
    return ("HEALTHY" if score >= 85 and all_pass else "DEGRADED"), score, factors


class LiveIngestionGateway:
    """Validate, retain and synchronize read-only measurements into tenant twin state."""

    def ingest(
        self,
        session: Session,
        source: DataSource,
        message: TelemetryMessage,
    ) -> dict[str, Any]:
        if not source.read_only:
            raise ValueError(
                "ProcessTwin sources are read-only; control/write operations are prohibited"
            )
        mapping = session.scalar(
            select(SourceTagMapping).where(
                SourceTagMapping.organization_id == source.organization_id,
                SourceTagMapping.data_source_id == source.id,
                SourceTagMapping.source_key == message.source_key,
            )
        )
        if mapping is None:
            raise ValueError("Source key is not mapped to a tenant sensor")
        sensor = session.scalar(
            select(Sensor).where(
                Sensor.id == mapping.sensor_id,
                Sensor.organization_id == source.organization_id,
                Sensor.enabled.is_(True),
            )
        )
        if sensor is None:
            raise ValueError("Mapped sensor is disabled or outside the source tenant")
        equipment = sensor.equipment
        if source.plant_id is not None and equipment.plant_id != source.plant_id:
            raise ValueError("Mapped sensor does not belong to the source plant")
        if message.timestamp.tzinfo is None or message.timestamp.utcoffset() is None:
            raise ValueError("Telemetry timestamp must include a timezone")
        timestamp = as_utc(message.timestamp)
        engineering_value = convert(float(message.value), message.unit, sensor.unit)
        decision = DataQualityService().assess(session, sensor, timestamp, engineering_value)
        status = decision.status
        reason = decision.detail or decision.event_type or "ALL_CONFIGURED_CHECKS_PASSED"
        if message.timestamp_source == "RECEIVED" and status is QualityStatusName.GOOD:
            status = QualityStatusName.SUSPECT
            reason = "SOURCE_TIMESTAMP_MISSING; RECEIVED_AT_USED"
        duplicate = session.scalar(
            select(SensorReading.id).where(
                SensorReading.organization_id == source.organization_id,
                SensorReading.sensor_id == sensor.id,
                SensorReading.timestamp == timestamp,
            )
        )
        if duplicate is not None:
            session.add(
                QualityEvent(
                    organization_id=source.organization_id,
                    sensor_id=sensor.id,
                    reading_timestamp=timestamp,
                    event_type="DUPLICATE_TIMESTAMP",
                    detail="Duplicate live observation was not inserted",
                )
            )
            source.error_count += 1
            source.last_error = "Duplicate sensor timestamp"
            source.status = "ERROR"
            source.last_message_at = message.received_at
            session.flush()
            return {"accepted": False, "quality_status": "BAD", "reason": "DUPLICATE_TIMESTAMP"}
        DataQualityService.record_event(
            session,
            source.organization_id,
            sensor.id,
            timestamp,
            QualityDecision(status, decision.event_type, decision.detail),
        )
        reading = SensorReading(
            organization_id=source.organization_id,
            sensor_id=sensor.id,
            data_source_id=source.id,
            timestamp=timestamp,
            value=engineering_value,
            unit=sensor.unit,
            original_value=float(message.value),
            original_unit=message.unit,
            normalized_value=to_si(engineering_value, sensor.unit),
            normalized_unit=si_unit(sensor.unit),
            quality_status=status,
            quality_reasons=[reason],
            source=source.source_type,
        )
        session.add(reading)
        try:
            session.flush()
        except IntegrityError as exc:
            raise ValueError("Sensor timestamp already exists") from exc
        now = as_utc(message.received_at)
        source.last_message_at = now
        source.last_success_at = now
        source.freshness_s = 0.0
        source.latency_ms = max(0.0, (now - timestamp).total_seconds() * 1000)
        source.status = "CONNECTED"
        source.last_error = None
        session.flush()
        state, anomaly = self.synchronize(session, equipment.id, source.organization_id, now)
        return {
            "accepted": True,
            "reading_id": str(reading.id),
            "quality_status": status.value,
            "quality_reason": reason,
            "twin_state_id": str(state.id) if state else None,
            "potential_anomaly_id": str(anomaly.id) if anomaly else None,
        }

    def synchronize(
        self,
        session: Session,
        equipment_id: UUID,
        organization_id: UUID,
        now: datetime | None = None,
    ) -> tuple[TwinState | None, PotentialAnomaly | None]:
        current_time = as_utc(now or datetime.now(UTC))
        sensors = list(
            session.scalars(
                select(Sensor).where(
                    Sensor.organization_id == organization_id,
                    Sensor.equipment_id == equipment_id,
                    Sensor.enabled.is_(True),
                )
            )
        )
        mapped = session.execute(
            select(SourceTagMapping, Sensor)
            .join(Sensor, Sensor.id == SourceTagMapping.sensor_id)
            .where(
                SourceTagMapping.organization_id == organization_id,
                Sensor.organization_id == organization_id,
                Sensor.equipment_id == equipment_id,
                Sensor.enabled.is_(True),
            )
        ).all()
        maps_by_sensor: dict[UUID, list[SourceTagMapping]] = defaultdict(list)
        for tag_mapping, sensor in mapped:
            maps_by_sensor[sensor.id].append(tag_mapping)
        canonical: dict[str, tuple[SensorReading, Sensor]] = {}
        modes: set[str] = set()
        source_ids: set[str] = set()
        ages: list[float] = []
        qualities: list[QualityStatusName] = []
        for sensor in sensors:
            latest = session.scalar(
                select(SensorReading)
                .where(
                    SensorReading.organization_id == organization_id,
                    SensorReading.sensor_id == sensor.id,
                    SensorReading.quality_status.in_(
                        (QualityStatusName.GOOD, QualityStatusName.SUSPECT)
                    ),
                )
                .order_by(desc(SensorReading.timestamp))
                .limit(1)
            )
            if latest is None:
                continue
            modes.add(source_mode(latest.source))
            qualities.append(latest.quality_status)
            latest_timestamp = as_utc(latest.timestamp)
            ages.append(max(0.0, (current_time - latest_timestamp).total_seconds()))
            if latest.data_source_id:
                source_ids.add(str(latest.data_source_id))
            names = [tag.canonical_name for tag in maps_by_sensor[sensor.id]] or [
                sensor.measurement_type
            ]
            for name in names:
                previous = canonical.get(name)
                if previous is None or latest_timestamp > as_utc(previous[0].timestamp):
                    canonical[name] = (latest, sensor)
        if not canonical:
            return None, None
        if len(modes) != 1:
            state = TwinState(
                organization_id=organization_id,
                equipment_id=equipment_id,
                timestamp=current_time,
                state={"mode": "MIXED_DATA_BLOCKED", "measurements": {}},
                health_status="MIXED_DATA_BLOCKED",
                source_mode="MIXED_DATA_BLOCKED",
                source_ids=sorted(source_ids),
                data_quality={"status": "BLOCKED", "source_modes": sorted(modes)},
                prediction_status="BLOCKED_MIXED_SOURCES",
                uncertainty={},
                health_factors={"source_mode": {"status": "BLOCKED", "values": sorted(modes)}},
            )
            session.add(state)
            session.flush()
            return state, None
        mode = next(iter(modes))
        required = (
            "reactor.feed_flow",
            "reactor.feed_temperature",
            "reactor.feed_concentration",
            "reactor.cooling_temperature",
        )
        missing = [name for name in required if name not in canonical]
        measured: dict[str, float] = {}
        for name, (reading, _sensor) in canonical.items():
            if reading.normalized_value is not None:
                measured[name] = float(reading.normalized_value)
        quality = {
            "good_count": sum(item is QualityStatusName.GOOD for item in qualities),
            "suspect_count": sum(item is QualityStatusName.SUSPECT for item in qualities),
            "sensor_count": len(qualities),
            "latest_statuses": {
                name: row[0].quality_status.value for name, row in canonical.items()
            },
        }
        validated = session.scalar(
            select(ModelVersion)
            .where(
                ModelVersion.organization_id == organization_id,
                ModelVersion.model_type.in_(("physics_cstr", "physics_plus_ml_residual")),
                ModelVersion.status.in_(("VALIDATED", "PRODUCTION")),
            )
            .order_by(desc(ModelVersion.status == "PRODUCTION"), desc(ModelVersion.created_at))
            .limit(1)
        )
        parameters_record = (
            session.get(PhysicsParameterSet, validated.physics_parameter_set_id)
            if validated and validated.physics_parameter_set_id
            else None
        )
        parameters = (
            parameter_set_from_values(parameters_record.parameters)
            if parameters_record
            else CSTRParameters()
        )
        physics = CSTRPhysicsModel(parameters)
        prediction: dict[str, Any] = {}
        status = "WAITING_FOR_REQUIRED_INPUTS"
        residual: float | None = None
        envelope: bool | None = None
        ml_residual: float | None = None
        uncertainty: dict[str, Any] = {}
        if not missing:
            inputs = CSTRInputs(
                feed_flow_m3_s=measured["reactor.feed_flow"],
                feed_temperature_k=measured["reactor.feed_temperature"],
                feed_concentration_a_mol_m3=measured["reactor.feed_concentration"],
                cooling_temperature_k=measured["reactor.cooling_temperature"],
                pressure_pa=measured.get("reactor.pressure", 101_325.0),
            )
            try:
                steady = physics.steady_state(inputs)
                metrics = physics.metrics(steady, inputs)
                residual = (
                    abs(measured["reactor.temperature"] - steady.temperature_k)
                    if "reactor.temperature" in measured
                    else None
                )
                prediction = {
                    "temperature_k": steady.temperature_k,
                    "conversion": metrics.conversion,
                    "yield": metrics.yield_b,
                    "selectivity": metrics.selectivity_b,
                    "heat_generation_w": metrics.heat_generation_w,
                    "heat_removal_w": metrics.heat_removal_w,
                }
                envelope = _model_in_envelope(validated, inputs, parameters.density_kg_m3)
                status = (
                    "OUTSIDE_VALIDATED_MODEL_RANGE"
                    if envelope is False
                    else ("VALIDATED_PHYSICS" if validated else "UNVALIDATED_PHYSICS_REFERENCE")
                )
                if envelope is False:
                    prediction = {}
                uncertainty = {
                    "temperature_k": None,
                    "reason": "No current validated uncertainty estimate",
                }
                if (
                    validated
                    and validated.model_type == "physics_plus_ml_residual"
                    and validated.artifact_path
                    and envelope is not False
                    and parameters.volume_m3 > 0
                ):
                    from pathlib import Path

                    from packages.ml.pipeline import HybridResidualModel

                    artifact = HybridResidualModel.load(Path(validated.artifact_path))
                    features = np.asarray(
                        [
                            [
                                inputs.feed_temperature_k,
                                inputs.pressure_pa,
                                inputs.feed_flow_m3_s,
                                inputs.feed_concentration_a_mol_m3,
                                inputs.cooling_temperature_k,
                                parameters.volume_m3 / max(inputs.feed_flow_m3_s, 1e-12),
                            ]
                        ],
                        dtype=float,
                    )
                    _, hybrid_yield, interval = artifact.predict(
                        features, np.asarray([metrics.yield_b], dtype=float)
                    )
                    prediction["physics_yield"] = metrics.yield_b
                    prediction["yield"] = float(hybrid_yield[0])
                    ml_residual = abs(float(hybrid_yield[0]) - metrics.yield_b)
                    uncertainty["yield_fraction"] = float(interval[0])
                    status = "VALIDATED_HYBRID"
            except (ValueError, RuntimeError) as exc:
                status = "PREDICTION_ERROR"
                prediction = {"error": type(exc).__name__}
        drift_event = None
        if validated:
            drift_event = session.scalar(
                select(ModelDriftEvent)
                .where(
                    ModelDriftEvent.organization_id == organization_id,
                    ModelDriftEvent.model_version_id == validated.id,
                )
                .order_by(desc(ModelDriftEvent.created_at))
                .limit(1)
            )
        drift = bool(drift_event and drift_event.status == "MODEL_DRIFT_DETECTED")
        sensor_count = int(quality["sensor_count"]) if isinstance(quality["sensor_count"], (int, float, str)) else 1
        good_count_val = quality["good_count"]
        good_fraction = (float(good_count_val) if isinstance(good_count_val, (int, float, str)) else 0.0) / max(1, sensor_count)
        health, score, factors = _health_factors(
            ages=ages,
            good_fraction=good_fraction,
            missing=missing,
            residual_k=residual,
            ml_residual=ml_residual,
            envelope=envelope,
            drift=drift,
            stale_after_s=30.0,
        )
        factors["health_score"] = {
            "value": score,
            "unit": "points",
            "method": "unweighted mean of documented available factors",
        }
        state_values = {
            "mode": mode,
            "measurements": {
                name: {
                    "value": row[0].value,
                    "unit": row[0].unit,
                    "normalized_value": row[0].normalized_value,
                    "normalized_unit": row[0].normalized_unit,
                    "quality_status": row[0].quality_status.value,
                    "timestamp": row[0].timestamp.isoformat(),
                }
                for name, row in canonical.items()
            },
            "physics": prediction,
            "model_version_id": str(validated.id) if validated else None,
            "physics_parameter_set_id": str(parameters_record.id) if parameters_record else None,
            "physics_residual_temperature_k": residual,
            "potential_contributing_variables": [],
        }
        state = TwinState(
            organization_id=organization_id,
            equipment_id=equipment_id,
            timestamp=current_time,
            state=state_values,
            health_status=health,
            source_mode=mode,
            source_ids=sorted(source_ids),
            model_version_id=validated.id if validated else None,
            physics_parameter_set_id=parameters_record.id if parameters_record else None,
            data_quality=quality,
            prediction_status=status,
            uncertainty=uncertainty,
            health_factors=factors,
        )
        session.add(state)
        session.flush()
        anomaly = self._anomaly(
            session, organization_id, equipment_id, state, canonical, residual, envelope
        )
        if anomaly:
            state_values["potential_contributing_variables"] = (
                anomaly.potential_contributing_variables
            )
        return state, anomaly

    def _anomaly(
        self,
        session: Session,
        organization_id: UUID,
        equipment_id: UUID,
        twin_state: TwinState,
        canonical: dict[str, tuple[SensorReading, Sensor]],
        residual_k: float | None,
        envelope: bool | None,
    ) -> PotentialAnomaly | None:
        evidence: dict[str, Any] = {}
        contributors: list[str] = []
        component_scores: list[float] = []
        for name, (reading, sensor) in canonical.items():
            history = list(
                session.scalars(
                    select(SensorReading)
                    .where(
                        SensorReading.organization_id == organization_id,
                        SensorReading.sensor_id == sensor.id,
                        SensorReading.quality_status == QualityStatusName.GOOD,
                        SensorReading.normalized_value.is_not(None),
                    )
                    .order_by(desc(SensorReading.timestamp))
                    .limit(30)
                )
            )
            values = [
                float(item.normalized_value)
                for item in history[1:]
                if item.normalized_value is not None
            ]
            if len(values) >= 7 and reading.normalized_value is not None:
                center = median(values)
                mad = median([abs(value - center) for value in values])
                robust_z = abs(float(reading.normalized_value) - center) / max(
                    1.4826 * mad, abs(center) * 1e-6, 1e-9
                )
                evidence[name] = {"statistical_robust_z": robust_z, "historical_median": center}
                if robust_z >= 3.5:
                    component_scores.append(min(1.0, robust_z / 10.0))
                    contributors.append(name)
        names = sorted(canonical)
        aligned_by_name: dict[str, dict[datetime, float]] = {}
        for name in names:
            _reading, sensor = canonical[name]
            history = list(
                session.scalars(
                    select(SensorReading)
                    .where(
                        SensorReading.organization_id == organization_id,
                        SensorReading.sensor_id == sensor.id,
                        SensorReading.quality_status == QualityStatusName.GOOD,
                        SensorReading.normalized_value.is_not(None),
                    )
                    .order_by(desc(SensorReading.timestamp))
                    .limit(100)
                )
            )
            aligned_by_name[name] = {
                row.timestamp: float(row.normalized_value)
                for row in history
                if row.normalized_value is not None
            }
        aligned_times = (
            set.intersection(*(set(values) for values in aligned_by_name.values()))
            if aligned_by_name
            else set()
        )
        if len(names) >= 2 and len(aligned_times) >= 20:
            try:
                from packages.ml.anomaly import PotentialAnomalyDetector

                ordered_times = sorted(aligned_times)
                baseline = np.asarray(
                    [[aligned_by_name[name][stamp] for name in names] for stamp in ordered_times],
                    dtype=float,
                )
                current = np.asarray(
                    [[float(canonical[name][0].normalized_value or 0.0) for name in names]],
                    dtype=float,
                )
                detector = PotentialAnomalyDetector(contamination=0.05).fit(baseline)
                current_score = float(detector.score(current)[0])
                baseline_scores = detector.score(baseline)
                threshold = float(np.quantile(baseline_scores, 0.95))
                flagged = current_score > threshold
                evidence["ml_isolation_forest"] = {
                    "score": current_score,
                    "training_p95_threshold": threshold,
                    "flagged": flagged,
                    "training_observations": len(ordered_times),
                }
                if flagged:
                    component_scores.append(min(1.0, current_score / max(threshold * 2.0, 1e-9)))
                    deviations = []
                    for column, name in enumerate(names):
                        center = float(np.median(baseline[:, column]))  # type: ignore[index]
                        scale = max(
                            float(np.median(np.abs(baseline[:, column] - center))) * 1.4826, 1e-9  # type: ignore[index]
                        )
                        deviations.append((abs(float(current[0, column]) - center) / scale, name))  # type: ignore[index]
                    contributors.extend(
                        name for _deviation, name in sorted(deviations, reverse=True)[:3]
                    )
            except (ImportError, ValueError, RuntimeError) as exc:
                evidence["ml_isolation_forest"] = {
                    "status": "UNAVAILABLE",
                    "reason": type(exc).__name__,
                }
        if residual_k is not None:
            evidence["physics_residual_temperature_k"] = residual_k
            if residual_k >= 5.0:
                component_scores.append(min(1.0, residual_k / 15.0))
                contributors.append("reactor.temperature (physics residual)")
        if envelope is False:
            evidence["constraint_violation"] = "OUTSIDE_VALIDATED_MODEL_RANGE"
            component_scores.append(1.0)
            contributors.append("operating envelope")
        if not component_scores:
            return None
        score = sum(component_scores) / len(component_scores)
        anomaly = PotentialAnomaly(
            organization_id=organization_id,
            equipment_id=equipment_id,
            twin_state_id=twin_state.id,
            timestamp=twin_state.timestamp,
            score=score,
            evidence=evidence,
            potential_contributing_variables=sorted(set(contributors)),
        )
        session.add(anomaly)
        session.add(
            Alert(
                organization_id=organization_id,
                equipment_id=equipment_id,
                severity=AlertSeverity.WARNING,
                reason=f"POTENTIAL ANOMALY, score {score:.2f}; potential contributing variables: {', '.join(anomaly.potential_contributing_variables)}",
                status="OPEN",
                occurred_at=twin_state.timestamp,
            )
        )
        return anomaly

    @staticmethod
    def _model_in_envelope(
        model: ModelVersion | None, inputs: CSTRInputs, density: float
    ) -> bool | None:
        if model is None or not model.operating_envelope:
            return None
        values = {
            "temperature_k": inputs.feed_temperature_k,
            "temperature_c": convert(inputs.feed_temperature_k, "K", "degC"),
            "pressure_pa": inputs.pressure_pa,
            "pressure_bar": convert(inputs.pressure_pa, "Pa", "bar"),
            "feed_flow_m3_s": inputs.feed_flow_m3_s,
            "feed_flow_kg_s": inputs.feed_flow_m3_s * density,
            "feed_flow_kg_h": inputs.feed_flow_m3_s * density * 3600,
        }
        return all(
            name in values and float(bounds["minimum"]) <= values[name] <= float(bounds["maximum"])
            for name, bounds in model.operating_envelope.items()
        )
