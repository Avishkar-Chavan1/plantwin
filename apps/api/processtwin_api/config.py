from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    """Typed environment settings, kept dependency-light for local development."""

    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./processtwin.db"
    jwt_secret: str = "development-only-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    cors_origins: list[str] | None = None
    max_upload_bytes: int = 5_000_000
    demo_email: str = "engineer@processtwin.demo"
    demo_password: str = "ChangeMeDemoOnly!"

    def __post_init__(self) -> None:
        if self.cors_origins is None:
            object.__setattr__(self, "cors_origins", ["http://localhost:3000"])

    @classmethod
    def from_environment(cls) -> Settings:
        def integer(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, str(default)))
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc

        return cls(
            environment=os.getenv("ENVIRONMENT", "development"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            database_url=os.getenv("DATABASE_URL", "sqlite:///./processtwin.db"),
            jwt_secret=os.getenv("JWT_SECRET", "development-only-secret-change-me"),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            access_token_expire_minutes=integer("ACCESS_TOKEN_EXPIRE_MINUTES", 30),
            refresh_token_expire_days=integer("REFRESH_TOKEN_EXPIRE_DAYS", 7),
            cors_origins=[
                origin.strip()
                for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
                if origin.strip()
            ],
            max_upload_bytes=integer("MAX_UPLOAD_BYTES", 5_000_000),
            demo_email=os.getenv("DEMO_EMAIL", "engineer@processtwin.demo"),
            demo_password=os.getenv("DEMO_PASSWORD", "ChangeMeDemoOnly!"),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()
