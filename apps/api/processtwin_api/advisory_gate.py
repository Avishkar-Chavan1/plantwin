"""Fail-closed gate deciding whether the platform may emit an advisory recommendation.

A recommendation is advice to a human engineer, never a command. It must not be produced from
data or models that cannot support it. When any condition below holds, the answer is
NO RECOMMENDATION together with the explicit reasons, so the engineer can see *why*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import DataSource, TwinState
from .time_utils import as_utc

#: Default staleness limit when no connector configures one: twice the platform minimum.
DEFAULT_LIVE_STALE_AFTER_S = 120.0
#: Simulated telemetry is sampled every few minutes; allow a few missed samples.
SIMULATION_STALE_AFTER_S = 1_800.0
#: Largest tolerated predicted-yield uncertainty (fraction of yield) when it is quantified.
MAX_YIELD_UNCERTAINTY = 0.15

BLOCKING_PREDICTION_STATUSES = {
    "WAITING_FOR_REQUIRED_INPUTS": "Required model inputs are missing or have no data",
    "BLOCKED_MIXED_SOURCES": "Inputs come from mixed live/historical/simulated sources",
    "PREDICTION_ERROR": "The model could not produce a prediction from the current inputs",
    "OUTSIDE_VALIDATED_MODEL_RANGE": "Current operating point is outside the validated model range",
}


@dataclass
class GateResult:
    allowed: bool
    basis: str
    reasons: list[str] = field(default_factory=list)
    #: Conditions that do not block advice but the reviewing engineer must weigh.
    cautions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "RECOMMENDATION_ALLOWED" if self.allowed else "NO_RECOMMENDATION",
            "basis": self.basis,
            "reasons": list(self.reasons),
            "cautions": list(self.cautions),
        }


def _stale_limit_s(session: Session, organization_id: UUID, state: TwinState) -> float:
    limits: list[float] = []
    for source_id in state.source_ids or []:
        try:
            source = session.scalar(
                select(DataSource).where(
                    DataSource.id == UUID(str(source_id)),
                    DataSource.organization_id == organization_id,
                )
            )
        except ValueError:
            continue
        if source is not None and source.configuration.get("stale_after_s"):
            limits.append(2.0 * float(source.configuration["stale_after_s"]))
    return min(limits) if limits else DEFAULT_LIVE_STALE_AFTER_S


def evaluate_recommendation_gate(
    session: Session,
    organization_id: UUID,
    equipment_id: UUID,
    *,
    inside_validated_envelope: bool,
    now: datetime | None = None,
) -> GateResult:
    current = as_utc(now or datetime.now(UTC))
    reasons: list[str] = []
    if not inside_validated_envelope:
        reasons.append(
            "OUTSIDE_VALIDATED_RANGE: the supplied operating point is outside the validated "
            "operating envelope of the selected model"
        )
    state = session.scalar(
        select(TwinState)
        .where(TwinState.organization_id == organization_id, TwinState.equipment_id == equipment_id)
        .order_by(desc(TwinState.timestamp))
        .limit(1)
    )
    if state is None:
        # No synchronized plant data exists: the result is an explicit what-if scenario.
        return GateResult(not reasons, "USER_SUPPLIED_SCENARIO", reasons)

    basis = f"{state.source_mode}_STATE"
    if state.source_mode == "MIXED_DATA_BLOCKED":
        reasons.append("MIXED_SOURCES: live, historical and simulated data are mixed")
    elif state.source_mode == "HISTORICAL":
        reasons.append(
            "HISTORICAL_DATA: the latest state is archival and does not describe the current plant"
        )
    else:
        measurements = state.state.get("measurements", {}) or {}
        ages: list[float] = []
        for entry in measurements.values():
            try:
                ages.append(
                    max(0.0, (current - as_utc(datetime.fromisoformat(entry["timestamp"]))).total_seconds())
                )
            except (KeyError, TypeError, ValueError):
                reasons.append("UNTRUSTED_TIMESTAMP: a measurement has no usable timestamp")
                break
        limit = (
            SIMULATION_STALE_AFTER_S
            if state.source_mode == "SIMULATION"
            else _stale_limit_s(session, organization_id, state)
        )
        if not ages:
            reasons.append("NO_DATA: the latest twin state contains no measurements")
        elif max(ages) > limit:
            reasons.append(
                f"STALE_DATA: oldest input is {max(ages):.0f} s old (limit {limit:.0f} s)"
            )
    blocking = BLOCKING_PREDICTION_STATUSES.get(state.prediction_status)
    if blocking and state.prediction_status != "OUTSIDE_VALIDATED_MODEL_RANGE":
        reasons.append(f"{state.prediction_status}: {blocking}")
    elif blocking:
        reasons.append(f"OUTSIDE_VALIDATED_RANGE: {blocking}")
    quality = state.data_quality or {}
    sensor_count = int(quality.get("sensor_count", 0) or 0)
    good_count = int(quality.get("good_count", 0) or 0)
    cautions: list[str] = []
    if sensor_count and good_count == 0:
        reasons.append(f"BAD_DATA_QUALITY: none of the {sensor_count} inputs is GOOD quality")
    elif sensor_count and good_count < sensor_count:
        # BAD samples never enter the twin (they age out and trip the staleness check above);
        # SUSPECT ones (e.g. a frozen or noisy signal) are usable but must be visible.
        cautions.append(
            f"SUSPECT_INPUTS: {sensor_count - good_count} of {sensor_count} inputs are flagged "
            "SUSPECT and should be checked against the instruments"
        )
    factors = state.health_factors or {}
    if (factors.get("model_drift") or {}).get("status") == "DRIFT_DETECTED":
        reasons.append("MODEL_DRIFT: a drift event is recorded for the active model")
    uncertainty = (state.uncertainty or {}).get("yield_fraction")
    if isinstance(uncertainty, (int, float)) and uncertainty > MAX_YIELD_UNCERTAINTY:
        reasons.append(
            f"UNCERTAINTY_TOO_HIGH: predicted yield uncertainty {uncertainty:.2f} exceeds "
            f"{MAX_YIELD_UNCERTAINTY:.2f}"
        )
    return GateResult(not reasons, basis, reasons, cautions)
