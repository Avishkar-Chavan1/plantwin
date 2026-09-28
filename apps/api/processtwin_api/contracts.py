from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("email")
    @classmethod
    def valid_email_shape(cls, value: str) -> str:
        value = value.lower().strip()
        if value.count("@") != 1 or value.startswith("@") or value.endswith("@"):
            raise ValueError("Invalid email address")
        return value


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=20, max_length=4096)


class ReadingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sensor_id: UUID
    timestamp: datetime
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1, max_length=32)


class SimulationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment_id: UUID
    temperature_c: float = Field(ge=100, le=300)
    pressure_bar: float = Field(ge=0.1, le=100)
    flow_m3_h: float = Field(ge=0, le=10000)
    feed_concentration_mol_m3: float = Field(default=100.0, ge=0, le=10000)
    cooling_temperature_c: float = Field(default=20.0, ge=-50, le=150)
    duration_s: float = Field(default=3600, gt=0, le=86_400)


class OptimizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    equipment_id: UUID
    temperature_c: float = Field(default=180.0, ge=170, le=190)
    pressure_bar: float = Field(default=10.0, ge=8, le=12)
    flow_m3_h: float = Field(default=72.0, ge=57.6, le=86.4)
    feed_concentration_mol_m3: float = Field(default=100.0, ge=0)
    cooling_temperature_c: float = Field(default=20.0)
    energy_weight: float = Field(default=0.02, ge=0, le=1)
