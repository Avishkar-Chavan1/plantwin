"""Provision an initial organization, engineer user, and plant for ProcessTwin.

Safe, idempotent, and designed for initial environment setup (production, staging, or local).
Unlike seed_demo, this does NOT generate synthetic sensor or physics data. It only creates
the administrative foundation required for authentication and dashboard access.

Usage:
  # With explicit database URL:
  uv run python scripts/seed_initial_user.py --url "postgresql://..." --email "engineer@processtwin.demo" --password "demo-password-123!"

  # With environment variable (DATABASE_ADMIN_URL or DATABASE_URL):
  $env:DATABASE_ADMIN_URL = "postgresql://..."
  uv run python scripts/seed_initial_user.py --email "engineer@processtwin.demo" --password "demo-password-123!"
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from typing import Any
from urllib.parse import urlsplit

from apps.api.processtwin_api.auth import hash_password, validate_password_policy
from apps.api.processtwin_api.models import (
    Organization,
    OrganizationMembership,
    Plant,
    RoleName,
    User,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_initial_user")

_EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_input_url(raw: str | None) -> str:
    """Clean and strip surrounding whitespace and accidental quotes from URL input."""
    if not raw:
        return ""
    val = raw.strip()
    while (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        val = val[1:-1].strip()
    return val


def normalize_database_url(url: str) -> str:
    """Normalize URL scheme for SQLAlchemy (e.g. postgres:// -> postgresql://)."""
    cleaned = clean_input_url(url)
    if cleaned.startswith("postgres://"):
        return "postgresql://" + cleaned[len("postgres://") :]
    return cleaned


def mask_url(url: str | None) -> str:
    """Mask credentials in database URLs for safe logging."""
    if not url:
        return "<not set>"
    cleaned = clean_input_url(url)
    if not cleaned:
        return "<not set>"
    try:
        parsed = urlsplit(cleaned)
        if not parsed.scheme or not parsed.hostname:
            return "<invalid url>"
        username = parsed.username
        if parsed.password is not None:
            auth = f"{username or ''}:***@"
        elif username:
            auth = f"{username}@"
        else:
            auth = ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{auth}{parsed.hostname}{port}{parsed.path}"
    except Exception:
        return "<unparseable url>"


def seed_user(
    engine_or_session: Any,
    *,
    email: str,
    password: str,
    org_name: str = "ProcessTwin Demonstration",
    plant_name: str = "Simulated CSTR Plant",
    plant_location: str = "Simulation only - no physical site",
    role: RoleName = RoleName.OWNER,
    update_password: bool = False,
) -> dict[str, str]:
    """Idempotently seed the initial organization, user, membership, and plant.

    Returns a dict with the created/existing IDs:
      {"organization_id": ..., "user_id": ..., "plant_id": ...}
    """
    clean_email = email.strip().lower()
    if not _EMAIL_REGEX.match(clean_email):
        raise ValueError(f"Invalid email address: {email}")

    is_valid, errors = validate_password_policy(password)
    if not is_valid:
        raise ValueError(f"Password does not satisfy policy: {'; '.join(errors)}")

    def _execute(session: Session) -> dict[str, str]:
        # 1. Organization
        org = session.scalar(select(Organization).where(Organization.name == org_name))
        if org is None:
            org = Organization(name=org_name)
            session.add(org)
            session.flush()
            logger.info("Created organization: '%s' (id=%s)", org.name, org.id)
        else:
            logger.info("Found existing organization: '%s' (id=%s)", org.name, org.id)

        # 2. User
        user = session.scalar(select(User).where(User.email == clean_email))
        if user is None:
            user = User(
                email=clean_email,
                password_hash=hash_password(password),
                is_active=True,
            )
            session.add(user)
            session.flush()
            logger.info("Created user: '%s' (id=%s)", user.email, user.id)
        else:
            logger.info("Found existing user: '%s' (id=%s)", user.email, user.id)
            if update_password:
                user.password_hash = hash_password(password)
                user.is_active = True
                session.flush()
                logger.info("Updated password for user: '%s'", user.email)

        # 3. Organization Membership
        membership = session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id == org.id,
                OrganizationMembership.user_id == user.id,
            )
        )
        if membership is None:
            membership = OrganizationMembership(
                organization_id=org.id,
                user_id=user.id,
                role=role,
            )
            session.add(membership)
            session.flush()
            logger.info(
                "Created membership: '%s' is %s in '%s'", user.email, role.value, org.name
            )
        else:
            logger.info("Found existing membership: role=%s", membership.role.value)
            if membership.role != role:
                membership.role = role
                session.flush()
                logger.info("Updated membership role to %s", role.value)

        # 4. Plant
        plant = session.scalar(
            select(Plant).where(
                Plant.organization_id == org.id,
                Plant.name == plant_name,
            )
        )
        if plant is None:
            plant = Plant(
                organization_id=org.id,
                name=plant_name,
                location=plant_location,
            )
            session.add(plant)
            session.flush()
            logger.info("Created plant: '%s' (id=%s)", plant.name, plant.id)
        else:
            logger.info("Found existing plant: '%s' (id=%s)", plant.name, plant.id)

        session.commit()
        return {
            "organization_id": str(org.id),
            "user_id": str(user.id),
            "plant_id": str(plant.id),
        }

    if isinstance(engine_or_session, Session):
        return _execute(engine_or_session)

    with Session(engine_or_session) as session:
        return _execute(session)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision initial organization, user, and plant for ProcessTwin.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url",
        "--admin-url",
        dest="url",
        default=None,
        help="Target database URL. Defaults to DATABASE_ADMIN_URL or DATABASE_URL env var.",
    )
    parser.add_argument(
        "--email",
        default=os.environ.get("INITIAL_USER_EMAIL", "engineer@processtwin.demo"),
        help="Email address for initial user (default: engineer@processtwin.demo).",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("INITIAL_USER_PASSWORD", "demo-password-123!"),
        help="Password for initial user (must satisfy policy).",
    )
    parser.add_argument(
        "--org-name",
        default=os.environ.get("INITIAL_ORG_NAME", "ProcessTwin Demonstration"),
        help="Organization name (default: ProcessTwin Demonstration).",
    )
    parser.add_argument(
        "--plant-name",
        default=os.environ.get("INITIAL_PLANT_NAME", "Simulated CSTR Plant"),
        help="Plant name (default: Simulated CSTR Plant).",
    )
    parser.add_argument(
        "--plant-location",
        default="Simulation only - no physical site",
        help="Plant location description.",
    )
    parser.add_argument(
        "--role",
        default="OWNER",
        choices=[r.value for r in RoleName],
        help="Membership role for user (default: OWNER).",
    )
    parser.add_argument(
        "--update-password",
        action="store_true",
        help="Update user password if user already exists.",
    )

    args = parser.parse_args()

    raw_url = (
        args.url
        or os.environ.get("DATABASE_ADMIN_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not raw_url:
        logger.error(
            "Database URL required. Provide --url, set DATABASE_ADMIN_URL, or set DATABASE_URL."
        )
        return 1

    normalized_url = normalize_database_url(raw_url)
    masked = mask_url(normalized_url)
    logger.info("Connecting to target database: %s", masked)

    is_postgres = normalized_url.startswith(("postgresql", "postgres"))
    connect_args = {"options": "-c search_path=public"} if is_postgres else {}

    try:
        engine = create_engine(
            normalized_url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        result = seed_user(
            engine,
            email=args.email,
            password=args.password,
            org_name=args.org_name,
            plant_name=args.plant_name,
            plant_location=args.plant_location,
            role=RoleName(args.role),
            update_password=args.update_password,
        )
        logger.info(
            "SUCCESS! Initial provisioning complete.\n"
            "  Organization ID: %s\n"
            "  User ID:         %s (%s)\n"
            "  Plant ID:        %s",
            result["organization_id"],
            result["user_id"],
            args.email.lower(),
            result["plant_id"],
        )
        return 0
    except Exception as exc:
        logger.error("Failed to seed initial user: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
