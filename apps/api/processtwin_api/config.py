from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

_VALID_ENVIRONMENTS = frozenset({"development", "test", "production"})
_VALID_JWT_ALGORITHMS = frozenset({"HS256", "HS384", "HS512"})
_INSECURE_SECRETS = frozenset(
    {
        "",
        "development-only-secret-change-me",
        "replace-with-a-long-random-secret-in-production",
        "changeme",
    }
)


@dataclass(frozen=True)
class Settings:
    """Configuration loaded only from the process environment.

    Production intentionally has no credential or signing-key defaults. Local developers may
    use a generated value in their shell, but deployment automation must inject secrets through
    its secret manager.
    """

    environment: str
    log_level: str
    database_url: str
    database_pool_size: int
    database_max_overflow: int
    database_pool_timeout_s: int
    database_connect_timeout_s: int
    jwt_secret: str
    jwt_algorithm: str
    jwt_issuer: str
    jwt_audience: str
    access_token_expire_minutes: int
    refresh_token_expire_days: int
    cors_origins: tuple[str, ...]
    max_upload_bytes: int
    max_request_bytes: int
    rate_limit_requests: int
    rate_limit_window_seconds: int
    login_rate_limit_requests: int
    metrics_token: str | None
    mlflow_tracking_uri: str | None
    demo_email: str | None
    demo_password: str | None
    minio_endpoint: str | None
    minio_access_key: str | None
    minio_secret_key: str | None
    minio_bucket: str | None
    minio_secure: bool

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def auto_create_schema(self) -> bool:
        # Production schema changes are exclusively the migration job's responsibility.
        return self.environment in {"development", "test"}

    @classmethod
    def from_environment(cls) -> Settings:
        def integer(name: str, default: int, *, minimum: int = 1) -> int:
            try:
                value = int(os.getenv(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if value < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
            return value

        environment = os.getenv("ENVIRONMENT", "development").lower().strip()
        if environment not in _VALID_ENVIRONMENTS:
            raise ValueError("ENVIRONMENT must be development, test, or production")

        database_url = os.getenv("DATABASE_URL", "sqlite:///./processtwin.db").strip()
        if not database_url:
            raise ValueError("DATABASE_URL is required")
        if environment == "production" and not urlparse(database_url).scheme.startswith(
            "postgresql"
        ):
            raise ValueError("Production requires a PostgreSQL DATABASE_URL")

        jwt_secret = os.getenv("JWT_SECRET", "").strip()
        if jwt_secret in _INSECURE_SECRETS:
            raise ValueError("JWT_SECRET must be supplied through the environment")
        if len(jwt_secret) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        if environment == "production" and secrets.compare_digest(
            jwt_secret, "test-secret-not-for-deployment-0123456789"
        ):
            raise ValueError("Production JWT_SECRET must not use the test signing key")

        jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256").upper().strip()
        if jwt_algorithm not in _VALID_JWT_ALGORITHMS:
            raise ValueError("JWT_ALGORITHM must be one of HS256, HS384, or HS512")

        origins = tuple(
            origin.strip().rstrip("/")
            for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
            if origin.strip()
        )
        if not origins or "*" in origins:
            raise ValueError("CORS_ORIGINS must contain explicit origins and never '*'")
        for origin in origins:
            parsed_origin = urlparse(origin)
            if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.netloc:
                raise ValueError("Every CORS origin must be an absolute HTTP(S) origin")
            if environment == "production" and parsed_origin.scheme != "https":
                raise ValueError("Production CORS origins must use HTTPS")

        metrics_token = os.getenv("METRICS_TOKEN") or None
        if environment == "production" and (metrics_token is None or len(metrics_token) < 32):
            raise ValueError("Production requires METRICS_TOKEN with at least 32 characters")

        demo_email = os.getenv("DEMO_EMAIL") or None
        demo_password = os.getenv("DEMO_PASSWORD") or None
        if environment == "production" and (demo_email or demo_password):
            raise ValueError("DEMO_EMAIL and DEMO_PASSWORD must not be configured in production")

        max_upload_bytes = integer("MAX_UPLOAD_BYTES", 5_000_000)
        max_request_bytes = integer("MAX_REQUEST_BYTES", max_upload_bytes + 256_000)
        if max_request_bytes < max_upload_bytes:
            raise ValueError("MAX_REQUEST_BYTES must be at least MAX_UPLOAD_BYTES")

        return cls(
            environment=environment,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper().strip(),
            database_url=database_url,
            database_pool_size=integer("DATABASE_POOL_SIZE", 10),
            database_max_overflow=integer("DATABASE_MAX_OVERFLOW", 20, minimum=0),
            database_pool_timeout_s=integer("DATABASE_POOL_TIMEOUT_SECONDS", 30),
            database_connect_timeout_s=integer("DATABASE_CONNECT_TIMEOUT_SECONDS", 10),
            jwt_secret=jwt_secret,
            jwt_algorithm=jwt_algorithm,
            jwt_issuer=os.getenv("JWT_ISSUER", "processtwin-api").strip(),
            jwt_audience=os.getenv("JWT_AUDIENCE", "processtwin-web").strip(),
            access_token_expire_minutes=integer("ACCESS_TOKEN_EXPIRE_MINUTES", 30),
            refresh_token_expire_days=integer("REFRESH_TOKEN_EXPIRE_DAYS", 7),
            cors_origins=origins,
            max_upload_bytes=max_upload_bytes,
            max_request_bytes=max_request_bytes,
            rate_limit_requests=integer("RATE_LIMIT_REQUESTS", 120),
            rate_limit_window_seconds=integer("RATE_LIMIT_WINDOW_SECONDS", 60),
            login_rate_limit_requests=integer("LOGIN_RATE_LIMIT_REQUESTS", 10),
            metrics_token=metrics_token,
            mlflow_tracking_uri=os.getenv("MLFLOW_TRACKING_URI") or None,
            demo_email=demo_email,
            demo_password=demo_password,
            minio_endpoint=os.getenv("MINIO_ENDPOINT") or None,
            minio_access_key=os.getenv("MINIO_ACCESS_KEY") or None,
            minio_secret_key=os.getenv("MINIO_SECRET_KEY") or None,
            minio_bucket=os.getenv("MINIO_BUCKET") or None,
            minio_secure=os.getenv("MINIO_SECURE", "true").lower() == "true",
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()
