from __future__ import annotations

from apps.api.processtwin_api.contracts import ReadingRequest


class RestReadingAdapter:
    """Validates connector payloads before they cross into the application ingestion service."""

    def parse(self, payload: dict[str, object]) -> ReadingRequest:
        return ReadingRequest.model_validate(payload)
