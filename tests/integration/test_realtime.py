from __future__ import annotations

from fastapi.responses import StreamingResponse

from apps.api.processtwin_api.main import app


def test_dashboard_sse_endpoint_is_registered() -> None:
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", None) == "/api/v1/events/dashboard"
    )
    assert "GET" in route.methods
    assert route.response_class is StreamingResponse
