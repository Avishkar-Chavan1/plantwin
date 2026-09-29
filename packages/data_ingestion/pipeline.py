from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from statistics import median

from apps.api.processtwin_api.models import QualityStatusName

from packages.units import si_unit, to_si


@dataclass(frozen=True)
class HistoricalMapping:
    source_tag: str
    canonical_name: str
    source_unit: str
    minimum_si: float | None = None
    maximum_si: float | None = None
    expected_sampling_interval_s: int | None = None
    max_rate_of_change_per_s: float | None = None
    max_drift_per_hour: float | None = None


@dataclass(frozen=True)
class HistoricalObservation:
    source_tag: str
    canonical_name: str
    timestamp: datetime | None
    original_timestamp: str
    row_number: int
    original_value: float | None
    original_text: str | None
    original_unit: str | None
    normalized_value: float | None
    normalized_unit: str
    quality_status: QualityStatusName
    quality_reasons: tuple[str, ...]


def _timestamp(value: object) -> tuple[datetime | None, str | None]:
    text = "" if value is None else str(value).strip()
    if not text:
        return None, "TIMESTAMP_MISSING"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, "TIMESTAMP_INVALID"
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None, "TIMESTAMP_TIMEZONE_MISSING"
    return parsed.astimezone(UTC), None


def _drift_per_hour(points: Sequence[tuple[datetime, float]]) -> float:
    if len(points) < 8:
        return 0.0
    window = points[-min(len(points), 24) :]
    start, end = window[0], window[-1]
    hours = (end[0] - start[0]).total_seconds() / 3600
    if hours <= 0:
        return 0.0
    return abs(end[1] - start[1]) / hours


