"""Provision the non-owner PostgreSQL application role.

Executes the equivalent of scripts/postgres_app_role.sql without requiring
the psql CLI tool (works on Windows, macOS, and Linux via Python + psycopg).

This non-owner role ensures PostgreSQL row-level security (RLS) is strictly
enforced during runtime API operations.

Usage:
  uv run python scripts/create_app_role.py --admin-url "postgresql://..." --app-role "processtwin_app" --app-password "your-strong-password"
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import secrets
import sys
from urllib.parse import urlsplit, urlunsplit

import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("create_app_role")

_SAFE_ROLE_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


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


def provision_role(admin_url: str, app_role: str, app_password: str) -> str:
    """Provision non-owner application role and return the constructed DATABASE_URL."""
    if not _SAFE_ROLE_RE.match(app_role):
        raise ValueError(f"Invalid role identifier: {app_role}")
    if len(app_password) < 16:
        raise ValueError("Application role password must be at least 16 characters")

    cleaned_admin = clean_input_url(admin_url)
    # psycopg supports postgresql:// and postgres:// natively
    logger.info("Connecting to admin database: %s", mask_url(cleaned_admin))

    with psycopg.connect(cleaned_admin, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Create or update role
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s) THEN
                        EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', %s, %s);
                    ELSE
                        EXECUTE format('ALTER ROLE %I WITH PASSWORD %L', %s, %s);
                    END IF;
                END
                $$;
                """,
                (app_role, app_role, app_password, app_role, app_password),
            )
            logger.info("Role '%s' ensured with login privileges.", app_role)

            # 2. Grant permissions in schema public
            cur.execute(
                psycopg.sql.SQL("GRANT USAGE ON SCHEMA public TO {};").format(
                    psycopg.sql.Identifier(app_role)
                )
            )
            cur.execute(
                psycopg.sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {};"
                ).format(psycopg.sql.Identifier(app_role))
            )
            cur.execute(
                psycopg.sql.SQL(
                    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {};"
                ).format(psycopg.sql.Identifier(app_role))
            )
            cur.execute(
                psycopg.sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {};"
                ).format(psycopg.sql.Identifier(app_role))
            )
            cur.execute(
                psycopg.sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {};"
                ).format(psycopg.sql.Identifier(app_role))
            )
            logger.info("Schema USAGE and table DML privileges granted to '%s'.", app_role)

    # Build the application connection URL with the new credentials
    parsed = urlsplit(cleaned_admin)
    netloc_parts = []
    netloc_parts.append(f"{app_role}:{app_password}@")
    netloc_parts.append(parsed.hostname or "")
    if parsed.port:
        netloc_parts.append(f":{parsed.port}")
    new_netloc = "".join(netloc_parts)

    app_url = urlunsplit((parsed.scheme, new_netloc, parsed.path, parsed.query, parsed.fragment))
    return app_url


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision the non-owner application role for PostgreSQL RLS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--admin-url",
        default=os.environ.get("DATABASE_ADMIN_URL"),
        help="PostgreSQL admin connection URL (table owner / superuser).",
    )
    parser.add_argument(
        "--app-role",
        default="processtwin_app",
        help="Role name to create/grant (default: processtwin_app).",
    )
    parser.add_argument(
        "--app-password",
        default=None,
        help="Password for the application role. If omitted, a secure 24-character password is generated.",
    )

    args = parser.parse_args()

    admin_url = args.admin_url
    if not admin_url:
        logger.error("Admin database URL required. Provide --admin-url or set DATABASE_ADMIN_URL.")
        return 1

    password = args.app_password or secrets.token_urlsafe(18)

    try:
        app_url = provision_role(admin_url, args.app_role, password)
        logger.info(
            "SUCCESS! Non-owner role '%s' provisioned.\n"
            "Set this as your DATABASE_URL in Render:\n"
            "  %s",
            args.app_role,
            app_url,
        )
        return 0
    except Exception as exc:
        logger.error("Failed to provision application role: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())

