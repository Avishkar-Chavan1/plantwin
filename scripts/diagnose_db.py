"""Read-only production database diagnostic tool for ProcessTwin.

Safely checks schemas, search_paths, table existence, permissions, and Alembic
revisions across DATABASE_ADMIN_URL and DATABASE_URL without printing credentials
or modifying any data.

Connection URLs can be supplied via CLI flags or environment variables:
  CLI Flags:
    python scripts/diagnose_db.py --admin "<admin_url>" --app "<app_url>"
  Environment Variables:
    DATABASE_ADMIN_URL="<admin_url>" DATABASE_URL="<app_url>" python scripts/diagnose_db.py
  Mixed:
    CLI flags take precedence over environment variables when both are present.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict

logger = logging.getLogger("diagnose_db")

_ALLOWED_SCHEMES = frozenset(
    {
        "postgresql",
        "postgres",
        "postgresql+psycopg",
        "postgresql+psycopg2",
        "postgresql+asyncpg",
    }
)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", ""})


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
    """Mask credentials in database URLs for safe logging and reporting."""
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

        host = parsed.hostname
        port_str = f":{parsed.port}" if parsed.port else ""
        path = parsed.path

        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=True)
            for sensitive_key in ("password", "secret", "token", "key"):
                if sensitive_key in params:
                    params[sensitive_key] = ["***"]
            query_str = f"?{urlencode(params, doseq=True)}"
        else:
            query_str = ""

        return f"{parsed.scheme}://{auth}{host}{port_str}{path}{query_str}"
    except Exception:
        return "<invalid url>"


@dataclass(frozen=True)
class TargetInfo:
    """Validated database target information."""

    raw_scheme: str
    username: str | None
    hostname: str
    port: int
    dbname: str
    normalized_url: str
    masked_url: str


def parse_and_validate_url(raw_url: str | None, role_label: str) -> TargetInfo:
    """Parse, validate, and normalize a database connection URL.

    Raises ValueError with actionable diagnostics if the URL is missing,
    malformed, or incompatible with PostgreSQL.
    """
    cleaned = clean_input_url(raw_url)
    if not cleaned:
        raise ValueError(
            f"Missing {role_label}: URL is empty or unprovided."
        )

    try:
        parsed = urlsplit(cleaned)
    except Exception as exc:
        raise ValueError(
            f"Malformed {role_label}: Failed to parse URL structure ({exc})."
        ) from exc

    if not parsed.scheme:
        raise ValueError(
            f"Malformed {role_label}: URL is missing a scheme (e.g. 'postgresql://')."
        )

    scheme_lower = parsed.scheme.lower()
    if scheme_lower not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Invalid {role_label} scheme '{parsed.scheme}'. ProcessTwin requires "
            f"a PostgreSQL connection string starting with 'postgresql://' or 'postgres://'."
        )

    hostname = parsed.hostname
    if not hostname:
        raise ValueError(
            f"Malformed {role_label}: URL is missing a hostname. "
            "Expected format: postgresql://[user[:password]@]host[:port]/dbname"
        )

    dbname = parsed.path.lstrip("/")
    if not dbname:
        raise ValueError(
            f"Malformed {role_label}: URL is missing a database name in the path. "
            "Expected format: postgresql://[user[:password]@]host[:port]/dbname"
        )

    port = parsed.port or 5432

    # Query parameters & sslmode handling
    query_params = parse_qs(parsed.query, keep_blank_values=True)
    if hostname.lower() not in _LOCAL_HOSTS and "sslmode" not in query_params:
        query_params["sslmode"] = ["require"]

    new_query = urlencode(query_params, doseq=True)
    normalized_url = urlunsplit(
        ("postgresql", parsed.netloc, parsed.path, new_query, "")
    )

    # Verify libpq connection string syntax compatibility
    try:
        conninfo_to_dict(normalized_url)
    except Exception as exc:
        raise ValueError(
            f"Malformed {role_label}: Invalid libpq connection syntax ({exc})."
        ) from exc

    masked = mask_url(cleaned)

    return TargetInfo(
        raw_scheme=parsed.scheme,
        username=parsed.username,
        hostname=hostname,
        port=port,
        dbname=dbname,
        normalized_url=normalized_url,
        masked_url=masked,
    )


def compare_targets(
    admin_target: TargetInfo | None,
    app_target: TargetInfo | None,
    admin_res: dict[str, object],
    app_res: dict[str, object],
) -> tuple[str, str]:
    """Compare targets safely, returning (status_label, detail_message).

    Status is 'UNKNOWN' if either connection failed, never claiming 'YES'.
    """
    if not admin_target or not app_target:
        return "N/A (single role provided)", "Provide both --admin and --app to compare targets."

    admin_ok = admin_res.get("connection_status") == "SUCCESS"
    app_ok = app_res.get("connection_status") == "SUCCESS"

    if not admin_ok or not app_ok:
        failed_roles: list[str] = []
        if not admin_ok:
            failed_roles.append(f"Admin ({admin_res.get('error', 'connection failed')})")
        if not app_ok:
            failed_roles.append(f"App ({app_res.get('error', 'connection failed')})")
        return (
            "UNKNOWN (connection failed for one or both roles)",
            f"Could not verify database identity because connection failed: {'; '.join(failed_roles)}",
        )

    admin_db = str(admin_res.get("database") or admin_target.dbname)
    app_db = str(app_res.get("database") or app_target.dbname)

    hosts_match = admin_target.hostname.lower() == app_target.hostname.lower()
    ports_match = admin_target.port == app_target.port
    dbs_match = admin_db == app_db

    if hosts_match and ports_match and dbs_match:
        return (
            "YES (same database)",
            f"Both roles successfully connected to database '{admin_db}' on {admin_target.hostname}:{admin_target.port}",
        )

    mismatches: list[str] = []
    if not hosts_match:
        mismatches.append(f"Host mismatch: admin '{admin_target.hostname}' vs app '{app_target.hostname}'")
    if not ports_match:
        mismatches.append(f"Port mismatch: admin '{admin_target.port}' vs app '{app_target.port}'")
    if not dbs_match:
        mismatches.append(f"Database mismatch: admin '{admin_db}' vs app '{app_db}'")

    return "NO (MISMATCH!)", "; ".join(mismatches)


def inspect_role(label: str, target: TargetInfo) -> dict[str, object]:
    """Execute read-only queries to inspect role context, schema, and tables."""
    print(f"\n{'=' * 70}")
    print(f"[{label}] Inspecting: {target.masked_url}")
    print(f"{'=' * 70}")

    results: dict[str, object] = {
        "label": label,
        "masked_url": target.masked_url,
        "connection_status": "PENDING",
    }

    try:
        with psycopg.connect(
            target.normalized_url,
            autocommit=True,
            connect_timeout=10,
            options="-c default_transaction_read_only=on -c search_path=public",
        ) as conn:
            results["connection_status"] = "SUCCESS"
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

                # 3. Available schemas & USAGE privilege
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
                schemas_info: list[dict[str, object]] = []
                for s_name, s_owner in schemas:
                    has_usage = False
                    try:
                        cur.execute(
                            "SELECT has_schema_privilege(current_user, %s, 'USAGE');",
                            (s_name,),
                        )
                        u_row = cur.fetchone()
                        has_usage = bool(u_row and u_row[0])
                    except Exception as exc:
                        logger.debug("Failed checking schema privilege: %s", exc)
                    print(f"    - {s_name} (owner: {s_owner}, USAGE privilege: {has_usage})")
                    schemas_info.append({"name": s_name, "owner": s_owner, "usage": has_usage})
                results["schemas"] = schemas_info

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

                # 10. Table-level privilege check for key tables
                key_tables = ["users", "organizations", "plants", "refresh_tokens"]
                print("  Table-level privileges (current_user):")
                privileges_info: dict[str, list[str]] = {}
                for tbl in key_tables:
                    try:
                        privs: list[str] = []
                        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                            cur.execute(
                                "SELECT has_table_privilege(current_user, %s, %s);",
                                (f"public.{tbl}", priv),
                            )
                            has = cur.fetchone()
                            if has and has[0]:
                                privs.append(priv)
                        print(f"    public.{tbl}: {', '.join(privs) if privs else 'NONE'}")
                        privileges_info[tbl] = privs
                    except Exception as exc:
                        print(f"    public.{tbl}: ERROR checking privileges -> {type(exc).__name__}: {exc}")
                results["privileges"] = privileges_info

    except Exception as exc:
        results["connection_status"] = "FAILED"
        results["error"] = f"{type(exc).__name__}: {exc}"
        print(f"  ERROR connecting with {label}: {type(exc).__name__}: {exc}")

    return results


def resolve_urls(
    cli_admin: str, cli_app: str
) -> tuple[str, str]:
    """Resolve admin and app URLs between CLI arguments and environment variables."""
    admin_url = clean_input_url(cli_admin) or clean_input_url(
        os.environ.get("DATABASE_ADMIN_URL")
    )
    app_url = clean_input_url(cli_app) or clean_input_url(
        os.environ.get("DATABASE_URL")
    )
    return admin_url, app_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ProcessTwin Read-Only Production Database Diagnostic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Using CLI flags:
  python scripts/diagnose_db.py --admin "postgresql://owner:pass@host:5432/dbname" --app "postgresql://app:pass@host:5432/dbname"

  # Using environment variables:
  DATABASE_ADMIN_URL="..." DATABASE_URL="..." python scripts/diagnose_db.py

  # Single connection inspection:
  python scripts/diagnose_db.py --app "postgresql://app:pass@host:5432/dbname"
""",
    )
    parser.add_argument(
        "--admin",
        default="",
        help="DATABASE_ADMIN_URL (PostgreSQL owner/migrator role URL). Overrides env var.",
    )
    parser.add_argument(
        "--app",
        default="",
        help="DATABASE_URL (PostgreSQL application non-owner role URL). Overrides env var.",
    )
    args = parser.parse_args(argv)

    raw_admin, raw_app = resolve_urls(args.admin, args.app)

    print("=" * 70)
    print("ProcessTwin Production Database Diagnostic (Read-Only)")
    print("=" * 70)

    if not raw_admin and not raw_app:
        print("\n[ERROR] Neither DATABASE_ADMIN_URL nor DATABASE_URL was provided.")
        print("\nPlease supply connection strings using CLI flags or environment variables:")
        print("  python scripts/diagnose_db.py --admin '<admin_url>' --app '<app_url>'")
        print("  Or:")
        print("  DATABASE_ADMIN_URL='...' DATABASE_URL='...' python scripts/diagnose_db.py")
        print("\nRun with --help for full documentation and examples.")
        return 1

    # Parse and validate URLs before attempting any network connection
    admin_target: TargetInfo | None = None
    app_target: TargetInfo | None = None
    validation_failed = False

    if raw_admin:
        try:
            admin_target = parse_and_validate_url(raw_admin, "DATABASE_ADMIN_URL")
        except ValueError as exc:
            print(f"\n[VALIDATION ERROR] {exc}")
            validation_failed = True

    if raw_app:
        try:
            app_target = parse_and_validate_url(raw_app, "DATABASE_URL")
        except ValueError as exc:
            print(f"\n[VALIDATION ERROR] {exc}")
            validation_failed = True

    if validation_failed:
        print("\nDiagnostics aborted due to URL validation errors. Please check the connection strings.")
        return 1

    admin_res: dict[str, object] = {}
    app_res: dict[str, object] = {}

    if admin_target:
        admin_res = inspect_role("DATABASE_ADMIN_URL (Owner/Migrator)", admin_target)
    else:
        print("\n[DATABASE_ADMIN_URL]: NOT SET (Skipped admin inspection)")

    if app_target:
        app_res = inspect_role("DATABASE_URL (App Non-Owner)", app_target)
    else:
        print("\n[DATABASE_URL]: NOT SET (Skipped application inspection)")

    print(f"\n{'=' * 70}")
    print("DIAGNOSTIC SUMMARY & ROOT CAUSE ANALYSIS")
    print(f"{'=' * 70}")

    # Safe target comparison
    match_status, match_detail = compare_targets(
        admin_target, app_target, admin_res, app_res
    )
    print(f"1. Database target match: {match_status}")
    print(f"   Detail: {match_detail}")

    # Check connection health
    admin_connected = admin_res.get("connection_status") == "SUCCESS" if admin_target else None
    app_connected = app_res.get("connection_status") == "SUCCESS" if app_target else None

    if (admin_target and not admin_connected) or (app_target and not app_connected):
        print("\n[CRITICAL: CONNECTION FAILURE]")
        if admin_target and not admin_connected:
            print(f"  - Admin role failed to connect: {admin_res.get('error')}")
        if app_target and not app_connected:
            print(f"  - App role failed to connect: {app_res.get('error')}")
        print("  - Please check network access, credentials, and SSL settings.")
        return 1

    # Check findings
    admin_av = admin_res.get("alembic_version")
    admin_users = admin_res.get("users_locations")
    app_uq = app_res.get("users_unqualified")

    if admin_av and not admin_users:
        print("\n[CRITICAL: SCHEMA / REVISION MISMATCH]")
        print("  - alembic_version contains revisions, but 'users' table does NOT exist in the database.")
        print("  - Cause: The database was stamped or initialized with alembic_version without executing DDL.")
        print("  - ACTION REQUIRED:")
        print("    1. Review the tables listed above to confirm no application data exists in any schema.")
        print("    2. If the database is truly empty except for alembic_version, manually run:")
        print("         DELETE FROM public.alembic_version;")
        print("       using the admin connection string.")
        print("    3. Redeploy. The migration runner will then execute all DDL from scratch.")
        print("  - DO NOT auto-repair without inspecting. Other tables or data may exist.")
    elif app_uq and app_uq != "SUCCESS":
        print("\n[CRITICAL: PERMISSION OR SEARCH_PATH ISSUE]")
        print(f"  - App role query failed with: {app_uq}")
        print("  - Solution: Ensure the app role has USAGE on schema public and table DML privileges.")
    else:
        print("\n[STATUS: OK]")
        print("  - No obvious schema or permission anomalies detected.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
