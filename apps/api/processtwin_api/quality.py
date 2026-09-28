"""Data-quality classification. BAD values are recorded as events and rejected from measurements."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import QualityEvent, QualityStatusName, Sensor, SensorReading


@dataclass(frozen=True)
class QualityDecision:
    status: QualityStatusName
    event_type: str | None = None
    detail: str | None = None


class DataQualityService:
    spike_factor: float = 8.0

    def assess(
        self, session: Session, sensor: Sensor, timestamp: datetime, value: float
    ) -> QualityDecision:
        if not isfinite(value):
            return QualityDecision(
                QualityStatusName.BAD, "IMPOSSIBLE_VALUE", "Reading is not finite"
            )
        if sensor.min_valid_value is not None and value < sensor.min_valid_value:
            return QualityDecision(
                QualityStatusName.BAD, "IMPOSSIBLE_VALUE", "Reading is below configured minimum"
            )
        if sensor.max_valid_value is not None and value > sensor.max_valid_value:
            return QualityDecision(
                QualityStatusName.BAD, "IMPOSSIBLE_VALUE", "Reading is above configured maximum"
            )
        previous = list(
            session.scalars(
                select(SensorReading)
                .where(SensorReading.sensor_id == sensor.id)
                .order_by(desc(SensorReading.timestamp))
                .limit(6)
            )
        )
        if previous and timestamp <= previous[0].timestamp:
            return QualityDecision(
                QualityStatusName.BAD,
                "OUT_OF_ORDER",
                "Timestamp is not later than latest accepted reading",
            )
        if len(previous) >= 5 and all(
            abs(reading.value - value) < 1e-12 for reading in previous[:5]
        ):
            return QualityDecision(
                QualityStatusName.SUSPECT,
                "STUCK_VALUE",
                "Five previous readings have the same value",
            )
        if len(previous) >= 3:
            values = [reading.value for reading in previous[:3]]
            mean = sum(values) / len(values)
            spread = max(max(values) - min(values), max(abs(mean) * 0.01, 1e-9))
            if abs(value - mean) > self.spike_factor * spread:
                return QualityDecision(
                    QualityStatusName.SUSPECT,
                    "SENSOR_SPIKE",
                    "Reading is a large deviation from recent history",
                )
        return QualityDecision(QualityStatusName.GOOD)

    @staticmethod
    def record_event(
        session: Session,
        organization_id: UUID,
        sensor_id: UUID,
        timestamp: datetime,
        decision: QualityDecision,
    ) -> None:
        if decision.event_type:
            session.add(
                QualityEvent(
                    organization_id=organization_id,
                    sensor_id=sensor_id,
                    reading_timestamp=timestamp,
                    event_type=decision.event_type,
                    detail=decision.detail or decision.event_type,
                )
            )
