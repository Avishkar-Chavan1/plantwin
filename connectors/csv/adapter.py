from __future__ import annotations

import csv
import io

from apps.api.processtwin_api.contracts import ReadingRequest


def parse_csv_rows(raw: bytes, max_bytes: int = 5_000_000) -> list[ReadingRequest]:
    """Parse only UTF-8 structured reading rows; file execution is never possible."""
    if len(raw) > max_bytes:
        raise ValueError("CSV exceeds configured size limit")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    if not rows or not {"sensor_id", "timestamp", "value", "unit"}.issubset(rows[0]):
        raise ValueError("Required columns: sensor_id,timestamp,value,unit")
    return [ReadingRequest.model_validate(row) for row in rows]
