from __future__ import annotations

import asyncio
import csv
import hmac
import io
import logging
import os
import re
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from packages.optimization import OptimizationService
from packages.physics import CSTRInputs, CSTRPhysicsModel, CSTRState
from packages.twin import DigitalTwinService
from packages.units import convert, si_unit, to_si
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .audit import append_audit
from .auth import (
    SessionDependency,
    TenantContext,
    create_token,
    current_user,
    decode_refresh_token,
    require_roles,
    tenant_context,
    verify_password,
)
from .config import get_settings
from .contracts import (
    LoginRequest,
    OptimizationRequest,
    ReadingRequest,
    RefreshRequest,
    SimulationRequest,
)
from .database import Base, database_is_ready, engine, set_request_principal, set_tenant_context
from .datasets import router as datasets_router
from .live_api import router as live_router
from .modeling import router as modeling_router
from .models import (
    Alert,
    Equipment,
    ModelVersion,
    OptimizationRun,
    OrganizationMembership,
    PhysicsParameterSet,
    Plant,
    QualityEvent,
    QualityStatusName,
    Recommendation,
    RefreshToken,
    RoleName,
    Sensor,
    SensorReading,
    Simulation,
    TwinState,
    User,
)
from .observability import RequestIdFilter, configure_logging, set_request_id
from .quality import DataQualityService
from .rate_limit import RATE_LIMITER
from .time_utils import as_utc
from .workflow_api import router as workflow_router

logger = logging.getLogger("processtwin.api")
REQUESTS = Counter(
    "processtwin_api_requests_total", "API request count", ["method", "path", "status"]
)
LATENCY = Histogram("processtwin_api_request_seconds", "API request duration", ["path"])
INGESTED = Counter("processtwin_sensor_readings_total", "Sensor readings persisted", ["quality"])
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging(get_settings().log_level)
    # Add request ID filter to root logger for correlation
    logging.getLogger().addFilter(RequestIdFilter())
    # Migration is preferred in development and mandatory in production.
    if get_settings().auto_create_schema:
        Base.metadata.create_all(bind=engine)
    try:
        yield
    finally:
        # Graceful shutdown: close database connections
        engine.dispose()
        logging.getLogger("processtwin.api").info("Application shutdown complete")


def create_app() -> FastAPI:
    """Create the FastAPI application with current get_settings()."""
    settings = get_settings()
    app = FastAPI(
        title="ProcessTwin API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
    )
    app.include_router(datasets_router)
    app.include_router(live_router)
    app.include_router(modeling_router)
    app.include_router(workflow_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins_parsed),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Organization-ID", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )
    return app


app = create_app()


@app.middleware("http")
async def request_context(request: Request, call_next: Any) -> Response:
    runtime_settings = get_settings()
    supplied_request_id = request.headers.get("X-Request-ID", "")
    request_id = (
        supplied_request_id if _REQUEST_ID_RE.fullmatch(supplied_request_id) else uuid4().hex
    )
    # Set request ID in context variable for logging correlation
    set_request_id(request_id)
    if request.method != "OPTIONS" and request.url.path not in {"/health", "/ready", "/metrics", "/live"}:
        client = request.client.host if request.client else "unknown"
        limit = (
            runtime_settings.login_rate_limit_requests
            if request.url.path == "/api/v1/auth/login"
            else runtime_settings.rate_limit_requests
        )
        allowed, retry_after = RATE_LIMITER.allow(
            f"{client}:{request.url.path}",
            limit=limit,
            window_seconds=runtime_settings.rate_limit_window_seconds,
        )
        if not allowed:
            response = JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": {
                        "code": "RATE_LIMITED",
                        "message": "Too many requests; retry later",
                        "request_id": request_id,
                    }
                },
            )
            response.headers["Retry-After"] = str(retry_after)
            return response
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            too_large = int(content_length) > runtime_settings.max_request_bytes
        except ValueError:
            too_large = True
        if too_large:
            return JSONResponse(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "error": {
                        "code": "REQUEST_TOO_LARGE",
                        "message": "Request exceeds configured size limit",
                        "request_id": request_id,
                    }
                },
            )
    started = perf_counter()
    with LATENCY.labels(request.url.path).time():
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "unhandled request error",
                extra={"request_id": request_id, "path": request.url.path},
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "Internal server error",
                        "request_id": request_id,
                    }
                },
            )
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    if runtime_settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    REQUESTS.labels(request.method, request.url.path, str(response.status_code)).inc()
    logger.info(
        "request_completed",
        extra={
            "event": "request_completed",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round((perf_counter() - started) * 1_000, 2),
        },
    )
    return response


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    detail = (
        exc.detail
        if isinstance(exc.detail, dict)
        else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {**detail, "request_id": request.headers.get("X-Request-ID", "")}},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, _: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "INVALID_REQUEST",
                "message": "Request validation failed",
                "request_id": request.headers.get("X-Request-ID", ""),
            }
        },
    )


@app.get("/live", tags=["system"])
def liveness() -> dict[str, str]:
    """Liveness probe - returns OK if the process is running.

    This endpoint should be used for Kubernetes liveness probes.
    It does not check external dependencies - only that the process is responsive.
    """
    return {"status": "alive", "service": "processtwin-api"}


@app.get("/health", tags=["system"])
def health() -> dict[str, Any]:
    """Health check - returns basic service status.

    This endpoint provides a quick health overview without checking
    external dependencies. Use /ready for dependency verification.
    """
    return {"status": "ok", "service": "processtwin-api", "version": "0.1.0"}


@app.get("/ready", tags=["system"])
def readiness() -> Response:
    """Readiness probe - verifies all required dependencies are available.

    This endpoint should be used for Kubernetes readiness probes.
    It checks database connectivity and other required services.
    """
    checks: dict[str, str] = {}
    all_ready = True

    # Check database
    if database_is_ready():
        checks["database"] = "ok"
    else:
        checks["database"] = "unavailable"
        all_ready = False

    # Check Redis if configured (for rate limiting, caching)
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            import redis  # type: ignore[import-untyped]
            client = redis.Redis.from_url(redis_url, socket_connect_timeout=2, socket_timeout=2)
            client.ping()
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "unavailable"
            all_ready = False
    else:
        checks["redis"] = "not_configured"

    status_code = status.HTTP_200_OK if all_ready else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=status_code,
        content={"status": "ready" if all_ready else "not_ready", "checks": checks},
    )


