"""PostgreSQL row-level-security enforcement proofs.

These tests are the evidence that tenant isolation holds at the *database* layer,
not only in application queries. They require a real PostgreSQL target:

    TEST_DATABASE_URL=postgresql+psycopg://app_role:...@host/processtwin \
    TEST_DATABASE_ADMIN_URL=postgresql+psycopg://owner:...@host/processtwin \
    pytest tests/integration/test_postgres_rls.py

- ``TEST_DATABASE_URL`` is the role the API uses. In the production posture this is
  the NON-OWNER role provisioned by ``scripts/postgres_app_role.sql``; RLS is only
  enforced against a non-owner.
- ``TEST_DATABASE_ADMIN_URL`` is an owner/superuser role used for seeding and must
  be set whenever the app role is non-owner (owners and superusers bypass RLS).

Without ``TEST_DATABASE_URL`` pointing at a postgresql:// URL the whole module is
skipped, so local SQLite runs stay green.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import uuid4

import pytest

APP_URL = os.environ.get("TEST_DATABASE_URL", "")
ADMIN_URL = os.environ.get("TEST_DATABASE_ADMIN_URL", APP_URL)
IS_POSTGRES = APP_URL.startswith(("postgresql://", "postgres://", "postgresql+psycopg://"))

PASSWORD = "RlsE2ePassword!123"

pytestmark = pytest.mark.skipif(
    not IS_POSTGRES, reason="requires TEST_DATABASE_URL pointing at a PostgreSQL server"
)


@pytest.fixture(scope="module")
def seed() -> dict[str, Any]:
    """Create two tenants and one user as the admin (owner) role, which bypasses RLS."""
    from apps.api.processtwin_api.auth import hash_password
    from apps.api.processtwin_api.models import (
        Organization,
        OrganizationMembership,
        Plant,
        RoleName,
        User,
    )
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    suffix = uuid4().hex[:12]
    engine = create_engine(ADMIN_URL)
    try:
        with Session(engine) as session:
            org_a = Organization(name=f"RLS Org A {suffix}")
            org_b = Organization(name=f"RLS Org B {suffix}")
            user = User(
                email=f"rls-{suffix}@example.test",
                password_hash=hash_password(PASSWORD),
                is_active=True,
            )
            session.add_all([org_a, org_b, user])
            session.flush()
            session.add(
                OrganizationMembership(
                    organization_id=org_a.id, user_id=user.id, role=RoleName.OWNER
                )
            )
            session.add(Plant(organization_id=org_a.id, name=f"RLS Plant A {suffix}"))
            session.add(Plant(organization_id=org_b.id, name=f"RLS Plant B {suffix}"))
            session.commit()
            return {
                "org_a": org_a.id,
                "org_b": org_b.id,
                "user_id": user.id,
                "email": user.email,
                "plant_a": f"RLS Plant A {suffix}",
                "plant_b": f"RLS Plant B {suffix}",
            }
    finally:
        engine.dispose()


def test_rls_policies_are_enabled_on_every_protected_table(seed: dict[str, Any]) -> None:
    """Migration 0006 must have armed all 28 tenant tables plus membership/token tables."""
    from apps.api.processtwin_api.database import SessionLocal
    from sqlalchemy import text

    with SessionLocal() as session:
        policy_count = session.scalar(
            text(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname = 'public' AND policyname LIKE 'processtwin_%'"
            )
        )
        row_security = session.scalar(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = 'plants'")
        )
    assert policy_count == 30, f"expected 30 processtwin_* policies, found {policy_count}"
    assert row_security is True, "ROW LEVEL SECURITY is not enabled on plants"


def test_unscoped_session_sees_no_tenant_rows(seed: dict[str, Any]) -> None:
    """Without a tenant GUC the app role must see zero tenant-scoped rows."""
    from apps.api.processtwin_api.database import SessionLocal
    from apps.api.processtwin_api.models import OrganizationMembership, Plant
    from sqlalchemy import func, select

    with SessionLocal() as session:
        plants = session.scalar(select(func.count()).select_from(Plant))
        memberships = session.scalar(select(func.count()).select_from(OrganizationMembership))
    assert plants == 0, f"unscoped session saw {plants} plants"
    assert memberships == 0, f"unscoped session saw {memberships} memberships"


def test_tenant_context_scopes_plant_visibility(seed: dict[str, Any]) -> None:
    """set_tenant_context (the app's RLS wiring) reveals exactly one tenant's rows."""
    from apps.api.processtwin_api.database import SessionLocal, set_tenant_context
    from apps.api.processtwin_api.models import Plant
    from sqlalchemy import select

    with SessionLocal() as session:
        set_tenant_context(session, str(seed["org_a"]))
        names_a = list(session.scalars(select(Plant.name)))
        set_tenant_context(session, str(seed["org_b"]))
        names_b = list(session.scalars(select(Plant.name)))

    assert seed["plant_a"] in names_a
    assert seed["plant_b"] not in names_a
    assert seed["plant_b"] in names_b
    assert seed["plant_a"] not in names_b


def test_principal_context_scopes_membership_visibility(seed: dict[str, Any]) -> None:
    """set_request_principal reveals only the caller's own memberships."""
    from apps.api.processtwin_api.database import SessionLocal, set_request_principal
    from apps.api.processtwin_api.models import OrganizationMembership
    from sqlalchemy import select

    with SessionLocal() as session:
        set_request_principal(session, str(seed["user_id"]))
        org_ids = list(
            session.scalars(
                select(OrganizationMembership.organization_id).where(
                    OrganizationMembership.user_id == seed["user_id"]
                )
            )
        )
    assert org_ids == [seed["org_a"]]


def test_end_to_end_api_flow_under_enforced_rls(seed: dict[str, Any]) -> None:
    """Login, tenant reads, cross-tenant denial, refresh rotation and logout must all
    work while RLS is enforced against the app role.

    This is the regression guard for the whole class of bugs where an endpoint
    queries a protected table before its GUC is set (or after a commit clears it).
    """
    from apps.api.processtwin_api.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        # Login inserts refresh_tokens and audit_logs, both RLS-protected: the
        # WITH CHECK clauses only pass because set_request_principal/set_tenant_context
        # run first.
        login = client.post(
            "/api/v1/auth/login", json={"email": seed["email"], "password": PASSWORD}
        )
        assert login.status_code == 200, login.text
        body = login.json()
        assert [org["id"] for org in body["organizations"]] == [str(seed["org_a"])]

        auth = {"Authorization": f"Bearer {body['access_token']}"}

        me = client.get("/api/v1/auth/me", headers=auth)
        assert me.status_code == 200, me.text
        assert [org["id"] for org in me.json()["organizations"]] == [str(seed["org_a"])]

        # Own tenant: visible.
        own = client.get(
            "/api/v1/plants",
            headers={**auth, "X-Organization-ID": str(seed["org_a"])},
        )
        assert own.status_code == 200, own.text
        assert seed["plant_a"] in str(own.json())
        assert seed["plant_b"] not in str(own.json())

        # Foreign tenant: membership gate rejects before any row can leak.
        foreign = client.get(
            "/api/v1/plants",
            headers={**auth, "X-Organization-ID": str(seed["org_b"])},
        )
        assert foreign.status_code == 403, foreign.text

        # Refresh rotation mutates refresh_tokens (user-scoped RLS policy).
        refresh1 = client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert refresh1.status_code == 200, refresh1.text
        # Replay of the rotated token must fail (revocation is enforced, not cosmetic).
        replay = client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
        assert replay.status_code == 401, replay.text

        logout = client.post("/api/v1/auth/logout", json={"refresh_token": refresh1.json()["refresh_token"]})
        assert logout.status_code == 200, logout.text
        reuse = client.post("/api/v1/auth/logout", json={"refresh_token": refresh1.json()["refresh_token"]})
        assert reuse.status_code == 401, reuse.text
