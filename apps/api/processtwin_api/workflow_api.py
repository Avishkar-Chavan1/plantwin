from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select

from .audit import append_audit
from .auth import SessionDependency, TenantContext, require_roles, tenant_context
from .models import (
    Equipment,
    PotentialAnomaly,
    Recommendation,
    RecommendationReview,
    RoleName,
    TwinState,
)
from .time_utils import as_utc

router = APIRouter(tags=["digital-twin-advisory"])
TenantDependency = Annotated[TenantContext, Depends(tenant_context)]
EngineerDependency = Annotated[
    TenantContext, Depends(require_roles(RoleName.OWNER, RoleName.ADMIN, RoleName.ENGINEER))
]


class RecommendationReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["REVIEWED", "ACCEPTED", "REJECTED"]
    comment: str = Field(min_length=5, max_length=2000)


def _raise(code: str, message: str, status: int = 422) -> None:
    raise HTTPException(status, detail={"code": code, "message": message})


def _recommendation_payload(item: Recommendation, now: datetime) -> dict[str, Any]:
    current_time = as_utc(now)
    expires_at = as_utc(item.expires_at) if item.expires_at else None
    state = item.status
    if expires_at and expires_at <= current_time and state == "GENERATED":
        state = "EXPIRED"
    return {
        "id": str(item.id),
        "timestamp": item.created_at,
        "equipment_id": str(item.equipment_id),
        "model_version_id": str(item.model_version_id) if item.model_version_id else None,
        "simulation_id": str(item.simulation_id) if item.simulation_id else None,
        "optimization_run_id": str(item.optimization_run_id) if item.optimization_run_id else None,
        "baseline": item.baseline,
        "proposed_change": item.proposed_change,
        "expected_benefit": item.expected_impact,
        "energy_impact": item.energy_impact,
        "uncertainty": item.uncertainty,
        "constraint_status": item.constraint_status,
        "confidence": item.confidence,
        "text": item.text,
        "status": state,
        "expires_at": expires_at,
        "advisory": "Recommendation is advisory only. No ProcessTwin connector can send plant-control commands.",
    }


@router.get("/api/v1/digital-twins/{equipment_id}/latest")
def latest_twin(
    equipment_id: UUID,
    context: TenantDependency,
    session: SessionDependency,
    limit: int = 100,
) -> dict[str, Any]:
    equipment = session.scalar(
        select(Equipment).where(
            Equipment.id == equipment_id,
            Equipment.organization_id == context.organization_id,
        )
    )
    if equipment is None:
        _raise("EQUIPMENT_NOT_FOUND", "Equipment was not found", 404)
    state = session.scalar(
        select(TwinState)
        .where(
            TwinState.organization_id == context.organization_id,
            TwinState.equipment_id == equipment.id,
        )
        .order_by(desc(TwinState.timestamp))
        .limit(1)
    )
    if state is None:
        _raise("TWIN_STATE_UNAVAILABLE", "No synchronized twin state is available", 404)
    previous = list(
        session.scalars(
            select(TwinState)
            .where(
                TwinState.organization_id == context.organization_id,
                TwinState.equipment_id == equipment.id,
            )
            .order_by(desc(TwinState.timestamp))
            .limit(min(max(limit, 1), 500))
        )
    )
    return {
        "equipment": {"id": str(equipment.id), "tag": equipment.tag, "name": equipment.name},
        "mode": state.source_mode,
        "timestamp": state.timestamp,
        "health": {"status": state.health_status, "factors": state.health_factors},
        "data_quality": state.data_quality,
        "prediction": {
            "status": state.prediction_status,
            "values": state.state.get("physics", {}),
            "uncertainty": state.uncertainty,
        },
        "model_version_id": str(state.model_version_id) if state.model_version_id else None,
        "physics_parameter_set_id": str(state.physics_parameter_set_id)
        if state.physics_parameter_set_id
        else None,
        "source_ids": state.source_ids,
        "measurements": state.state.get("measurements", {}),
        "physics_residual_temperature_k": state.state.get("physics_residual_temperature_k"),
        "potential_contributing_variables": state.state.get("potential_contributing_variables", []),
        "history": [
            {
                "timestamp": item.timestamp,
                "mode": item.source_mode,
                "health_status": item.health_status,
                "prediction_status": item.prediction_status,
                "state": item.state,
            }
            for item in previous
        ],
    }


