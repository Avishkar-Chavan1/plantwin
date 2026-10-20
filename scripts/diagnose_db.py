"""Read-only production database diagnostic tool for ProcessTwin.

Safely checks schemas, search_paths, table existence, permissions, and Alembic
revisions across DATABASE_ADMIN_URL and DATABASE_URL without printing credentials
or modifying any data.
"""

from __future__ import annotations

import logging
import os
import sys
from urllib.parse import urlparse

import psycopg

logger = logging.getLogger("diagnose_db")


def mask_url(url: str | None) -> str:
    """Mask credentials in database URLs for safe logging."""
    if not url:
        return "<not set>"
    try:
        parsed = urlparse(url)
        username = parsed.username or "<none>"
        hostname = parsed.hostname or "<none>"
        port = parsed.port or 5432
        dbname = parsed.path.lstrip("/") or "<none>"
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.scheme}://{username}:***@{hostname}:{port}/{dbname}{query}"
    except Exception:
        return "<invalid url>"


def inspect_role(label: str, url: str) -> dict[str, object]:
    """Execute read-only queries to inspect role context, schema, and tables."""
    print(f"\n{'=' * 70}")
    print(f"[{label}] Inspecting: {mask_url(url)}")
    print(f"{'=' * 70}")

    results: dict[str, object] = {}

    try:
        # Connect using autocommit (read-only queries)
        with psycopg.connect(url, autocommit=True) as conn:
            with conn.cursor() as cur:
                # 1. Connection identity
                cur.execute(
                    "SELECT current_database(), current_user, current_schema(), session_user;"
                )
                row = cur.fetchone()
                if row:
                    db, user, schema, s_user = row
                    print(f"  current_database() : {db}")
                    print(f"  current_user       : {user}")
                    print(f"  current_schema()   : {schema}")
                    print(f"  session_user       : {s_user}")
                    results["database"] = db
                    results["user"] = user
                    results["schema"] = schema

                # 2. search_path
                cur.execute("SHOW search_path;")
                sp_row = cur.fetchone()
                search_path = sp_row[0] if sp_row else "<unknown>"
                print(f"  search_path        : {search_path}")
                results["search_path"] = search_path

                # 3. Available schemas
                cur.execute(
                    """
                    SELECT nspname, pg_catalog.pg_get_userbyid(nspowner) AS owner
                    FROM pg_namespace
                    WHERE nspname NOT LIKE 'pg_%' AND nspname != 'information_schema'
                    ORDER BY nspname;
                    """
                )
                schemas = cur.fetchall()
                print("  Non-system schemas :")
                for s_name, s_owner in schemas:
                    has_usage = False
                    try:
                        cur.execute("SELECT has_schema_privilege(current_user, %s, 'USAGE');", (s_name,))
                        u_row = cur.fetchone()
                        has_usage = bool(u_row and u_row[0])
                    except Exception as exc:
                        logger.debug("Failed checking schema privilege: %s", exc)
                    print(f"    - {s_name} (owner: {s_owner}, USAGE privilege: {has_usage})")

                # 4. alembic_version table
                cur.execute(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_name = 'alembic_version';
                    """
                )
                av_tables = cur.fetchall()
                if av_tables:
                    for av_schema, av_name in av_tables:
                        try:
                            # Table name is hardcoded from information_schema lookup
                            cur.execute('SELECT version_num FROM alembic_version;')
                            versions = [r[0] for r in cur.fetchall()]
                            print(f"  Alembic version in [{av_schema}.{av_name}]: {versions}")
                            results["alembic_version"] = versions
                        except Exception as e:
                            print(f"  Alembic version in [{av_schema}.{av_name}] read error: {e}")
                else:
                    print("  Alembic version table : NOT FOUND in information_schema.tables")
                    results["alembic_version"] = None

                # 5. Tables in database
                cur.execute(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_schema NOT LIKE 'pg_%'
                      AND table_schema != 'information_schema'
                    ORDER BY table_schema, table_name;
                    """
                )
                tables = cur.fetchall()
                print(f"  User tables total  : {len(tables)}")
                by_schema: dict[str, list[str]] = {}
                for t_schema, t_name in tables:
                    by_schema.setdefault(t_schema, []).append(t_name)
                for t_schema, t_names in by_schema.items():
                    print(f"    Schema '{t_schema}' ({len(t_names)} tables):")
                    for name in t_names:
                        print(f"      - {name}")
                results["tables_by_schema"] = by_schema

                # 6. Specific check for 'users'
                cur.execute(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_name = 'users';
                    """
                )
                user_tables = cur.fetchall()
                print(f"  'users' table locations: {user_tables or 'NONE'}")
                results["users_locations"] = user_tables

                # 7. Test query: SELECT count(*) FROM users
                try:
                    cur.execute("SELECT count(*) FROM users;")
                    c_row = cur.fetchone()
                    count = c_row[0] if c_row else 0
                    print(f"  Count query on users table: SUCCESS (count = {count})")
                    results["users_query"] = "SUCCESS"
                except Exception as exc:
                    print(f"  Count query on users table: FAILED -> {type(exc).__name__}: {exc}")
                    results["users_query"] = f"{type(exc).__name__}: {exc}"

                # 8. Check RLS policies if any
                cur.execute(
                    """
                    SELECT schemaname, tablename, policyname, roles
                    FROM pg_policies
                    WHERE policyname LIKE 'processtwin_%'
                    ORDER BY schemaname, tablename;
                    """
                )
                policies = cur.fetchall()
                print(f"  ProcessTwin RLS policies count: {len(policies)}")
                results["policy_count"] = len(policies)

    except Exception as exc:
        print(f"  ERROR connecting with {label}: {type(exc).__name__}: {exc}")
        results["error"] = f"{type(exc).__name__}: {exc}"

    return results


def main() -> int:
    admin_url = os.environ.get("DATABASE_ADMIN_URL", "").strip()
    runtime_url = os.environ.get("DATABASE_URL", "").strip()

    print("=" * 70)
    print("ProcessTwin Production Database Diagnostic")
    print("=" * 70)

    if not admin_url and not runtime_url:
        print("ERROR: Neither DATABASE_ADMIN_URL nor DATABASE_URL is set in environment.")
        print("Usage:")
        print("  python scripts/diagnose_db.py")
        print("Or set DATABASE_ADMIN_URL and DATABASE_URL before invoking.")
        return 1

    if admin_url:
        inspect_role("DATABASE_ADMIN_URL (Owner/Migrator)", admin_url)
    else:
        print("\n[DATABASE_ADMIN_URL]: NOT SET")

    if runtime_url:
        inspect_role("DATABASE_URL (App Non-Owner)", runtime_url)
    else:
        print("\n[DATABASE_URL]: NOT SET")

    print(f"\n{'=' * 70}")
    print("DIAGNOSTIC SUMMARY & ROOT CAUSE ANALYSIS")
    print(f"{'=' * 70}")

    if admin_url and runtime_url:
        u_admin = urlparse(admin_url)
        u_app = urlparse(runtime_url)
        target_match = (
            u_admin.hostname == u_app.hostname
            and u_admin.port == u_app.port
            and u_admin.path == u_app.path
        )
        print(f"1. Database target match: {'YES (same database)' if target_match else 'NO (MISMATCH!)'}")
        if not target_match:
            print(f"   Admin host/path : {u_admin.hostname}:{u_admin.port}{u_admin.path}")
            print(f"   App host/path   : {u_app.hostname}:{u_app.port}{u_app.path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
