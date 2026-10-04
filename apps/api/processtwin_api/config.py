from __future__ import annotations

import secrets
from functools import lru_cache
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_VALID_ENVIRONMENTS = frozenset({"development", "test", "production"})
_VALID_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
_INSECURE_SECRETS = frozenset(
    {
        "",
        "development-only-secret-change-me",
        "replace-with-a-long-random-secret-in-production",
        "changeme",
        # Verbatim placeholders shipped in .env.example; they must never deploy.
        "REPLACE_WITH_A_RANDOM_32_CHARACTER_MINIMUM_SECRET",
        "REPLACE_WITH_A_SEPARATE_RANDOM_32_CHARACTER_MINIMUM_TOKEN",
    }
)

# Test override - set by test suite to bypass environment validation
_test_settings_override: Settings | None = None


def set_test_settings_override(settings: Settings) -> None:
    """Set a test settings override for the test suite."""
    global _test_settings_override
    _test_settings_override = settings


def clear_test_settings_override() -> None:
    """Clear the test settings override."""
    global _test_settings_override
    _test_settings_override = None


class Settings(BaseSettings):
    """Configuration loaded only from the process environment.

    Production intentionally has no credential or signing-key defaults. Local developers may
    use a generated value in their shell, but deployment automation must inject secrets through
    its secret manager.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./processtwin.db"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout_s: int = 30
    database_connect_timeout_s: int = 10
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "processtwin-api"
    jwt_audience: str = "processtwin-web"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    cors_origins: str = "http://localhost:3000"
    max_upload_bytes: int = 5_000_000
    max_request_bytes: int = 5_256_000
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60
    login_rate_limit_requests: int = 10
    metrics_token: str | None = None
    mlflow_tracking_uri: str | None = None
    demo_email: str | None = None
    demo_password: str | None = None
    minio_endpoint: str | None = None
    minio_access_key: str | None = None
    minio_secret_key: str | None = None
    minio_bucket: str | None = None
    minio_secure: bool = True

    # Password policy
    password_min_length: int = 12
    password_require_uppercase: bool = True
    password_require_lowercase: bool = True
    password_require_digits: bool = True
    password_require_special: bool = True

    # OIDC/SSO integration point (optional)
    oidc_enabled: bool = False
    oidc_issuer_url: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_scopes: str = "openid,email,profile"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def auto_create_schema(self) -> bool:
        # Production schema changes are exclusively the migration job's responsibility.
        return self.environment in {"development", "test"}

    @property
    def cors_origins_parsed(self) -> tuple[str, ...]:
        origins = tuple(
            origin.strip().rstrip("/")
            for origin in self.cors_origins.split(",")
            if origin.strip()
        )
        if not origins or "*" in origins:
            raise ValueError("CORS_ORIGINS must contain explicit origins and never '*'")
        for origin in origins:
            parsed_origin = urlparse(origin)
            if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.netloc:
                raise ValueError("Every CORS origin must be an absolute HTTP(S) origin")
        return origins

    @property
    def oidc_scopes_parsed(self) -> tuple[str, ...]:
        return tuple(s.strip() for s in self.oidc_scopes.split(",") if s.strip())

    @field_validator("environment", mode="before")
    @classmethod
    def _validate_environment(cls, v: str) -> str:
        v = v.lower().strip()
        if v not in _VALID_ENVIRONMENTS:
            raise ValueError("ENVIRONMENT must be development, test, or production")
        return v

    @field_validator("database_url", mode="before")
    @classmethod
    def _validate_database_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("DATABASE_URL is required")
        return v

    @field_validator("jwt_secret", mode="before")
    @classmethod
    def _validate_jwt_secret(cls, v: str) -> str:
        v = v.strip()
        if v in _INSECURE_SECRETS:
            raise ValueError("JWT_SECRET must be supplied through the environment")
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return v

    @field_validator("jwt_algorithm", mode="before")
    @classmethod
    def _validate_jwt_algorithm(cls, v: str) -> str:
        v = v.upper().strip()
        if v not in _VALID_JWT_ALGORITHMS:
            raise ValueError("JWT_ALGORITHM must be one of HS256, HS384, or HS512")
        return v

    @field_validator("metrics_token")
    @classmethod
    def _validate_metrics_token(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                return None
            if v in _INSECURE_SECRETS:
                raise ValueError("METRICS_TOKEN must be a generated token, not a placeholder")
        return v

    @model_validator(mode="after")
    def _validate_production_requirements(self) -> Settings:
        if self.environment == "production":
            if not urlparse(self.database_url).scheme.startswith("postgresql"):
                raise ValueError("Production requires a PostgreSQL DATABASE_URL")
            if secrets.compare_digest(
                self.jwt_secret, "test-secret-not-for-deployment-0123456789"
            ):
                raise ValueError("Production JWT_SECRET must not use the test signing key")
            if self.metrics_token is None or len(self.metrics_token) < 32:
                raise ValueError("Production requires METRICS_TOKEN with at least 32 characters")
            if self.demo_email or self.demo_password:
                raise ValueError("DEMO_EMAIL and DEMO_PASSWORD must not be configured in production")
            for origin in self.cors_origins_parsed:
                parsed_origin = urlparse(origin)
                if parsed_origin.scheme != "https":
                    raise ValueError("Production CORS origins must use HTTPS")
            if self.oidc_enabled and (not self.oidc_issuer_url or not self.oidc_client_id):
                raise ValueError("OIDC issuer URL and client ID required when OIDC is enabled")
        if self.max_request_bytes < self.max_upload_bytes:
            raise ValueError("MAX_REQUEST_BYTES must be at least MAX_UPLOAD_BYTES")
        return self

    def validate_password(self, password: str) -> tuple[bool, list[str]]:
        """Validate password against policy. Returns (is_valid, list_of_errors)."""
        errors: list[str] = []
        if len(password) < self.password_min_length:
            errors.append(f"Password must be at least {self.password_min_length} characters")
        if self.password_require_uppercase and not any(c.isupper() for c in password):
            errors.append("Password must contain at least one uppercase letter")
        if self.password_require_lowercase and not any(c.islower() for c in password):
            errors.append("Password must contain at least one lowercase letter")
        if self.password_require_digits and not any(c.isdigit() for c in password):
            errors.append("Password must contain at least one digit")
        if self.password_require_special and not any(not c.isalnum() for c in password):
            errors.append("Password must contain at least one special character")
        return len(errors) == 0, errors


@lru_cache
def get_settings() -> Settings:
    if _test_settings_override is not None:
        return _test_settings_override
    # jwt_secret is intentionally required with no default; pydantic-settings injects it
    # from the JWT_SECRET environment variable, so fail-closed validation still applies.
    return Settings()  # type: ignore[call-arg]