def inspect_historical_rows(
    rows: Sequence[Mapping[str, object]],
    mappings: Sequence[HistoricalMapping],
    *,
    timestamp_column: str = "timestamp",
) -> list[HistoricalObservation]:
    """Normalize each mapped value to SI and classify anomalies without dropping records."""
    if not rows:
        raise ValueError("Historical dataset contains no data rows")
    if not mappings:
        raise ValueError("At least one source tag mapping is required")
    if len({mapping.source_tag for mapping in mappings}) != len(mappings):
        raise ValueError("Each source tag may be mapped only once")
    headers = set(rows[0])
    missing_columns = {mapping.source_tag for mapping in mappings} - headers
    if timestamp_column not in headers or missing_columns:
        names = ", ".join(sorted(missing_columns))
        raise ValueError(
            f"Missing required timestamp or mapped columns: {names or timestamp_column}"
        )

    unit_targets: dict[str, str] = {}
    for mapping in mappings:
        try:
            unit_targets[mapping.source_tag] = si_unit(mapping.source_unit)
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"Unsupported unit for {mapping.source_tag}: {mapping.source_unit}"
            ) from exc

    observations: list[HistoricalObservation] = []
    previous_values: dict[str, list[float]] = {mapping.source_tag: [] for mapping in mappings}
    previous_points: dict[str, list[tuple[datetime, float]]] = {
        mapping.source_tag: [] for mapping in mappings
    }
    latest_times: dict[str, datetime] = {}
    seen_times: dict[str, set[datetime]] = {mapping.source_tag: set() for mapping in mappings}

    for row_number, row in enumerate(rows, start=2):
        timestamp, timestamp_issue = _timestamp(row.get(timestamp_column))
        original_timestamp = "" if row.get(timestamp_column) is None else str(row[timestamp_column])
        for mapping in mappings:
            raw_value = row.get(mapping.source_tag)
            original_text = None if raw_value is None else str(raw_value)
            row_unit_value = row.get(f"{mapping.source_tag}_unit")
            original_unit = (
                str(row_unit_value).strip()
                if row_unit_value is not None and str(row_unit_value).strip()
                else mapping.source_unit
            )
            reasons: list[str] = []
            original_value: float | None = None
            normalized_value: float | None = None
            if timestamp_issue:
                reasons.append(timestamp_issue)
            if timestamp is not None:
                latest = latest_times.get(mapping.source_tag)
                if timestamp in seen_times[mapping.source_tag]:
                    reasons.append("DUPLICATE_TIMESTAMP")
                if latest is not None and timestamp < latest:
                    reasons.append("OUT_OF_ORDER_TIMESTAMP")
                seen_times[mapping.source_tag].add(timestamp)
                if latest is None or timestamp > latest:
                    latest_times[mapping.source_tag] = timestamp

            if raw_value is None or not str(raw_value).strip():
                reasons.append("MISSING_VALUE")
            else:
                try:
                    original_value = float(str(raw_value))
                except (TypeError, ValueError):
                    reasons.append("NON_NUMERIC_VALUE")
                if original_value is not None:
                    if not isfinite(original_value):
                        reasons.append("NON_FINITE_VALUE")
                    else:
                        try:
                            if si_unit(original_unit) != unit_targets[mapping.source_tag]:
                                raise ValueError("Reported unit has an incompatible dimension")
                            normalized_value = to_si(original_value, original_unit)
                        except ValueError:
                            reasons.append("UNIT_MISMATCH")
                            normalized_value = None
                        if normalized_value is not None and not isfinite(normalized_value):
                            reasons.append("NON_FINITE_NORMALIZED_VALUE")
                            normalized_value = None

            if normalized_value is not None:
                if mapping.minimum_si is not None and normalized_value < mapping.minimum_si:
                    reasons.append("BELOW_ENGINEERING_LIMIT")
                if mapping.maximum_si is not None and normalized_value > mapping.maximum_si:
                    reasons.append("ABOVE_ENGINEERING_LIMIT")
                history = previous_values[mapping.source_tag]
                if len(history) >= 4 and all(
                    abs(value - history[-1]) <= 1e-12 for value in history[-4:]
                ):
                    reasons.append("STUCK_SENSOR")
                if len(history) >= 4:
                    baseline = median(history[-8:])
                    deviations = [abs(value - baseline) for value in history[-8:]]
                    robust_spread = median(deviations) * 1.4826
                    floor = max(abs(baseline) * 1e-6, 1e-9)
                    if abs(normalized_value - baseline) > 8 * max(robust_spread, floor):
                        reasons.append("SUDDEN_SPIKE")
                prior = previous_points[mapping.source_tag]
                if timestamp is not None and prior:
                    last_time, last_value = prior[-1]
                    elapsed = (timestamp - last_time).total_seconds()
                    if elapsed > 0:
                        if (
                            mapping.expected_sampling_interval_s
                            and elapsed > mapping.expected_sampling_interval_s * 3
                        ):
                            reasons.append("COMMUNICATION_GAP")
                        rate = abs(normalized_value - last_value) / elapsed
                        if (
                            mapping.max_rate_of_change_per_s is not None
                            and rate > mapping.max_rate_of_change_per_s
                        ):
                            reasons.append("UNREALISTIC_RATE_OF_CHANGE")
                if (
                    timestamp is not None
                    and mapping.max_drift_per_hour is not None
                    and _drift_per_hour(
                        previous_points[mapping.source_tag] + [(timestamp, normalized_value)]
                    )
                    > mapping.max_drift_per_hour
                ):
                    reasons.append("SENSOR_DRIFT")

            if not reasons:
                reasons.append("ALL_CONFIGURED_CHECKS_PASSED")
            if any(
                reason
                in {
                    "TIMESTAMP_MISSING",
                    "TIMESTAMP_INVALID",
                    "TIMESTAMP_TIMEZONE_MISSING",
                    "DUPLICATE_TIMESTAMP",
                    "OUT_OF_ORDER_TIMESTAMP",
                    "NON_NUMERIC_VALUE",
                    "NON_FINITE_VALUE",
                    "NON_FINITE_NORMALIZED_VALUE",
                    "UNIT_MISMATCH",
                    "BELOW_ENGINEERING_LIMIT",
                    "ABOVE_ENGINEERING_LIMIT",
                }
                for reason in reasons
            ):
                quality = QualityStatusName.BAD
            elif "MISSING_VALUE" in reasons:
                quality = QualityStatusName.MISSING
            elif len(reasons) > 1 or reasons[0] != "ALL_CONFIGURED_CHECKS_PASSED":
                quality = QualityStatusName.SUSPECT
            else:
                quality = QualityStatusName.GOOD

            observations.append(
                HistoricalObservation(
                    source_tag=mapping.source_tag,
                    canonical_name=mapping.canonical_name,
                    timestamp=timestamp,
                    original_timestamp=original_timestamp,
                    row_number=row_number,
                    original_value=original_value,
                    original_text=original_text,
                    original_unit=original_unit if original_value is not None else None,
                    normalized_value=normalized_value,
                    normalized_unit=unit_targets[mapping.source_tag],
                    quality_status=quality,
                    quality_reasons=tuple(reasons),
                )
            )
            if normalized_value is not None and timestamp is not None:
                previous_values[mapping.source_tag].append(normalized_value)
                previous_points[mapping.source_tag].append((timestamp, normalized_value))
    return observations


def summarize_quality(observations: Sequence[HistoricalObservation]) -> dict[str, object]:
    counts = {status.value: 0 for status in QualityStatusName}
    reason_counts: dict[str, int] = {}
    for observation in observations:
        counts[observation.quality_status.value] += 1
        for reason in observation.quality_reasons:
            if reason != "ALL_CONFIGURED_CHECKS_PASSED":
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return {
        "measurements": len(observations),
        "status_counts": counts,
        "reason_counts": reason_counts,
    }
