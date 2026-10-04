from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import numpy as np
from packages.ml import train_residual_model
from packages.physics import CSTRPhysicsModel
from packages.units import to_si
from sqlalchemy import select

from apps.api.processtwin_api.config import get_settings
from apps.api.processtwin_api.database import Base, SessionLocal, engine
from apps.api.processtwin_api.models import (
    ModelVersion,
    Organization,
    QualityStatusName,
    Sensor,
    SensorReading,
    TrainingRun,
)

FEATURE_TAGS = (
    "REACTOR_TEMPERATURE",
    "REACTOR_PRESSURE",
    "FEED_FLOW",
    "FEED_CONCENTRATION",
    "COOLING_FLOW",
    "AGITATOR_SPEED",
)
FEATURE_NAMES = (
    "temperature_c",
    "pressure_bar",
    "flow_m3_h",
    "feed_concentration_mol_m3",
    "cooling_flow_m3_h",
    "agitation_rpm",
    "residence_time_s",
)


def physics_yield(temperature_c: float, flow_m3_h: float, feed_concentration: float) -> float:
    """Measured temperature first-order CSTR baseline; residual model learns plant/model discrepancy."""
    model = CSTRPhysicsModel()
    k_main, k_side = model.rate_constants(to_si(temperature_c, "degC"))
    tau = model.parameters.volume_m3 / to_si(flow_m3_h, "m3/h")
    concentration_a = feed_concentration / (1 + (k_main + k_side) * tau)
    return k_main * concentration_a * tau / feed_concentration if feed_concentration else 0.0


def train_all(organization_id: UUID | None = None) -> int:
    if get_settings().auto_create_schema:
        Base.metadata.create_all(bind=engine)
    trained = 0
    with SessionLocal() as session:
        organizations = (
            select(Organization)
            if organization_id is None
            else select(Organization).where(Organization.id == organization_id)
        )
        for organization in session.scalars(organizations):
            sensors = {
                sensor.tag: sensor.id
                for sensor in session.scalars(
                    select(Sensor).where(Sensor.organization_id == organization.id)
                )
            }
            needed = set(FEATURE_TAGS) | {"YIELD"}
            if not needed.issubset(sensors):
                continue
            by_time: dict[datetime, dict[str, float]] = defaultdict(dict)
            rows = session.execute(
                select(SensorReading.timestamp, Sensor.tag, SensorReading.value)
                .join(Sensor, Sensor.id == SensorReading.sensor_id)
                .where(
                    SensorReading.organization_id == organization.id,
                    SensorReading.quality_status == QualityStatusName.GOOD,
                    Sensor.tag.in_(needed),
                )
                .order_by(SensorReading.timestamp)
            ).all()
            for timestamp, tag, value in rows:
                by_time[timestamp][tag] = value
            feature_rows: list[list[float]] = []
            targets: list[float] = []
            baselines: list[float] = []
            for _, values in sorted(by_time.items(), key=lambda item: item[0]):
                if not needed.issubset(values):
                    continue
                flow = values["FEED_FLOW"]
                if flow <= 0:
                    continue
                feature_rows.append(
                    [
                        values["REACTOR_TEMPERATURE"],
                        values["REACTOR_PRESSURE"],
                        flow,
                        values["FEED_CONCENTRATION"],
                        values["COOLING_FLOW"],
                        values["AGITATOR_SPEED"],
                        5.0 / to_si(flow, "m3/h"),
                    ]
                )
                targets.append(values["YIELD"] / 100)
                baselines.append(
                    physics_yield(values["REACTOR_TEMPERATURE"], flow, values["FEED_CONCENTRATION"])
                )
            if len(feature_rows) < 10:
                continue
            result = train_residual_model(
                np.array(feature_rows), np.array(targets), np.array(baselines), FEATURE_NAMES
            )
            version = f"yield-residual-{trained + 1}"
            path = Path("data/models") / f"{organization.id}-{version}.joblib"
            result.model.save(path)
            raw_feature_importance = cast(
                object,
                getattr(
                    result.model.residual_model,
                    "feature_importances_",
                    np.zeros(len(FEATURE_NAMES)),
                ),
            )
            feature_importance = np.asarray(raw_feature_importance, dtype=float).reshape(-1)
            record = ModelVersion(
                organization_id=organization.id,
                name="CSTR Yield Hybrid",
                version=version,
                model_type="physics_plus_gradient_boosting_residual",
                status="VALIDATION",
                feature_schema={"names": FEATURE_NAMES},
                target_schema={"name": "yield_fraction"},
                metrics={
                    "validation": result.validation_metrics.as_dict(),
                    "test": result.test_metrics.as_dict(),
                    "split": {
                        "train": result.split.train_end,
                        "validation": result.split.validation_end - result.split.train_end,
                        "test": result.split.total - result.split.validation_end,
                    },
                    "feature_importance": dict(
                        zip(
                            FEATURE_NAMES, [float(item) for item in feature_importance], strict=True
                        )
                    ),
                },
                artifact_path=str(path),
            )
            session.add(record)
            session.flush()
            session.add(
                TrainingRun(
                    organization_id=organization.id,
                    model_version_id=record.id,
                    dataset_description=f"{len(feature_rows)} GOOD time-aligned simulated observations; ordered 60/20/20 split",
                    metrics={
                        "validation": result.validation_metrics.as_dict(),
                        "test": result.test_metrics.as_dict(),
                    },
                    status="COMPLETED",
                )
            )
            trained += 1
        session.commit()
    return trained


if __name__ == "__main__":
    print(f"Trained {train_all()} model version(s); no model was promoted automatically.")
