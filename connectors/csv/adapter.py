from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from typing import Any

from apps.api.processtwin_api.contracts import ReadingRequest


def _read_csv_rows(raw: bytes, max_bytes: int = 5_000_000) -> list[dict[str, str]]:
    if len(raw) > max_bytes:
        raise ValueError("CSV exceeds configured size limit")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))
    if not rows:
        raise ValueError("CSV is empty")
    return rows


def parse_csv_dataset(
    raw: bytes,
    max_bytes: int = 5_000_000,
    tag_mapping: Mapping[str, str | Mapping[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Parse CSV data supporting either sensor_id rows or arbitrary plant-tag mappings."""
    rows = _read_csv_rows(raw, max_bytes=max_bytes)
    if tag_mapping is None:
        if not {"timestamp", "value", "unit"}.issubset(rows[0]):
            raise ValueError("Required columns: timestamp,value,unit")
        return [dict(row) for row in rows]

    timestamp_key = "timestamp"
    mapped_rows: list[dict[str, Any]] = []
    for row in rows:
        if timestamp_key not in row:
            raise ValueError("Required columns: timestamp and mapped plant tags")
        record: dict[str, Any] = {"timestamp": row[timestamp_key]}
        for source_tag, target in tag_mapping.items():
            if source_tag not in row or row[source_tag] in (None, ""):
                continue
            if isinstance(target, str):
                target_name = target
                target_unit = None
            else:
                target_name = target.get("name", source_tag)
                target_unit = target.get("unit")
            try:
                record[target_name] = float(row[source_tag])
            except ValueError as exc:
                raise ValueError(f"Value for {source_tag} is not numeric") from exc
            if target_unit is not None:
                record[f"{target_name}_unit"] = target_unit
        if record.keys() != {"timestamp"}:
            mapped_rows.append(record)
    if not mapped_rows:
        raise ValueError("No mapped plant-tag data could be parsed from the CSV")
    return mapped_rows


def parse_csv_rows(raw: bytes, max_bytes: int = 5_000_000) -> list[ReadingRequest]:
    """Parse only UTF-8 structured reading rows; file execution is never possible."""
    rows = _read_csv_rows(raw, max_bytes=max_bytes)
    if not rows or not {"sensor_id", "timestamp", "value", "unit"}.issubset(rows[0]):
        raise ValueError("Required columns: sensor_id,timestamp,value,unit")
    return [ReadingRequest.model_validate(row) for row in rows]
