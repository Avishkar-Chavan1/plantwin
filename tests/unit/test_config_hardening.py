"""Fail-closed settings tests: deployment-critical configuration must refuse to start."""

from __future__ import annotations

import pytest
from apps.api.processtwin_api.config import Settings
from pydantic import ValidationError

PLACEHOLDER_JWT_SECRET = "REPLACE_WITH_A_RANDOM_32_CHARACTER_MINIMUM_SECRET"
PLACEHOLDER_METRICS_TOKEN = "REPLACE_WITH_A_SEPARATE_RANDOM_32_CHARACTER_MINIMUM_TOKEN"


def _settings(**overrides: object) -> Settings:
    """Build settings with every production-relevant field explicit (no ambient .env leakage)."""
    values: dict[str, object] = {
        "environment": "development",
        "database_url": "sqlite:///./processtwin.db",
        "jwt_secret": "unit-test-secret-value-not-for-any-deployment-0123456789",
        "metrics_token": None,
        "cors_origins": "http://localhost:3000",
        "demo_email": None,
        "demo_password": None,
        "oidc_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_development_accepts_generated_secret() -> None:
    assert _settings().environment == "development"


def test_placeholder_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        _settings(jwt_secret=PLACEHOLDER_JWT_SECRET)


def test_short_jwt_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        _settings(jwt_secret="too-short")


def test_placeholder_metrics_token_is_rejected() -> None:
    with pytest.raises(ValidationError, match="METRICS_TOKEN"):
        _settings(metrics_token=PLACEHOLDER_METRICS_TOKEN)


def test_production_requires_postgresql() -> None:
    with pytest.raises(ValidationError, match="PostgreSQL"):
        _settings(
            environment="production",
            metrics_token="a" * 48,
            cors_origins="https://operator.example.com",
        )


def test_production_rejects_http_cors_origin() -> None:
    with pytest.raises(ValidationError, match="HTTPS"):
        _settings(
            environment="production",
            database_url="postgresql+psycopg://user:pass@db.example.com/processtwin",
            metrics_token="a" * 48,
            cors_origins="http://operator.example.com",
        )


def test_production_rejects_demo_credentials() -> None:
    with pytest.raises(ValidationError, match="DEMO_"):
        _settings(
            environment="production",
            database_url="postgresql+psycopg://user:pass@db.example.com/processtwin",
            metrics_token="a" * 48,
            cors_origins="https://operator.example.com",
            demo_email="engineer@processtwin.demo",
            demo_password="ChangeMeDemoOnly!",
        )


def test_production_accepts_a_valid_configuration() -> None:
    settings = _settings(
        environment="production",
        database_url="postgresql+psycopg://user:pass@db.example.com/processtwin",
        metrics_token="b" * 48,
        cors_origins="https://operator.example.com",
    )
    assert settings.is_production is True
    assert settings.auto_create_schema is False
