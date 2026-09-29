from __future__ import annotations

from datetime import UTC, datetime

from apps.api.processtwin_api.main import app
from apps.api.processtwin_api.time_utils import as_utc
from fastapi.responses import StreamingResponse


def test_dashboard_sse_endpoint_is_registered() -> None:
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/v1/events/dashboard"
    )
    assert "GET" in route.methods
    assert route.response_class is StreamingResponse


def test_sqlite_style_naive_timestamp_is_interpreted_as_utc() -> None:
    stored = datetime(2026, 9, 29, 12, 30)

    assert as_utc(stored) == datetime(2026, 9, 29, 12, 30, tzinfo=UTC)
