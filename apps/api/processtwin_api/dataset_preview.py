"""Pure helpers behind the dataset upload preview (no database access, no side effects).

The preview lets an engineer inspect detected columns, confirm which one is the timestamp,
map the remaining columns to process variables with units, and see the validation outcome
*before* anything is stored.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, tzinfo
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TIMESTAMP_NAMES = {"timestamp", "time", "datetime", "date_time", "date", "ts", "t"}

# Name heuristics only *suggest* a mapping. The engineer must confirm every row; nothing is
# imported on the strength of a guess.
_SUGGESTIONS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"^(ti|tt|tic|temp\w*)([_\-.\d]|$)", re.I), "reactor.temperature", "degC"),
    (re.compile(r"^(pi|pt|pic|press\w*)([_\-.\d]|$)", re.I), "reactor.pressure", "bar"),
    (re.compile(r"^(fi|ft|fic|flow\w*)([_\-.\d]|$)", re.I), "reactor.feed_flow", "m3/h"),
    (re.compile(r"^(li|lt|lic|level\w*)([_\-.\d]|$)", re.I), "reactor.level", "%"),
    (re.compile(r"^(si|st|sic|speed\w*|rpm\w*)([_\-.\d]|$)", re.I), "agitator.speed", "rpm"),
    (re.compile(r"^(ai|at|aic|conc\w*)([_\-.\d]|$)", re.I), "reactor.feed_concentration", "mol/L"),
)


class TimezoneError(ValueError):
    """Raised for an unknown IANA time-zone name."""


def parse_assumed_timezone(name: str | None) -> tzinfo | None:
    """Resolve an explicit user-chosen zone for timestamps that carry no offset."""
    if name is None or not name.strip():
        return None
    cleaned = name.strip()
    if cleaned.upper() in {"UTC", "Z", "GMT"}:
        return UTC
    try:
        return ZoneInfo(cleaned)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise TimezoneError(f"Unknown time zone '{cleaned}'. Use an IANA name such as UTC.") from exc


def _parse_timestamp(value: object, assume: tzinfo | None) -> tuple[datetime | None, str | None]:
    text = "" if value is None else str(value).strip()
    if not text:
        return None, "missing"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, "invalid"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        if assume is None:
            return None, "no_timezone"
        parsed = parsed.replace(tzinfo=assume)
    return parsed.astimezone(UTC), None


def _is_number(value: object) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    try:
        return isfinite(float(text))
    except ValueError:
        return False


def _is_blank(value: object) -> bool:
    return value is None or not str(value).strip()


def analyze_columns(rows: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    """Describe each column: inferred kind, fill rate and a few sample values."""
    if not rows:
        return []
    total = len(rows)
    result: list[dict[str, Any]] = []
    for name in rows[0]:
        values = [row.get(name) for row in rows]
        non_blank = [value for value in values if not _is_blank(value)]
        numeric = sum(_is_number(value) for value in non_blank)
        iso = sum(
            _parse_timestamp(value, UTC)[0] is not None
            and not _is_number(value)
            and any(ch in str(value) for ch in "-:/T")
            for value in non_blank
        )
        kind = "empty"
        if non_blank:
            if numeric / len(non_blank) >= 0.95:
                kind = "numeric"
            elif iso / len(non_blank) >= 0.95:
                kind = "timestamp"
            else:
                kind = "text"
        result.append(
            {
                "name": str(name),
                "kind": kind,
                "non_empty_count": len(non_blank),
                "empty_count": total - len(non_blank),
                "numeric_fraction": round(numeric / len(non_blank), 4) if non_blank else 0.0,
                "sample": [str(value)[:64] for value in non_blank[:3]],
            }
        )
    return result


def guess_timestamp_column(columns: Sequence[Mapping[str, Any]], requested: str | None) -> str | None:
    names = [str(column["name"]) for column in columns]
    if requested and requested in names:
        return requested
    for name in names:
        if name.strip().lower() in _TIMESTAMP_NAMES:
            return name
    for column in columns:
        if column["kind"] == "timestamp":
            return str(column["name"])
    return None


def analyze_timestamps(
    rows: Sequence[Mapping[str, object]], column: str, assume: tzinfo | None
) -> dict[str, Any]:
    parsed: list[datetime] = []
    missing = invalid = no_timezone = 0
    for row in rows:
        value, issue = _parse_timestamp(row.get(column), assume)
        if value is not None:
            parsed.append(value)
        elif issue == "missing":
            missing += 1
        elif issue == "no_timezone":
            no_timezone += 1
        else:
            invalid += 1
    duplicates = len(parsed) - len(set(parsed))
    out_of_order = sum(1 for a, b in zip(parsed, parsed[1:], strict=False) if b < a)
    issues: list[str] = []
    if no_timezone:
        issues.append(
            f"{no_timezone} timestamps have no UTC offset. Choose the time zone they were "
            "recorded in, otherwise those rows are rejected."
        )
    if invalid:
        issues.append(f"{invalid} timestamps are not ISO-8601 (for example 2026-01-31T14:05:00Z).")
    if missing:
        issues.append(f"{missing} rows have an empty timestamp.")
    if duplicates:
        issues.append(f"{duplicates} rows repeat an earlier timestamp and are flagged as bad data.")
    if out_of_order:
        issues.append(f"{out_of_order} rows are out of chronological order and are flagged as bad data.")
    return {
        "column": column,
        "parsed_count": len(parsed),
        "missing_count": missing,
        "invalid_count": invalid,
        "no_timezone_count": no_timezone,
        "duplicate_count": duplicates,
        "out_of_order_count": out_of_order,
        "first": min(parsed).isoformat() if parsed else None,
        "last": max(parsed).isoformat() if parsed else None,
        "issues": issues,
    }


def suggest_mappings(
    columns: Sequence[Mapping[str, Any]], timestamp_column: str | None
) -> list[dict[str, Any]]:
    """Propose (never apply) a canonical name and unit for each numeric column."""
    names = {str(column["name"]) for column in columns}
    suggestions: list[dict[str, Any]] = []
    for column in columns:
        name = str(column["name"])
        if name == timestamp_column or column["kind"] != "numeric":
            continue
        if name.endswith("_unit") and name[: -len("_unit")] in names:
            continue  # per-row unit column consumed by the importer, not a measurement
        canonical, unit = None, None
        for pattern, candidate_name, candidate_unit in _SUGGESTIONS:
            if pattern.match(name):
                canonical, unit = candidate_name, candidate_unit
                break
        suggestions.append(
            {
                "source_tag": name,
                "canonical_name": canonical,
                "unit": unit,
                "plant_tag": name,
                "suggested": canonical is not None,
            }
        )
    return suggestions


def sample_rows(rows: Sequence[Mapping[str, object]], limit: int = 8) -> list[dict[str, str]]:
    return [
        {str(key): ("" if value is None else str(value)[:64]) for key, value in row.items()}
        for row in rows[:limit]
    ]
