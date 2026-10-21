"""Run production migrations once before starting the API server."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import NoReturn
from urllib.parse import urlparse

_MIGRATION_MODULE = "apps.api.processtwin_api.migration_runner"
_API_APP = "apps.api.processtwin_api.main:app"


def _database_environments(
    environ: dict[str, str],
) -> tuple[dict[str, str], dict[str, str]]:
    runtime_url = environ.get("DATABASE_URL", "").strip()
    admin_url = environ.get("DATABASE_ADMIN_URL", "").strip()
    if not runtime_url or not admin_url:
        raise RuntimeError("DATABASE_URL and DATABASE_ADMIN_URL are required at API startup")
    if runtime_url.startswith("postgres://"):
        runtime_url = "postgresql://" + runtime_url[len("postgres://") :]
    if admin_url.startswith("postgres://"):
        admin_url = "postgresql://" + admin_url[len("postgres://") :]
    runtime = urlparse(runtime_url)
    admin = urlparse(admin_url)
    is_test_sqlite = environ.get("ENVIRONMENT") == "test" and runtime_url.startswith("sqlite")
    if not is_test_sqlite:
        if not runtime.scheme.startswith("postgresql"):
            raise RuntimeError("DATABASE_URL must target PostgreSQL at API startup")
        if not admin.scheme.startswith("postgresql"):
            raise RuntimeError("DATABASE_ADMIN_URL must target PostgreSQL at API startup")
        if (
            runtime.hostname,
            runtime.port,
            runtime.path,
        ) != (
            admin.hostname,
            admin.port,
            admin.path,
        ):
            raise RuntimeError("DATABASE_URL and DATABASE_ADMIN_URL must target the same database")
        if not runtime.username or not admin.username or runtime.username == admin.username:
            if environ.get("ALLOW_SAME_DATABASE_ROLE", "").lower() not in ("1", "true", "yes"):
                raise RuntimeError("DATABASE_URL and DATABASE_ADMIN_URL must use separate database roles")

    app_role = runtime.username or ""
    migration_environ = {
        **environ,
        "DATABASE_URL": admin_url,
        "APP_ROLE": app_role,
    }
    migration_environ.pop("DATABASE_ADMIN_URL", None)
    api_environ = environ.copy()
    api_environ["DATABASE_URL"] = runtime_url
    api_environ.pop("DATABASE_ADMIN_URL", None)
    return migration_environ, api_environ


def _verify_database_readiness(runtime_url: str) -> None:
    """Verify that the application non-owner role can query the required tables before exec'ing API."""
    if os.environ.get("ENVIRONMENT") == "test" and "example.test" in runtime_url:
        return
    if runtime_url.startswith("postgres://"):
        runtime_url = "postgresql://" + runtime_url[len("postgres://") :]
    from sqlalchemy import create_engine, text

    is_sqlite = runtime_url.startswith("sqlite")
    connect_args = (
        {}
        if is_sqlite
        else {"connect_timeout": 5, "options": "-c search_path=public"}
    )
    probe_engine = create_engine(
        runtime_url,
        connect_args=connect_args,
    )
    try:
        with probe_engine.connect() as connection:
            connection.execute(text("SELECT 1 FROM users LIMIT 1;"))
    except Exception as exc:
        raise RuntimeError(
            f"Pre-flight database readiness check failed for application role: {exc}. "
            "Ensure migrations completed successfully and the application role has USAGE and SELECT privileges."
        ) from exc
    finally:
        probe_engine.dispose()


def start_api() -> NoReturn:
    """Complete migrations before exec'ing one Gunicorn master process."""
    migration_environ, api_environ = _database_environments(dict(os.environ))
    port = api_environ.get("PORT", "8000")
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise RuntimeError("PORT must be a valid TCP port")

    subprocess.run(  # noqa: S603 - fixed module invocation, no shell, no user-controlled arguments
        [sys.executable, "-m", _MIGRATION_MODULE],
        check=True,
        env=migration_environ,
    )

    _verify_database_readiness(api_environ["DATABASE_URL"])

    os.environ.pop("DATABASE_ADMIN_URL", None)
    command = [
        "gunicorn",
        _API_APP,
        "--bind",
        f"0.0.0.0:{port}",
        "--workers",
        "1",
        "--worker-class",
        "uvicorn.workers.UvicornWorker",
        "--access-logfile",
        "-",
        "--error-logfile",
        "-",
        "--log-level",
        "info",
    ]
    os.execvpe(command[0], command, api_environ)  # noqa: S606 - replace launcher with API master


def main() -> NoReturn:
    start_api()


if __name__ == "__main__":
    main()