@app.get("/metrics", include_in_schema=False)
def metrics(request: Request) -> Response:
    configured_token = get_settings().metrics_token
    if configured_token:
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not hmac.compare_digest(supplied, configured_token):
            raise ApiError(
                status.HTTP_401_UNAUTHORIZED,
                "METRICS_AUTH_REQUIRED",
                "Metrics authentication required",
            )
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/v1/auth/login", tags=["auth"])
def login(payload: LoginRequest, request: Request, session: SessionDependency) -> dict[str, Any]:
    user = session.scalar(select(User).where(User.email == payload.email.lower()))
    if (
        user is None
        or not user.is_active
        or not verify_password(payload.password, user.password_hash)
    ):
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "INVALID_CREDENTIALS", "Invalid email or password"
        )
    set_request_principal(session, str(user.id))
    memberships = list(
        session.scalars(
            select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
        )
    )
    if not memberships:
        raise ApiError(
            status.HTTP_403_FORBIDDEN, "NO_ORGANIZATION_ACCESS", "User has no organization"
        )
    refresh_token_id = uuid4()
    refresh_expiry = datetime.now(UTC) + timedelta(days=get_settings().refresh_token_expire_days)
    session.add(RefreshToken(id=refresh_token_id, user_id=user.id, expires_at=refresh_expiry))
    for membership in memberships:
        set_tenant_context(session, str(membership.organization_id))
        append_audit(
            session,
            membership.organization_id,
            "LOGIN",
            "user",
            user_id=user.id,
            ip_address=request.client.host if request.client else None,
        )
    session.commit()
    return {
        "access_token": create_token(
            user.id, "access", timedelta(minutes=get_settings().access_token_expire_minutes)
        ),
        "refresh_token": create_token(
            user.id,
            "refresh",
            timedelta(days=get_settings().refresh_token_expire_days),
            token_id=refresh_token_id,
        ),
        "token_type": "bearer",
        "organizations": [
            {"id": str(item.organization_id), "role": item.role.value} for item in memberships
        ],
    }


@app.get("/api/v1/auth/me", tags=["auth"])
def me(user: Annotated[User, Depends(current_user)], session: SessionDependency) -> dict[str, Any]:
    memberships = list(
        session.scalars(
            select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
        )
    )
    return {
        "id": str(user.id),
        "email": user.email,
        "organizations": [
            {"id": str(item.organization_id), "role": item.role.value} for item in memberships
        ],
    }


@app.post("/api/v1/auth/refresh", tags=["auth"])
def refresh(payload: RefreshRequest, session: SessionDependency) -> dict[str, str]:
    user_id, token_id = decode_refresh_token(payload.refresh_token)
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED,
            "INVALID_REFRESH_TOKEN",
            "Refresh token user is no longer active",
        )
    set_request_principal(session, str(user.id))
    stored_token = session.get(RefreshToken, token_id)
    if (
        stored_token is None
        or stored_token.user_id != user.id
        or stored_token.revoked_at is not None
        or as_utc(stored_token.expires_at) <= datetime.now(UTC)
    ):
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "INVALID_REFRESH_TOKEN", "Refresh token is invalid"
        )
    membership = session.scalar(
        select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
    )
    if membership is None:
        raise ApiError(
            status.HTTP_403_FORBIDDEN, "NO_ORGANIZATION_ACCESS", "User has no organization"
        )
    replacement_id = uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=get_settings().refresh_token_expire_days)
    stored_token.revoked_at = datetime.now(UTC)
    stored_token.replaced_by_id = replacement_id
    session.add(RefreshToken(id=replacement_id, user_id=user.id, expires_at=expires_at))
    set_tenant_context(session, str(membership.organization_id))
    append_audit(
        session,
        membership.organization_id,
        "REFRESH_TOKEN_ROTATED",
        "user",
        user_id=user.id,
    )
    session.commit()
    return {
        "access_token": create_token(
            user.id, "access", timedelta(minutes=get_settings().access_token_expire_minutes)
        ),
        "refresh_token": create_token(
            user.id,
            "refresh",
            timedelta(days=get_settings().refresh_token_expire_days),
            token_id=replacement_id,
        ),
        "token_type": "bearer",
    }


@app.post("/api/v1/auth/logout", tags=["auth"])
def logout(payload: RefreshRequest, session: SessionDependency) -> dict[str, str]:
    user_id, token_id = decode_refresh_token(payload.refresh_token)
    user = session.get(User, user_id)
    if user is None:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "INVALID_REFRESH_TOKEN", "Refresh token is invalid"
        )
    set_request_principal(session, str(user.id))
    stored_token = session.get(RefreshToken, token_id)
    if stored_token is None or stored_token.user_id != user.id:
        raise ApiError(
            status.HTTP_401_UNAUTHORIZED, "INVALID_REFRESH_TOKEN", "Refresh token is invalid"
        )
    if stored_token.revoked_at is None:
        stored_token.revoked_at = datetime.now(UTC)
        membership = session.scalar(
            select(OrganizationMembership).where(OrganizationMembership.user_id == user.id)
        )
        if membership is not None:
            set_tenant_context(session, str(membership.organization_id))
            append_audit(
                session,
                membership.organization_id,
                "LOGOUT",
                "user",
                user_id=user.id,
            )
        session.commit()
    return {"status": "logged_out"}


TenantDependency = Annotated[TenantContext, Depends(tenant_context)]
EngineerDependency = Annotated[
    TenantContext, Depends(require_roles(RoleName.OWNER, RoleName.ADMIN, RoleName.ENGINEER))
]
AdminDependency = Annotated[TenantContext, Depends(require_roles(RoleName.OWNER, RoleName.ADMIN))]


def tenant_equipment(session: Session, equipment_id: UUID, organization_id: UUID) -> Equipment:
    equipment = session.scalar(
        select(Equipment).where(
            Equipment.id == equipment_id, Equipment.organization_id == organization_id
        )
    )
    if equipment is None:
        raise ApiError(
            status.HTTP_404_NOT_FOUND,
            "EQUIPMENT_NOT_FOUND",
            "Equipment was not found in this organization",
        )
    return equipment


