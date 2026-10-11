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
    runtime = urlparse(runtime_url)
    admin = urlparse(admin_url)
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
        raise RuntimeError("DATABASE_URL and DATABASE_ADMIN_URL must use separate database roles")

    migration_environ = {**environ, "DATABASE_URL": admin_url}
    migration_environ.pop("DATABASE_ADMIN_URL", None)
    api_environ = environ.copy()
    api_environ.pop("DATABASE_ADMIN_URL", None)
    return migration_environ, api_environ


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
