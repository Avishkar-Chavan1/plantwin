from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
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


class PlantTag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag: str = Field(min_length=1, max_length=128)
    canonical_name: str = Field(min_length=1, max_length=128)
    unit: str | None = None
    description: str | None = None


class TagMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_tag: str = Field(min_length=1, max_length=128)
    canonical_name: str = Field(min_length=1, max_length=128)
    unit: str = Field(min_length=1, max_length=32)
    original_unit: str | None = None
    note: str | None = None


class DataSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str = Field(min_length=1, max_length=200)
    source_type: Literal["CSV", "PARQUET", "REST", "DATABASE", "MQTT", "OPCUA"]
    endpoint: str | None = None
    read_only: bool = True
    description: str | None = None


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    source_id: UUID | None = None
    tags: list[PlantTag] = Field(default_factory=list)
    mappings: list[TagMapping] = Field(default_factory=list)
    row_count: int = 0


class DatasetVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_id: UUID
    version: int = Field(ge=1)
    status: Literal["DRAFT", "VALIDATED", "ACTIVE"] = "DRAFT"
    row_count: int = 0
    created_at: datetime
    source_uri: str | None = None
