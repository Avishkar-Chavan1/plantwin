"""Test-only process configuration, loaded before application modules are imported."""

from __future__ import annotations

import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:///./processtwin_test.db")
os.environ.setdefault("JWT_SECRET", "test-secret-not-for-deployment-0123456789")
os.environ.setdefault("JWT_ISSUER", "processtwin-api")
os.environ.setdefault("JWT_AUDIENCE", "processtwin-web")
