"""Test-only process configuration, loaded before application modules are imported."""

from __future__ import annotations

import getpass
import os
import tempfile
from pathlib import Path

import pytest


def _configure_test_temp_root() -> None:
    """Point pytest's ``tmp_path`` root at a directory this process can delete.

    pytest creates its basetemp with mode ``0o700``. On Windows that yields an
    owner-only ACL, so a *fixed* temp root (the OS ``pytest-of-<user>`` directory or
    a fixed repo-local ``--basetemp``) becomes permanently unusable for every other
    account on the host: the first account to run the suite locks the rest out, and
    each locked-out account then fails at session start with access denied before a
    single test runs.

    Resolving a per-account root here, with a writability probe and a fallback, keeps
    ``make test`` runnable for every account. This runs while conftest is imported,
    which is before pytest builds its basetemp, so setting ``tempfile.tempdir`` is
    still effective.
    """
    repo_root = Path(__file__).resolve().parents[1]
    user = getpass.getuser()
    candidates = (
        repo_root / ".pytest-tmp" / user,
        Path(tempfile.gettempdir()) / f"processtwin-tests-{user}",
    )
    for candidate in candidates:
        try:
            # Default mode (0o777) is deliberate: these directories must inherit the
            # caller's ACEs instead of receiving pytest's owner-only 0o700 ACL.
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / f".probe-{os.getpid()}"
            probe.write_text("probe", encoding="utf-8")
            probe.unlink()
        except OSError:
            continue
        root = str(candidate)
        tempfile.tempdir = root
        os.environ["TMPDIR"] = root
        os.environ["TEMP"] = root
        os.environ["TMP"] = root
        return
    # Both candidates unusable would be an environment prerequisite failure; leave
    # the platform default alone so the resulting message is the platform's own.


_configure_test_temp_root()

# Set test environment BEFORE any modules are imported
os.environ.setdefault("ENVIRONMENT", "test")
# CI may point the suite at a real PostgreSQL service (see .github/workflows/ci.yml);
# local runs keep the default SQLite database.
_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite:///./processtwin_test.db")
os.environ.setdefault("DATABASE_URL", _TEST_DATABASE_URL)
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-deployment-0123456789")
os.environ.setdefault("JWT_ISSUER", "processtwin-api")
os.environ.setdefault("JWT_AUDIENCE", "processtwin-web")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Set test settings override before any test modules are imported."""
    print("pytest_sessionstart: Setting test settings override...")
    import apps.api.processtwin_api.config as config_module
    from apps.api.processtwin_api.config import Settings, set_test_settings_override

    # Create test settings and set override
    test_settings = Settings(
        environment="test",
        log_level="INFO",
        database_url=_TEST_DATABASE_URL,
        database_pool_size=10,
        database_max_overflow=20,
        database_pool_timeout_s=30,
        database_connect_timeout_s=10,
        jwt_secret="test-secret-not-for-deployment-0123456789",
        jwt_algorithm="HS256",
        jwt_issuer="processtwin-api",
        jwt_audience="processtwin-web",
        access_token_expire_minutes=30,
        refresh_token_expire_days=7,
        cors_origins="http://localhost:3000",
        max_upload_bytes=5_000_000,
        max_request_bytes=5_256_000,
        rate_limit_requests=120,
        rate_limit_window_seconds=60,
        login_rate_limit_requests=10,
        metrics_token=None,
        mlflow_tracking_uri=None,
        demo_email="engineer@processtwin.demo",
        demo_password="ChangeMeDemoOnly!",
        minio_endpoint=None,
        minio_access_key=None,
        minio_secret_key=None,
        minio_bucket=None,
        minio_secure=True,
        password_min_length=12,
        password_require_uppercase=True,
        password_require_lowercase=True,
        password_require_digits=True,
        password_require_special=True,
        oidc_enabled=False,
        oidc_issuer_url=None,
        oidc_client_id=None,
        oidc_client_secret=None,
        oidc_scopes="openid,email,profile",
    )

    # Set the test settings override - this is checked by get_settings()
    set_test_settings_override(test_settings)
    # Clear any cached version
    config_module.get_settings.cache_clear()
    print("pytest_sessionstart: Test settings override set successfully")


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Ensure each test gets a clean rate limiter state."""
    from apps.api.processtwin_api.rate_limit import RATE_LIMITER

    RATE_LIMITER.reset()
    yield
    RATE_LIMITER.reset()
