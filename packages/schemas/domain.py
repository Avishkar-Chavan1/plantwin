from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Role(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    ENGINEER = "ENGINEER"
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"


class QualityStatus(StrEnum):
    GOOD = "GOOD"
    SUSPECT = "SUSPECT"
    BAD = "BAD"
    MISSING = "MISSING"


class MeasurementSource(StrEnum):
    MEASURED = "MEASURED"
    ESTIMATED = "ESTIMATED"
    SIMULATED = "SIMULATED"
    PREDICTED = "PREDICTED"


class TwinStateValue(BaseModel):
    """A value and enough provenance to avoid presenting prediction as measurement."""

    model_config = ConfigDict(extra="forbid")

    value: float
    unit: str
    source: MeasurementSource
    timestamp: datetime
    quality_status: QualityStatus = QualityStatus.GOOD
    uncertainty_lower: float | None = None
    uncertainty_upper: float | None = None


class SensorReadingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sensor_id: UUID
    timestamp: datetime
    value: float = Field(allow_inf_nan=False)
    unit: str
