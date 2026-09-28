from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from .models import AuditLog


def append_audit(
    session: Session,
    organization_id: UUID,
    action: str,
    resource: str,
    *,
    user_id: UUID | None = None,
    ip_address: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Intentionally has no paired mutation/delete operation in the application layer."""
    session.add(
        AuditLog(
            organization_id=organization_id,
            user_id=user_id,
            action=action,
            resource=resource,
            ip_address=ip_address,
            metadata_json=metadata or {},
        )
    )