@app.get("/api/v1/plants", tags=["plants"])
def list_plants(
    context: TenantDependency, session: SessionDependency, limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    if not 1 <= limit <= 200 or offset < 0:
        raise ApiError(422, "INVALID_PAGINATION", "limit must be 1-200 and offset non-negative")
    query = (
        select(Plant)
        .where(Plant.organization_id == context.organization_id)
        .order_by(Plant.name)
        .limit(limit)
        .offset(offset)
    )
    plants = list(session.scalars(query))
    return {
        "items": [
            {"id": str(plant.id), "name": plant.name, "location": plant.location}
            for plant in plants
        ],
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/v1/plants/{plant_id}", tags=["plants"])
def get_plant(
    plant_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    plant = session.scalar(
        select(Plant).where(Plant.id == plant_id, Plant.organization_id == context.organization_id)
    )
    if plant is None:
        raise ApiError(404, "PLANT_NOT_FOUND", "Plant was not found in this organization")
    return {
        "id": str(plant.id),
        "name": plant.name,
        "location": plant.location,
        "equipment": [
            {"id": str(item.id), "tag": item.tag, "name": item.name, "type": item.equipment_type}
            for item in plant.equipment
        ],
    }


@app.get("/api/v1/sensors", tags=["sensors"])
def list_sensors(context: TenantDependency, session: SessionDependency) -> dict[str, Any]:
    sensors = list(
        session.scalars(
            select(Sensor)
            .where(Sensor.organization_id == context.organization_id)
            .order_by(Sensor.tag)
        )
    )
    return {
        "items": [
            {
                "id": str(sensor.id),
                "equipment_id": str(sensor.equipment_id),
                "tag": sensor.tag,
                "name": sensor.name,
                "unit": sensor.unit,
                "measurement_type": sensor.measurement_type,
                "enabled": sensor.enabled,
            }
            for sensor in sensors
        ]
    }


@app.get("/api/v1/sensors/{sensor_id}/readings", tags=["sensors"])
def readings(
    sensor_id: UUID,
    context: TenantDependency,
    session: SessionDependency,
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    sensor = session.scalar(
        select(Sensor).where(
            Sensor.id == sensor_id, Sensor.organization_id == context.organization_id
        )
    )
    if sensor is None:
        raise ApiError(404, "SENSOR_NOT_FOUND", "Sensor was not found in this organization")
    query = (
        select(SensorReading)
        .where(
            SensorReading.sensor_id == sensor.id,
            SensorReading.organization_id == context.organization_id,
        )
        .order_by(desc(SensorReading.timestamp))
        .limit(min(max(limit, 1), 1000))
        .offset(max(offset, 0))
    )
    data = list(session.scalars(query))
    return {
        "sensor_id": str(sensor.id),
        "items": [
            {
                "timestamp": item.timestamp,
                "value": item.value,
                "unit": item.unit,
                "original_value": item.original_value,
                "original_unit": item.original_unit,
                "normalized_value": item.normalized_value,
                "normalized_unit": item.normalized_unit,
                "quality_status": item.quality_status.value,
                "quality_reasons": item.quality_reasons,
                "source": item.source,
            }
            for item in data
        ],
    }


def ingest_one(
    session: Session, context: TenantContext, payload: ReadingRequest, source: str = "REST"
) -> dict[str, Any]:
    sensor = session.scalar(
        select(Sensor).where(
            Sensor.id == payload.sensor_id,
            Sensor.organization_id == context.organization_id,
            Sensor.enabled.is_(True),
        )
    )
    if sensor is None:
        raise ApiError(404, "SENSOR_NOT_FOUND", "Enabled sensor was not found in this organization")
    if payload.unit != sensor.unit:
        try:
            normalized = convert(payload.value, payload.unit, sensor.unit)
        except ValueError as exc:
            raise ApiError(
                422, "INVALID_SENSOR_UNIT", f"Reading unit must be compatible with {sensor.unit}"
            ) from exc
    else:
        normalized = payload.value
    quality = DataQualityService().assess(session, sensor, payload.timestamp, normalized)
    DataQualityService.record_event(
        session, context.organization_id, sensor.id, payload.timestamp, quality
    )
    if quality.status is QualityStatusName.BAD:
        session.commit()  # Persist the quality event; the invalid reading itself is deliberately rejected.
        raise ApiError(422, "INVALID_SENSOR_READING", quality.detail or "Sensor reading is invalid")
    reading = SensorReading(
        organization_id=context.organization_id,
        sensor_id=sensor.id,
        timestamp=payload.timestamp,
        value=normalized,
        unit=sensor.unit,
        original_value=payload.value,
        original_unit=payload.unit,
        normalized_value=to_si(normalized, sensor.unit),
        normalized_unit=si_unit(sensor.unit),
        quality_status=quality.status,
        quality_reasons=[quality.detail or quality.event_type or "ALL_CONFIGURED_CHECKS_PASSED"],
        source=source,
    )
    session.add(reading)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        session.add(
            QualityEvent(
                organization_id=context.organization_id,
                sensor_id=sensor.id,
                reading_timestamp=payload.timestamp,
                event_type="DUPLICATE_TIMESTAMP",
                detail="A reading already exists for this sensor timestamp",
            )
        )
        session.commit()
        raise ApiError(
            409, "DUPLICATE_SENSOR_READING", "A reading already exists for this sensor timestamp"
        ) from exc
    INGESTED.labels(quality.status.value).inc()
    return {
        "id": str(reading.id),
        "quality_status": quality.status.value,
        "quality_reasons": reading.quality_reasons,
        "value": normalized,
        "unit": sensor.unit,
        "original_value": payload.value,
        "original_unit": payload.unit,
        "normalized_value": reading.normalized_value,
        "normalized_unit": reading.normalized_unit,
    }


@app.post("/api/v1/ingestion/readings", tags=["ingestion"])
def ingest_reading(
    payload: ReadingRequest,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    result = ingest_one(session, context, payload)
    append_audit(
        session,
        context.organization_id,
        "INGEST_SENSOR_READING",
        f"sensor:{payload.sensor_id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
    )
    session.commit()
    return result


@app.post("/api/v1/ingestion/csv", tags=["ingestion"])
async def ingest_csv(
    file: Annotated[UploadFile, File(...)],
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise ApiError(422, "INVALID_FILE_TYPE", "Only .csv files are accepted")
    raw = await file.read(get_settings().max_upload_bytes + 1)
    if len(raw) > get_settings().max_upload_bytes:
        raise ApiError(413, "FILE_TOO_LARGE", "CSV exceeds configured size limit")
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ApiError(422, "MALFORMED_CSV", "CSV must be UTF-8 with valid rows") from exc
    required = {"sensor_id", "timestamp", "value", "unit"}
    if not rows or not required.issubset(rows[0]):
        raise ApiError(
            422, "INVALID_CSV_COLUMNS", "Required columns: sensor_id,timestamp,value,unit"
        )
    accepted: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        try:
            reading_request = ReadingRequest(
                sensor_id=UUID(row["sensor_id"]),
                timestamp=datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")),
                value=float(row["value"]),
                unit=row["unit"],
            )
            accepted.append(ingest_one(session, context, reading_request, source="CSV"))
        except (ValueError, ApiError) as exc:
            session.rollback()
            if isinstance(exc, ApiError):
                detail = exc.detail
                # ApiError always sets detail as dict with "code" and "message"
                if isinstance(detail, dict):
                    message = detail.get("message", str(detail))
                else:
                    message = str(detail)
            else:
                message = str(exc)
            raise ApiError(422, "INVALID_CSV_ROW", f"Row {index}: {message}") from exc
    append_audit(
        session,
        context.organization_id,
        "IMPORT_CSV",
        "sensor_readings",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={"rows": len(accepted)},
    )
    session.commit()
    return {"accepted": len(accepted), "items": accepted}


def cstr_inputs_from_simulation(payload: SimulationRequest | OptimizationRequest) -> CSTRInputs:
    return CSTRInputs(
        feed_temperature_k=to_si(payload.temperature_c, "degC"),
        pressure_pa=to_si(payload.pressure_bar, "bar"),
        feed_flow_m3_s=to_si(payload.flow_m3_h, "m3/h"),
        feed_concentration_a_mol_m3=payload.feed_concentration_mol_m3,
        cooling_temperature_k=to_si(payload.cooling_temperature_c, "degC"),
    )


def validated_cstr_configuration(
    session: Session, organization_id: UUID, model_version_id: UUID | None
) -> tuple[ModelVersion, CSTRPhysicsModel, Any]:
    query = select(ModelVersion).where(
        ModelVersion.organization_id == organization_id,
        ModelVersion.model_type == "physics_cstr",
        ModelVersion.status.in_(("VALIDATED", "PRODUCTION")),
    )
    if model_version_id is not None:
        query = query.where(ModelVersion.id == model_version_id)
    model_version = session.scalar(query.order_by(desc(ModelVersion.created_at)).limit(1))
    if model_version is None:
        raise ApiError(
            422,
            "VALIDATED_MODEL_REQUIRED",
            "Select a tenant-owned VALIDATED/PRODUCTION CSTR model version before optimizing",
        )
    if not model_version.operating_envelope:
        raise ApiError(
            422, "MODEL_ENVELOPE_REQUIRED", "Validated model has no configured operating envelope"
        )
    parameter_set = (
        session.get(PhysicsParameterSet, model_version.physics_parameter_set_id)
        if model_version.physics_parameter_set_id
        else None
    )
    if parameter_set is None or parameter_set.organization_id != organization_id:
        raise ApiError(
            422, "MODEL_PARAMETERS_REQUIRED", "Validated model has no tenant-owned parameter set"
        )
    from .realtime import parameter_set_from_values

    return (
        model_version,
        CSTRPhysicsModel(parameter_set_from_values(parameter_set.parameters)),
        parameter_set,
    )


def optimization_envelope(model_version: ModelVersion, physics: CSTRPhysicsModel) -> Any:
    from packages.optimization import OperatingEnvelope

    configured = model_version.operating_envelope
    aliases = {
        "temperature_k": ("temperature_k", 1.0, 0.0),
        "temperature_c": ("temperature_k", 1.0, 273.15),
        "pressure_pa": ("pressure_pa", 1.0, 0.0),
        "pressure_bar": ("pressure_pa", 100_000.0, 0.0),
        "feed_flow_m3_s": ("flow_m3_s", 1.0, 0.0),
        "feed_flow_kg_s": ("flow_m3_s", 1.0 / physics.parameters.density_kg_m3, 0.0),
        "feed_flow_kg_h": ("flow_m3_s", 1.0 / (3600.0 * physics.parameters.density_kg_m3), 0.0),
    }
    converted: dict[str, tuple[float, float]] = {}
    for key, value in configured.items():
        if key not in aliases:
            continue
        name, scale, offset = aliases[key]
        low = float(value["minimum"]) * scale + offset
        high = float(value["maximum"]) * scale + offset
        if name in converted and converted[name] != (low, high):
            raise ApiError(
                422, "INVALID_MODEL_ENVELOPE", f"Conflicting envelope definitions for {name}"
            )
        converted[name] = (low, high)
    required = {"temperature_k", "pressure_pa", "flow_m3_s"}
    if not required.issubset(converted):
        raise ApiError(
            422,
            "INCOMPLETE_MODEL_ENVELOPE",
            "Optimization requires temperature, pressure and feed-flow envelope bounds",
        )
    temperature = converted["temperature_k"]
    pressure = converted["pressure_pa"]
    flow = converted["flow_m3_s"]
    return OperatingEnvelope(
        temperature_k_min=temperature[0],
        temperature_k_max=temperature[1],
        pressure_pa_min=pressure[0],
        pressure_pa_max=pressure[1],
        flow_m3_s_min=flow[0],
        flow_m3_s_max=flow[1],
        max_energy_w=float(configured.get("max_energy_w", 1_000_000.0)),
    )


def serialized_metrics(
    state: CSTRState, inputs: CSTRInputs, model: CSTRPhysicsModel
) -> dict[str, float | None]:
    metrics = model.metrics(state, inputs)
    return {
        "temperature_c": convert(state.temperature_k, "K", "degC"),
        "pressure_bar": convert(inputs.pressure_pa, "Pa", "bar"),
        "conversion_pct": metrics.conversion * 100,
        "yield_pct": metrics.yield_b * 100,
        "selectivity_pct": metrics.selectivity_b * 100,
        "reaction_rate_mol_m3_s": metrics.reaction_rate_b_mol_m3_s,
        "heat_generation_kw": metrics.heat_generation_w / 1000,
        "heat_removal_kw": metrics.heat_removal_w / 1000,
        "residence_time_s": metrics.residence_time_s,
        "energy_proxy_kw": metrics.heat_removal_w / 1000,
    }


@app.post("/api/v1/simulations", tags=["simulations"])
def run_simulation(
    payload: SimulationRequest,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    tenant_equipment(session, payload.equipment_id, context.organization_id)
    inputs = cstr_inputs_from_simulation(payload)
    model_version: ModelVersion | None = None
    parameter_set: PhysicsParameterSet | None = None
    model = CSTRPhysicsModel()
    envelope: dict[str, Any] = {}
    warnings: list[str] = [
        "SIMULATION MODE: scenario values are computed; no live measurements or control commands are used."
    ]
    if payload.model_version_id is not None:
        model_version, model, parameter_set = validated_cstr_configuration(
            session, context.organization_id, payload.model_version_id
        )
        envelope = model_version.operating_envelope
    try:
        baseline_state = model.steady_state(CSTRInputs())
        baseline = serialized_metrics(baseline_state, CSTRInputs(), model)
        result = model.simulate(baseline_state, inputs, (0.0, payload.duration_s), sample_count=121)
    except RuntimeError as exc:
        raise ApiError(422, "SIMULATION_FAILED", str(exc)) from exc
    final = serialized_metrics(result.final_state, inputs, model)
    trajectory = [
        {
            "time_s": float(time),
            "temperature_c": convert(state.temperature_k, "K", "degC"),
            "conversion_pct": metric.conversion * 100,
            "yield_pct": metric.yield_b * 100,
            "energy_proxy_kw": metric.heat_removal_w / 1000,
        }
        for time, state, metric in zip(result.time_s, result.states, result.metrics, strict=True)
    ]
    configured_envelope = (
        optimization_envelope(model_version, model)
        if model_version is not None
        else OptimizationService(model).envelope
    )
    outside_range = not configured_envelope.contains(inputs)
    if model_version is None:
        warnings.append(
            "UNVALIDATED_REFERENCE_MODEL: this what-if uses demonstrator defaults, not a validated plant model."
        )
    if outside_range:
        warnings.append(
            "OUTSIDE VALIDATED MODEL RANGE: simulated extrapolation is illustrative and not predictive."
        )
    response = {
        "mode": "SIMULATION",
        "baseline": baseline,
        "scenario": final,
        "difference": {
            "yield_percentage_points": final["yield_pct"] - baseline["yield_pct"],  # type: ignore[operator]
            "energy_kw": final["energy_proxy_kw"] - baseline["energy_proxy_kw"],  # type: ignore[operator]
            "temperature_c": final["temperature_c"] - baseline["temperature_c"],  # type: ignore[operator]
            "pressure_bar": final["pressure_bar"] - baseline["pressure_bar"],  # type: ignore[operator]
            "conversion_percentage_points": final["conversion_pct"] - baseline["conversion_pct"],  # type: ignore[operator]
            "selectivity_percentage_points": final["selectivity_pct"] - baseline["selectivity_pct"],  # type: ignore[operator]
        },
        "trajectory": trajectory,
        "constraint_violations": ["Outside validated model range."] if outside_range else [],
        "model_validity": "UNVALIDATED_REFERENCE"
        if model_version is None
        else ("OUTSIDE_VALIDATED_MODEL_RANGE" if outside_range else "VALIDATED_MODEL_RANGE"),
        "model_version_id": str(model_version.id) if model_version else None,
        "physics_parameter_set_id": str(parameter_set.id) if parameter_set else None,
        "operating_envelope": envelope,
        "uncertainty": {
            "status": "UNAVAILABLE",
            "reason": "No scenario uncertainty estimator is configured",
        },
        "warnings": warnings,
        "source": "SIMULATED",
        "advisory": "Simulation only. No actual plant setting has been modified.",
    }
    simulation = Simulation(
        organization_id=context.organization_id,
        equipment_id=payload.equipment_id,
        inputs=payload.model_dump(mode="json"),
        results=response,
        user_id=context.user.id,
        model_version_id=model_version.id if model_version else None,
        physics_parameter_set_id=parameter_set.id if parameter_set else None,
        operating_envelope=envelope,
        warnings=warnings,
    )
    session.add(simulation)
    append_audit(
        session,
        context.organization_id,
        "RUN_SIMULATION",
        f"equipment:{payload.equipment_id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
    )
    session.commit()
    return {"id": str(simulation.id), **response}


@app.get("/api/v1/simulations/{simulation_id}", tags=["simulations"])
def get_simulation(
    simulation_id: UUID, context: TenantDependency, session: SessionDependency
) -> dict[str, Any]:
    simulation = session.scalar(
        select(Simulation).where(
            Simulation.id == simulation_id, Simulation.organization_id == context.organization_id
        )
    )
    if simulation is None:
        raise ApiError(404, "SIMULATION_NOT_FOUND", "Simulation not found in this organization")
    return {
        "id": str(simulation.id),
        "inputs": simulation.inputs,
        "results": simulation.results,
        "status": simulation.status,
        "created_at": simulation.created_at,
    }


@app.post("/api/v1/optimization/runs", tags=["optimization"])
def optimize(
    payload: OptimizationRequest,
    context: EngineerDependency,
    session: SessionDependency,
    request: Request,
) -> dict[str, Any]:
    tenant_equipment(session, payload.equipment_id, context.organization_id)
    model_version, physics, parameter_set = validated_cstr_configuration(
        session, context.organization_id, payload.model_version_id
    )
    baseline_inputs = cstr_inputs_from_simulation(payload)
    try:
        envelope = optimization_envelope(model_version, physics)
        optimizer = OptimizationService(physics=physics, envelope=envelope)
        result = optimizer.optimize(baseline_inputs, payload.energy_weight)
    except (ValueError, RuntimeError) as exc:
        raise ApiError(422, "OPTIMIZATION_REJECTED", str(exc)) from exc
    baseline_state = physics.steady_state(baseline_inputs)
    optimized_state = physics.steady_state(result.inputs)
    baseline_metrics = serialized_metrics(baseline_state, baseline_inputs, physics)
    optimized_metrics = serialized_metrics(optimized_state, result.inputs, physics)
    recommended = {
        "temperature_c": convert(result.inputs.feed_temperature_k, "K", "degC"),
        "pressure_bar": convert(result.inputs.pressure_pa, "Pa", "bar"),
        "flow_m3_h": convert(result.inputs.feed_flow_m3_s, "m3/s", "m3/h"),
    }
    response = {
        "model_validity": "VALIDATED_MODEL_RANGE",
        "model_version_id": str(model_version.id),
        "physics_parameter_set_id": str(parameter_set.id),
        "algorithm": "scipy.differential_evolution",
        "objective": {
            "name": "maximize_yield_minus_energy",
            "weight": payload.energy_weight,
            "value": result.objective,
        },
        "bounds": {
            "temperature_k": [envelope.temperature_k_min, envelope.temperature_k_max],
            "pressure_pa": [envelope.pressure_pa_min, envelope.pressure_pa_max],
            "feed_flow_m3_s": [envelope.flow_m3_s_min, envelope.flow_m3_s_max],
            "max_energy_w": envelope.max_energy_w,
        },
        "baseline": baseline_metrics,
        "optimized": {**optimized_metrics, "variables": recommended},
        "objective_improvement": result.objective
        - (
            result.baseline_yield
            - payload.energy_weight * (result.baseline_energy_w / envelope.max_energy_w)
        ),
        "energy_impact_kw": (result.optimized_energy_w - result.baseline_energy_w) / 1000,
        "constraints": {
            "status": result.constraint_status,
            "hard_energy_limit_w": envelope.max_energy_w,
            "operating_envelope": model_version.operating_envelope,
        },
        "uncertainty": {
            "status": "UNAVAILABLE",
            "reason": "No uncertainty estimator is registered for this operating point",
        },
        "advisory": result.advisory,
    }
    run = OptimizationRun(
        organization_id=context.organization_id,
        equipment_id=payload.equipment_id,
        objective="maximize_yield_minus_energy",
        baseline=payload.model_dump(mode="json"),
        result=response,
        user_id=context.user.id,
        model_version_id=model_version.id,
        physics_parameter_set_id=parameter_set.id,
        algorithm="scipy.differential_evolution",
        bounds=response["bounds"],
        constraints=response["constraints"],
        uncertainty=response["uncertainty"],
        status="COMPLETED" if result.constraint_status == "PASS" else "INFEASIBLE",
    )
    session.add(run)
    recommendation = None
    simulation = None
    if result.constraint_status == "PASS":
        simulation_response = {
            "mode": "SIMULATION",
            "baseline": baseline_metrics,
            "scenario": optimized_metrics,
            "difference": {
                "yield_percentage_points": optimized_metrics["yield_pct"] - baseline_metrics["yield_pct"],  # type: ignore[operator]
                "energy_kw": optimized_metrics["energy_proxy_kw"] - baseline_metrics["energy_proxy_kw"],  # type: ignore[operator]
                "temperature_c": optimized_metrics["temperature_c"] - baseline_metrics["temperature_c"],  # type: ignore[operator]
                "pressure_bar": optimized_metrics["pressure_bar"] - baseline_metrics["pressure_bar"],  # type: ignore[operator]
                "conversion_percentage_points": optimized_metrics["conversion_pct"] - baseline_metrics["conversion_pct"],  # type: ignore[operator]
                "selectivity_percentage_points": optimized_metrics["selectivity_pct"] - baseline_metrics["selectivity_pct"],  # type: ignore[operator]
            },
            "model_validity": "VALIDATED_MODEL_RANGE",
            "model_version_id": str(model_version.id),
            "physics_parameter_set_id": str(parameter_set.id),
            "constraints": response["constraints"],
            "uncertainty": response["uncertainty"],
            "warnings": [
                "SIMULATION MODE: optimization output is a model scenario, not a plant command."
            ],
            "source": "SIMULATED",
        }
        simulation = Simulation(
            organization_id=context.organization_id,
            equipment_id=payload.equipment_id,
            inputs={**payload.model_dump(mode="json"), "optimized": recommended},
            results=simulation_response,
            user_id=context.user.id,
            model_version_id=model_version.id,
            physics_parameter_set_id=parameter_set.id,
            operating_envelope=model_version.operating_envelope,
            warnings=simulation_response["warnings"],
        )
        session.add(simulation)
        session.flush()
        recommendation = Recommendation(
            organization_id=context.organization_id,
            equipment_id=payload.equipment_id,
            text=f"Consider reviewing the model scenario: temperature {recommended['temperature_c']:.1f}°C and feed flow {recommended['flow_m3_h']:.1f} m³/h.",
            expected_impact={
                "yield_percentage_points": (result.optimized_yield - result.baseline_yield) * 100,
                "objective_improvement": response["objective_improvement"],
            },
            confidence="Computed from a VALIDATED model and the recorded operating envelope; not guaranteed plant performance.",
            status="GENERATED",
            simulation_id=simulation.id,
            optimization_run_id=run.id,
            model_version_id=model_version.id,
            baseline=baseline_metrics,
            proposed_change=recommended,
            energy_impact={
                "change_kw": response["energy_impact_kw"],
                "baseline_kw": baseline_metrics["energy_proxy_kw"],
                "scenario_kw": optimized_metrics["energy_proxy_kw"],
            },
            uncertainty=response["uncertainty"],
            constraint_status=result.constraint_status,
            expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        session.add(recommendation)
    append_audit(
        session,
        context.organization_id,
        "RUN_OPTIMIZATION",
        f"equipment:{payload.equipment_id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
    )
    session.commit()
    return {
        "id": str(run.id),
        "simulation_id": str(simulation.id) if simulation else None,
        "recommendation_id": str(recommendation.id) if recommendation else None,
        **response,
    }


@app.get("/api/v1/recommendations", tags=["recommendations"])
def recommendations(
    context: TenantDependency, session: SessionDependency, limit: int = 50
) -> dict[str, Any]:
    entries = list(
        session.scalars(
            select(Recommendation)
            .where(Recommendation.organization_id == context.organization_id)
            .order_by(desc(Recommendation.created_at))
            .limit(min(max(limit, 1), 200))
        )
    )
    return {
        "items": [
            {
                "id": str(entry.id),
                "equipment_id": str(entry.equipment_id),
                "text": entry.text,
                "expected_impact": entry.expected_impact,
                "confidence": entry.confidence,
                "status": entry.status,
                "created_at": entry.created_at,
                "advisory": "AI-generated engineering recommendation. Verify against plant operating procedures before implementation.",
            }
            for entry in entries
        ]
    }


@app.get("/api/v1/alerts", tags=["alerts"])
def alerts(
    context: TenantDependency, session: SessionDependency, limit: int = 50
) -> dict[str, Any]:
    entries = list(
        session.scalars(
            select(Alert)
            .where(Alert.organization_id == context.organization_id)
            .order_by(desc(Alert.occurred_at))
            .limit(min(max(limit, 1), 200))
        )
    )
    return {
        "items": [
            {
                "id": str(entry.id),
                "equipment_id": str(entry.equipment_id) if entry.equipment_id else None,
                "severity": entry.severity.value,
                "reason": entry.reason,
                "status": entry.status,
                "timestamp": entry.occurred_at,
            }
            for entry in entries
        ]
    }


@app.get("/api/v1/models", tags=["models"])
def models(context: TenantDependency, session: SessionDependency) -> dict[str, Any]:
    entries = list(
        session.scalars(
            select(ModelVersion)
            .where(ModelVersion.organization_id == context.organization_id)
            .order_by(desc(ModelVersion.created_at))
        )
    )
    return {
        "items": [
            {
                "id": str(entry.id),
                "name": entry.name,
                "version": entry.version,
                "type": entry.model_type,
                "status": entry.status,
                "metrics": entry.metrics,
                "features": entry.feature_schema,
                "target": entry.target_schema,
                "dataset_version_id": str(entry.dataset_version_id)
                if entry.dataset_version_id
                else None,
                "physics_parameter_set_id": str(entry.physics_parameter_set_id)
                if entry.physics_parameter_set_id
                else None,
                "training_period": entry.training_period,
                "validation_period": entry.validation_period,
                "test_period": entry.test_period,
                "hyperparameters": entry.hyperparameters,
                "operating_envelope": entry.operating_envelope,
                "git_sha": entry.git_sha,
                "mlflow_run_id": entry.mlflow_run_id,
                "created_by": str(entry.created_by) if entry.created_by else None,
                "created_at": entry.created_at,
            }
            for entry in entries
        ]
    }


@app.post("/api/v1/models/train", tags=["models"])
def train_model(
    context: EngineerDependency, session: SessionDependency, request: Request
) -> dict[str, Any]:
    # Training is an explicit workflow, scoped to this tenant; it only registers VALIDATION models.
    from apps.worker.processtwin_worker.train import train_all

    trained = train_all(context.organization_id)
    append_audit(
        session,
        context.organization_id,
        "TRAIN_MODEL",
        "model_registry",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
        metadata={"registered_versions": trained},
    )
    session.commit()
    return {
        "registered_versions": trained,
        "status": "VALIDATION",
        "message": "No model was promoted automatically.",
    }


@app.post("/api/v1/models/{model_id}/promote", tags=["models"])
def promote_model(
    model_id: UUID, context: AdminDependency, session: SessionDependency, request: Request
) -> dict[str, Any]:
    candidate = session.scalar(
        select(ModelVersion).where(
            ModelVersion.id == model_id, ModelVersion.organization_id == context.organization_id
        )
    )
    if candidate is None:
        raise ApiError(404, "MODEL_NOT_FOUND", "Model was not found in this organization")
    if candidate.status != "STAGING":
        raise ApiError(422, "MODEL_NOT_APPROVABLE", "Only explicitly staged models may be promoted")
    for current in session.scalars(
        select(ModelVersion).where(
            ModelVersion.organization_id == context.organization_id,
            ModelVersion.name == candidate.name,
            ModelVersion.status == "PRODUCTION",
        )
    ):
        current.status = "RETIRED"
    candidate.status = "PRODUCTION"
    append_audit(
        session,
        context.organization_id,
        "PROMOTE_MODEL",
        f"model:{model_id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
    )
    session.commit()
    return {"id": str(candidate.id), "status": candidate.status}


@app.post("/api/v1/models/{model_id}/stage", tags=["models"])
def stage_model(
    model_id: UUID, context: AdminDependency, session: SessionDependency, request: Request
) -> dict[str, str]:
    candidate = session.scalar(
        select(ModelVersion).where(
            ModelVersion.id == model_id,
            ModelVersion.organization_id == context.organization_id,
        )
    )
    if candidate is None:
        raise ApiError(404, "MODEL_NOT_FOUND", "Model was not found in this organization")
    if candidate.status != "VALIDATED":
        raise ApiError(
            422,
            "MODEL_NOT_VALIDATED",
            "Only a model with an independent VALIDATED evaluation may be staged",
        )
    candidate.status = "STAGING"
    append_audit(
        session,
        context.organization_id,
        "STAGE_MODEL",
        f"model:{model_id}",
        user_id=context.user.id,
        ip_address=request.client.host if request.client else None,
    )
    session.commit()
    return {"id": str(candidate.id), "status": candidate.status}


@app.get("/api/v1/audit-log", tags=["audit"])
def audit_log(
    context: TenantDependency, session: SessionDependency, limit: int = 100
) -> dict[str, Any]:
    from .models import AuditLog

    entries = list(
        session.scalars(
            select(AuditLog)
            .where(AuditLog.organization_id == context.organization_id)
            .order_by(desc(AuditLog.timestamp))
            .limit(min(max(limit, 1), 500))
        )
    )
    return {
        "items": [
            {
                "id": str(entry.id),
                "action": entry.action,
                "resource": entry.resource,
                "user_id": str(entry.user_id) if entry.user_id else None,
                "timestamp": entry.timestamp,
                "metadata": entry.metadata_json,
            }
            for entry in entries
        ]
    }


def dashboard_payload(context: TenantContext, session: Session) -> dict[str, Any]:
    equipment = session.scalar(
        select(Equipment)
        .where(Equipment.organization_id == context.organization_id)
        .order_by(Equipment.created_at)
    )
    if equipment is None:
        return {"plant_health": "NO_DATA", "message": "No equipment configured"}
    sensors = list(
        session.scalars(
            select(Sensor).where(
                Sensor.equipment_id == equipment.id,
                Sensor.organization_id == context.organization_id,
            )
        )
    )
    latest: dict[str, dict[str, Any]] = {}
    modes: set[str] = set()
    for sensor in sensors:
        reading = session.scalar(
            select(SensorReading)
            .where(
                SensorReading.sensor_id == sensor.id,
                SensorReading.organization_id == context.organization_id,
                SensorReading.quality_status.in_(
                    (QualityStatusName.GOOD, QualityStatusName.SUSPECT)
                ),
            )
            .order_by(desc(SensorReading.timestamp))
        )
        if reading:
            from .realtime import source_mode

            modes.add(source_mode(reading.source))
            latest[sensor.tag] = {
                "value": reading.value,
                "unit": reading.unit,
                "quality_status": reading.quality_status.value,
                "source": reading.source,
                "timestamp": reading.timestamp,
            }
    if len(modes) > 1:
        return {
            "equipment": {"id": str(equipment.id), "tag": equipment.tag, "name": equipment.name},
            "source_mode": "MIXED_DATA_BLOCKED",
            "plant_health": "MIXED_DATA_BLOCKED",
            "measurements": {},
            "twin": None,
            "active_alerts": 0,
            "safety_notice": "SIMULATION, HISTORICAL and LIVE READ-ONLY values are never combined. Select a consistent source mode.",
        }
    mode = next(iter(modes)) if modes else "NO_DATA"
    if mode in {"LIVE_READ_ONLY", "HISTORICAL"}:
        stored = session.scalar(
            select(TwinState)
            .where(
                TwinState.organization_id == context.organization_id,
                TwinState.equipment_id == equipment.id,
                TwinState.source_mode == mode,
            )
            .order_by(desc(TwinState.timestamp))
            .limit(1)
        )
        if stored is None:
            twin_body = None
            health_status = "WAITING_FOR_SYNCHRONIZED_TWIN"
        else:
            physics_state = stored.state.get("physics", {})
            measured = stored.state.get("measurements", {})
            measured_temperature = measured.get("reactor.temperature", {})
            selected_temperature = measured_temperature.get("normalized_value")
            if selected_temperature is None:
                selected_temperature = physics_state.get("temperature_k")
            twin_body = {
                "temperature": {
                    "value": convert(float(selected_temperature), "K", "degC")
                    if selected_temperature is not None
                    else None,
                    "unit": "degC",
                    "source": "MEASURED" if measured_temperature else "ESTIMATED",
                    "timestamp": stored.timestamp,
                    "quality_status": measured_temperature.get("quality_status", "GOOD"),
                },
                "conversion": {
                    "value": physics_state.get("conversion", 0.0) * 100,
                    "unit": "%",
                    "source": "ESTIMATED",
                    "timestamp": stored.timestamp,
                    "quality_status": "GOOD",
                },
                "yield": {
                    "value": physics_state.get("yield", 0.0) * 100,
                    "unit": "%",
                    "source": "ESTIMATED",
                    "timestamp": stored.timestamp,
                    "quality_status": "GOOD",
                },
                "selectivity": {
                    "value": physics_state.get("selectivity", 0.0) * 100,
                    "unit": "%",
                    "source": "ESTIMATED",
                    "timestamp": stored.timestamp,
                    "quality_status": "GOOD",
                },
                "heat_removal": {
                    "value": physics_state.get("heat_removal_w", 0.0) / 1000,
                    "unit": "kW",
                    "source": "ESTIMATED",
                    "timestamp": stored.timestamp,
                    "quality_status": "GOOD",
                },
                "divergence_temperature_k": stored.state.get("physics_residual_temperature_k"),
                "prediction_status": stored.prediction_status,
                "uncertainty": stored.uncertainty,
                "data_quality": stored.data_quality,
                "health_factors": stored.health_factors,
                "model_version_id": str(stored.model_version_id)
                if stored.model_version_id
                else None,
                "physics_parameter_set_id": str(stored.physics_parameter_set_id)
                if stored.physics_parameter_set_id
                else None,
            }
            health_status = stored.health_status
        return {
            "equipment": {"id": str(equipment.id), "tag": equipment.tag, "name": equipment.name},
            "source_mode": mode,
            "plant_health": health_status,
            "measurements": latest,
            "twin": twin_body,
            "active_alerts": 0,
            "safety_notice": "LIVE READ-ONLY MODE"
            if mode == "LIVE_READ_ONLY"
            else "HISTORICAL MODE; no control connection is available.",
        }
    if mode == "NO_DATA":
        return {"plant_health": "NO_DATA", "source_mode": mode, "measurements": {}, "twin": None}
    twin = DigitalTwinService()
    nominal_inputs = CSTRInputs()
    state = twin.physics.steady_state(nominal_inputs)
    measured_temperature = (
        to_si(latest["REACTOR_TEMPERATURE"]["value"], latest["REACTOR_TEMPERATURE"]["unit"])
        if "REACTOR_TEMPERATURE" in latest
        else None
    )
    snapshot = twin.snapshot(
        state=state, inputs=nominal_inputs, measured_temperature_k=measured_temperature
    )
    active_alerts = (
        session.scalar(
            select(func.count())
            .select_from(Alert)
            .where(Alert.organization_id == context.organization_id, Alert.status == "OPEN")
        )
        or 0
    )
    return {
        "equipment": {"id": str(equipment.id), "tag": equipment.tag, "name": equipment.name},
        "source_mode": "SIMULATION",
        "plant_health": "SIMULATION",
        "measurements": latest,
        "twin": {
            "temperature": snapshot.temperature.model_dump(),
            "conversion": snapshot.conversion.model_dump(),
            "yield": snapshot.yield_b.model_dump(),
            "selectivity": snapshot.selectivity_b.model_dump(),
            "heat_removal": snapshot.heat_removal.model_dump(),
            "divergence_temperature_k": snapshot.divergence_temperature_k,
        },
        "active_alerts": active_alerts,
        "safety_notice": "Predictions and recommendations are advisory. No physical plant controls are connected.",
    }


@app.get("/api/v1/dashboard/summary", tags=["dashboard"])
def dashboard(context: TenantDependency, session: SessionDependency) -> dict[str, Any]:
    return dashboard_payload(context, session)


@app.get(
    "/api/v1/events/dashboard",
    tags=["dashboard"],
    response_class=StreamingResponse,
)
async def dashboard_events(
    context: TenantDependency, session: SessionDependency
) -> StreamingResponse:
    async def events() -> AsyncGenerator[str, None]:
        # Read-only SSE polling endpoint; deployments can replace this with Redis/Kafka event fanout.
        for _ in range(60):
            response = JSONResponse(content=dashboard_payload(context, session))
            body = response.body
            if isinstance(body, memoryview):
                body = body.tobytes()
            yield f"event: twin_state\ndata: {body.decode()}\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
