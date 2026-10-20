"""Read-only production database diagnostic tool for ProcessTwin.

Safely checks schemas, search_paths, table existence, permissions, and Alembic
revisions across DATABASE_ADMIN_URL and DATABASE_URL without printing credentials
or modifying any data.

Usable locally:
  python scripts/diagnose_db.py --admin "<admin_url>" --app "<app_url>"
Or using environment variables:
  DATABASE_ADMIN_URL="..." DATABASE_URL="..." python scripts/diagnose_db.py
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

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


def normalize_connection_url(raw_url: str) -> str:
    """Normalize SQLAlchemy/psycopg/Render URL variations for direct psycopg connection."""
    url = raw_url.strip()
    if url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url[len("postgresql+psycopg://") :]
    elif url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    # If connecting to a remote cloud host (like Render), ensure sslmode=require
    if hostname not in ("localhost", "127.0.0.1", ""):
        query_params = parse_qs(parsed.query)
        if "sslmode" not in query_params:
            query_params["sslmode"] = ["require"]
            new_query = urlencode(query_params, doseq=True)
            url = urlunparse(parsed._replace(query=new_query))

    return url


def inspect_role(label: str, raw_url: str) -> dict[str, object]:
    """Execute read-only queries to inspect role context, schema, and tables."""
    print(f"\n{'=' * 70}")
    print(f"[{label}] Inspecting: {mask_url(raw_url)}")
    print(f"{'=' * 70}")

    url = normalize_connection_url(raw_url)
    results: dict[str, object] = {}

    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=10) as conn:
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
                            cur.execute(f'SELECT version_num FROM "{av_schema}"."{av_name}";')  # noqa: S608
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

                # 7. Unqualified query: SELECT count(*) FROM users
                unqualified_ok = False
                try:
                    cur.execute("SELECT count(*) FROM users;")
                    c_row = cur.fetchone()
                    count = c_row[0] if c_row else 0
                    print(f"  Unqualified query (users)        : SUCCESS (count = {count})")
                    results["users_unqualified"] = "SUCCESS"
                    unqualified_ok = True
                except Exception as exc:
                    print(f"  Unqualified query (users)        : FAILED -> {type(exc).__name__}: {exc}")
                    results["users_unqualified"] = f"{type(exc).__name__}: {exc}"

                # 8. Schema-qualified query: SELECT count(*) FROM public.users
                try:
                    cur.execute("SELECT count(*) FROM public.users;")
                    c_row = cur.fetchone()
                    count = c_row[0] if c_row else 0
                    print(f"  Qualified query (public.users)   : SUCCESS (count = {count})")
                    results["users_qualified"] = "SUCCESS"
                    if not unqualified_ok:
                        print("    [!] ROOT CAUSE DIAGNOSED: public.users exists, but unqualified resolution failed.")
                        print("        The role's search_path does not search public, or USAGE on public was skipped.")
                except Exception as exc:
                    print(f"  Qualified query (public.users)   : FAILED -> {type(exc).__name__}: {exc}")
                    results["users_qualified"] = f"{type(exc).__name__}: {exc}"

                # 9. Check RLS policies if any
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
    parser = argparse.ArgumentParser(description="ProcessTwin Production Database Diagnostic")
    parser.add_argument("--admin", default="", help="DATABASE_ADMIN_URL (Owner/Migrator)")
    parser.add_argument("--app", default="", help="DATABASE_URL (App Non-Owner)")
    args = parser.parse_args()

    admin_url = args.admin or os.environ.get("DATABASE_ADMIN_URL", "").strip()
    runtime_url = args.app or os.environ.get("DATABASE_URL", "").strip()

    print("=" * 70)
    print("ProcessTwin Production Database Diagnostic")
    print("=" * 70)

    if not admin_url and not runtime_url:
        print("ERROR: Neither DATABASE_ADMIN_URL nor DATABASE_URL was provided.")
        print("Usage:")
        print("  python scripts/diagnose_db.py --admin 'postgres://...' --app 'postgres://...'")
        print("Or set DATABASE_ADMIN_URL and DATABASE_URL in your environment before running.")
        return 1

    admin_res: dict[str, object] = {}
    runtime_res: dict[str, object] = {}

    if admin_url:
        admin_res = inspect_role("DATABASE_ADMIN_URL (Owner/Migrator)", admin_url)
    else:
        print("\n[DATABASE_ADMIN_URL]: NOT SET")

    if runtime_url:
        runtime_res = inspect_role("DATABASE_URL (App Non-Owner)", runtime_url)
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

    # Check findings
    admin_av = admin_res.get("alembic_version")
    admin_users = admin_res.get("users_locations")
    app_uq = runtime_res.get("users_unqualified")

    if admin_av and not admin_users:
        print("\n[CRITICAL ROOT CAUSE IDENTIFIED: FALSE REVISION STAMP]")
        print("  - alembic_version contains revisions, but 'users' table does NOT exist in the database.")
        print("  - Cause: The database was stamped or initialized with alembic_version without executing DDL.")
        print("  - Solution: With the updated migration runner, deploying will automatically detect this,")
        print("    clear the orphaned marker, and run full migrations cleanly.")
    elif app_uq and app_uq != "SUCCESS":
        print("\n[CRITICAL ROOT CAUSE IDENTIFIED: PERMISSION OR SEARCH_PATH ISSUE]")
        print(f"  - App role query failed with: {app_uq}")
        print("  - Solution: Ensure the app role has USAGE on schema public and table DML privileges.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
