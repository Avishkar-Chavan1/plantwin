"""Authentication and authorization dependencies; tenancy is derived from membership."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Protocol, cast
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_session, set_request_principal, set_tenant_context
from .models import OrganizationMembership, RoleName, User


class PasswordContext(Protocol):
    def hash(self, secret: str) -> str: ...

    def verify(self, secret: str, hash: str) -> bool: ...


password_context = cast(PasswordContext, CryptContext(schemes=["bcrypt"], deprecated="auto"))
bearer = HTTPBearer(auto_error=False)
SessionDependency = Annotated[Session, Depends(get_session)]


@dataclass(frozen=True)
class TenantContext:
    user: User
    organization_id: UUID
    role: RoleName


def hash_password(password: str) -> str:
    return password_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return password_context.verify(password, password_hash)


def create_token(
    user_id: UUID, token_type: str, expires_in: timedelta, *, token_id: UUID | None = None
) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "type": token_type,
            "iat": now,
            "exp": now + expires_in,
            "jti": str(token_id) if token_id else None,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_token(credentials: HTTPAuthorizationCredentials | None) -> UUID:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            get_settings().jwt_secret,
            algorithms=[get_settings().jwt_algorithm],
            issuer=get_settings().jwt_issuer,
            audience=get_settings().jwt_audience,
        )
        if payload.get("type") != "access":
            raise ValueError("Not an access token")
        return UUID(str(payload["sub"]))
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        ) from exc


def decode_refresh_token(token: str) -> tuple[UUID, UUID]:
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[get_settings().jwt_algorithm],
            issuer=get_settings().jwt_issuer,
            audience=get_settings().jwt_audience,
        )
        if payload.get("type") != "refresh":
            raise ValueError("Not a refresh token")
        return UUID(str(payload["sub"])), UUID(str(payload["jti"]))
    except (jwt.InvalidTokenError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token"
        ) from exc


def current_user(
    session: SessionDependency,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    user_id = decode_token(credentials)
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive or unknown user"
        )
    set_request_principal(session, str(user.id))
    return user


def tenant_context(
    request: Request, session: SessionDependency, user: Annotated[User, Depends(current_user)]
) -> TenantContext:
    raw_org_id = request.headers.get("X-Organization-ID")
    if not raw_org_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="X-Organization-ID header is required"
        )
    try:
        organization_id = UUID(raw_org_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid organization ID"
        ) from exc
    membership = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user.id,
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No membership in requested organization"
        )
    set_tenant_context(session, str(organization_id))
    return TenantContext(user=user, organization_id=organization_id, role=membership.role)


def require_roles(*allowed: RoleName) -> Callable[[TenantContext], TenantContext]:
    def dependency(context: Annotated[TenantContext, Depends(tenant_context)]) -> TenantContext:
        if context.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role for this action"
            )
        return context

    return dependency
