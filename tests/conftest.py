"""Test-only process configuration, loaded before application modules are imported."""

from __future__ import annotations

import os

import pytest

# Set test environment BEFORE any modules are imported
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///./processtwin_test.db")
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
        database_url="sqlite:///./processtwin_test.db",
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
        cors_origins=("http://localhost:3000",),
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