@router.get("/api/v1/anomalies")
def list_potential_anomalies(
    context: TenantDependency, session: SessionDependency, limit: int = 100
) -> dict[str, Any]:
    rows = list(
        session.scalars(
            select(PotentialAnomaly)
            .where(PotentialAnomaly.organization_id == context.organization_id)
            .order_by(desc(PotentialAnomaly.timestamp))
            .limit(min(max(limit, 1), 500))
        )
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "label": "POTENTIAL ANOMALY",
                "timestamp": row.timestamp,
                "equipment_id": str(row.equipment_id),
                "twin_state_id": str(row.twin_state_id) if row.twin_state_id else None,
                "score": row.score,
                "evidence": row.evidence,
                "potential_contributing_variables": row.potential_contributing_variables,
                "causality_claimed": False,
            }
            for row in rows
        ]
    }


@router.get("/api/v1/recommendations")
def list_advisory_recommendations(
    context: TenantDependency, session: SessionDependency, limit: int = 100
) -> dict[str, Any]:
    rows = list(
        session.scalars(
            select(Recommendation)
            .where(Recommendation.organization_id == context.organization_id)
            .order_by(desc(Recommendation.created_at))
            .limit(min(max(limit, 1), 500))
        )
    )
    return {"items": [_recommendation_payload(item, datetime.now(UTC)) for item in rows]}


@router.post("/api/v1/recommendations/{recommendation_id}/review")
def review_recommendation(
    recommendation_id: UUID,
    payload: RecommendationReviewRequest,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    item = session.scalar(
        select(Recommendation).where(
            Recommendation.id == recommendation_id,
            Recommendation.organization_id == context.organization_id,
        )
    )
    if item is None:
        _raise("RECOMMENDATION_NOT_FOUND", "Recommendation was not found", 404)
    now = datetime.now(UTC)
    if item.status in {"ACCEPTED", "REJECTED", "EXPIRED"} or (
        item.expires_at and as_utc(item.expires_at) <= now
    ):
        _raise("RECOMMENDATION_NOT_REVIEWABLE", "Recommendation is final or expired", 409)
    if payload.decision == "REVIEWED" and item.status != "GENERATED":
        _raise(
            "INVALID_REVIEW_TRANSITION",
            "Only generated recommendations may be marked reviewed",
            409,
        )
    if payload.decision in {"ACCEPTED", "REJECTED"} and item.status not in {
        "GENERATED",
        "REVIEWED",
    }:
        _raise(
            "INVALID_REVIEW_TRANSITION",
            "Recommendation cannot transition from its current status",
            409,
        )
    item.status = payload.decision
    review = RecommendationReview(
        organization_id=context.organization_id,
        recommendation_id=item.id,
        user_id=context.user.id,
        decision=payload.decision,
        comment=payload.comment,
    )
    session.add(review)
    append_audit(
        session,
        context.organization_id,
        f"RECOMMENDATION_{payload.decision}",
        f"recommendation:{item.id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={"review_id": str(review.id), "comment": payload.comment},
    )
    session.commit()
    return {
        "recommendation": _recommendation_payload(item, now),
        "review": {
            "id": str(review.id),
            "user_id": str(context.user.id),
            "timestamp": review.created_at,
            "decision": review.decision,
            "comment": review.comment,
        },
    }


@router.get("/api/v1/recommendations/{recommendation_id}/reviews")
def recommendation_reviews(
    recommendation_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    item = session.scalar(
        select(Recommendation).where(
            Recommendation.id == recommendation_id,
            Recommendation.organization_id == context.organization_id,
        )
    )
    if item is None:
        _raise("RECOMMENDATION_NOT_FOUND", "Recommendation was not found", 404)
    reviews = list(
        session.scalars(
            select(RecommendationReview)
            .where(
                RecommendationReview.organization_id == context.organization_id,
                RecommendationReview.recommendation_id == item.id,
            )
            .order_by(RecommendationReview.created_at)
        )
    )
    return {
        "items": [
            {
                "id": str(review.id),
                "user_id": str(review.user_id) if review.user_id else None,
                "decision": review.decision,
                "comment": review.comment,
                "timestamp": review.created_at,
            }
            for review in reviews
        ]
    }
