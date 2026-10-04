from __future__ import annotations

import io
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from statistics import median
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from apps.api.processtwin_api.models import QualityStatusName
from connectors.historical import read_historical_source
from connectors.object_storage import (
    ObjectStorageService,
    generate_chunk_key,
)

from packages.data_ingestion.pipeline import (
    HistoricalMapping,
    HistoricalObservation,
)
from packages.units import si_unit, to_si


def _load_pyarrow() -> tuple[Any, Any]:
    """Import the optional Parquet stack, or return ``(None, None)`` when unavailable.

    pyarrow ships no type information, so the adapter boundary is deliberately typed as
    ``Any``: the strict type gate then looks identical whether or not the ``parquet``
    extra is installed.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return None, None
    return pa, pq


pa, pq = _load_pyarrow()
PYARROW_AVAILABLE = pa is not None and pq is not None


@dataclass
class ChunkResult:
    chunk_index: int
    start_row: int
    end_row: int
    observations: list[HistoricalObservation]
    quality_summary: dict[str, Any]
    object_path: str | None = None
    object_size_bytes: int | None = None
    object_checksum_sha256: str | None = None
    rows_processed: int = 0
    rows_failed: int = 0
    error_message: str | None = None
    status: str = "PENDING"


@dataclass
class ImportJobResult:
    job_id: UUID
    total_chunks: int
    total_rows: int
    processed_rows: int
    failed_rows: int
    chunk_results: list[ChunkResult]
    final_quality_summary: dict[str, Any]


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


def _process_rows_chunk(
    rows: Sequence[Mapping[str, object]],
    mappings: Sequence[HistoricalMapping],
    timestamp_column: str,
    start_row_number: int,
) -> list[HistoricalObservation]:
    """Process a chunk of rows into observations."""
    if not rows:
        return []
    if not mappings:
        raise ValueError("At least one source tag mapping is required")

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

    for row_idx, row in enumerate(rows):
        row_number = start_row_number + row_idx
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


def _summarize_quality(observations: Sequence[HistoricalObservation]) -> dict[str, object]:
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


def _merge_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {
        "measurements": 0,
        "status_counts": {status.value: 0 for status in QualityStatusName},
        "reason_counts": {},
    }
    for summary in summaries:
        merged["measurements"] = int(merged["measurements"]) + int(summary.get("measurements", 0))
        for status, count in summary.get("status_counts", {}).items():
            merged["status_counts"][status] = (
                int(merged["status_counts"].get(status, 0)) + int(count)
            )
        for reason, count in summary.get("reason_counts", {}).items():
            merged["reason_counts"][reason] = (
                int(merged["reason_counts"].get(reason, 0)) + int(count)
            )
    return merged


def _write_chunk_to_parquet(
    observations: list[HistoricalObservation], output_stream: BinaryIO
) -> None:
    """Write observations to a Parquet stream."""
    if not PYARROW_AVAILABLE:
        raise RuntimeError("Parquet support requires pyarrow package")
    import pyarrow as pa

    # Convert to PyArrow table
    data: dict[str, list[Any]] = {
        "source_tag": [],
        "canonical_name": [],
        "timestamp": [],
        "original_timestamp": [],
        "row_number": [],
        "original_value": [],
        "original_text": [],
        "original_unit": [],
        "normalized_value": [],
        "normalized_unit": [],
        "quality_status": [],
        "quality_reasons": [],
    }
    for obs in observations:
        data["source_tag"].append(obs.source_tag)
        data["canonical_name"].append(obs.canonical_name)
        data["timestamp"].append(obs.timestamp.isoformat() if obs.timestamp else None)
        data["original_timestamp"].append(obs.original_timestamp)
        data["row_number"].append(obs.row_number)
        data["original_value"].append(obs.original_value)
        data["original_text"].append(obs.original_text)
        data["original_unit"].append(obs.original_unit)
        data["normalized_value"].append(obs.normalized_value)
        data["normalized_unit"].append(obs.normalized_unit)
        data["quality_status"].append(obs.quality_status.value)
        data["quality_reasons"].append(json.dumps(obs.quality_reasons))

    table = pa.table(data)
    pq.write_table(table, output_stream)


def read_chunk_from_parquet(input_stream: BinaryIO) -> list[HistoricalObservation]:
    """Read observations from a Parquet stream."""
    if not PYARROW_AVAILABLE:
        raise RuntimeError("Parquet support requires pyarrow package")

    table = pq.read_table(input_stream)
    observations = []
    for row in table.to_pylist():
        observations.append(
            HistoricalObservation(
                source_tag=row["source_tag"],
                canonical_name=row["canonical_name"],
                timestamp=datetime.fromisoformat(row["timestamp"]) if row["timestamp"] else None,
                original_timestamp=row["original_timestamp"],
                row_number=row["row_number"],
                original_value=row["original_value"],
                original_text=row["original_text"],
                original_unit=row["original_unit"],
                normalized_value=row["normalized_value"],
                normalized_unit=row["normalized_unit"],
                quality_status=QualityStatusName(row["quality_status"]),
                quality_reasons=tuple(json.loads(row["quality_reasons"])),
            )
        )
    return observations


class ChunkedImportProcessor:
    """Processes large historical data imports in chunks with object storage."""

    def __init__(
        self,
        chunk_size: int = 10000,
        object_storage: ObjectStorageService | None = None,
    ) -> None:
        self.chunk_size = chunk_size
        self.object_storage = object_storage or ObjectStorageService()

    def process_file(
        self,
        file_bytes: bytes,
        filename: str,
        mappings: Sequence[HistoricalMapping],
        timestamp_column: str = "timestamp",
        organization_id: UUID | None = None,
        job_id: UUID | None = None,
    ) -> ImportJobResult:
        """Process a file in chunks, storing intermediate results in object storage."""
        # Read all rows first to determine chunking
        rows = read_historical_source(filename, file_bytes)
        total_rows = len(rows)
        total_chunks = math.ceil(total_rows / self.chunk_size)

        if total_chunks == 1:
            # Small file - process directly without object storage
            observations = _process_rows_chunk(
                rows, mappings, timestamp_column, start_row_number=2
            )
            quality_summary = _summarize_quality(observations)
            return ImportJobResult(
                job_id=job_id or uuid4(),
                total_chunks=1,
                total_rows=total_rows,
                processed_rows=len(observations),
                failed_rows=sum(1 for o in observations if o.quality_status == QualityStatusName.BAD),
                chunk_results=[
                    ChunkResult(
                        chunk_index=0,
                        start_row=2,
                        end_row=total_rows + 1,
                        observations=observations,
                        quality_summary=quality_summary,
                        rows_processed=len(observations),
                        rows_failed=sum(
                            1 for o in observations if o.quality_status == QualityStatusName.BAD
                        ),
                    )
                ],
                final_quality_summary=quality_summary,
            )

        # Large file - process in chunks with object storage
        chunk_results: list[ChunkResult] = []
        all_summaries: list[dict[str, object]] = []

        for chunk_index in range(total_chunks):
            start_idx = chunk_index * self.chunk_size
            end_idx = min(start_idx + self.chunk_size, total_rows)
            chunk_rows = rows[start_idx:end_idx]
            start_row_number = start_idx + 2  # +2 because enumerate starts at 2 and header is row 1

            try:
                observations = _process_rows_chunk(
                    chunk_rows, mappings, timestamp_column, start_row_number
                )
                quality_summary = _summarize_quality(observations)
                all_summaries.append(quality_summary)

                # Store chunk in object storage
                chunk_buffer = io.BytesIO()
                _write_chunk_to_parquet(observations, chunk_buffer)
                chunk_buffer.seek(0)

                object_path = None
                object_size = None
                object_checksum = None
                if organization_id and job_id:
                    object_key = generate_chunk_key(organization_id, job_id, chunk_index)
                    chunk_buffer.seek(0)
                    upload_result = self.object_storage.upload_streaming(
                        chunk_buffer, object_key, content_type="application/parquet"
                    )
                    object_path = f"{upload_result.bucket}/{upload_result.object_key}"
                    object_size = upload_result.size_bytes
                    object_checksum = upload_result.checksum_sha256

                chunk_results.append(
                    ChunkResult(
                        chunk_index=chunk_index,
                        start_row=start_row_number,
                        end_row=start_row_number + len(chunk_rows) - 1,
                        observations=observations,
                        quality_summary=quality_summary,
                        object_path=object_path,
                        object_size_bytes=object_size,
                        object_checksum_sha256=object_checksum,
                        rows_processed=len(observations),
                        rows_failed=sum(
                            1 for o in observations if o.quality_status == QualityStatusName.BAD
                        ),
                    )
                )
            except Exception as exc:
                chunk_results.append(
                    ChunkResult(
                        chunk_index=chunk_index,
                        start_row=start_row_number,
                        end_row=end_idx + 1,
                        observations=[],
                        quality_summary={},
                        error_message=str(exc),
                        rows_failed=len(chunk_rows),
                    )
                )

        final_summary = _merge_summaries(all_summaries)
        return ImportJobResult(
            job_id=job_id or uuid4(),
            total_chunks=total_chunks,
            total_rows=total_rows,
            processed_rows=sum(r.rows_processed for r in chunk_results),
            failed_rows=sum(r.rows_failed for r in chunk_results),
            chunk_results=chunk_results,
            final_quality_summary=final_summary,
        )

    def resume_from_chunks(
        self,
        chunk_results: list[ChunkResult],
        organization_id: UUID,
        job_id: UUID,
    ) -> list[HistoricalObservation]:
        """Resume processing by reading completed chunks from object storage."""
        all_observations: list[HistoricalObservation] = []
        for chunk in chunk_results:
            if chunk.object_path and chunk.status == "COMPLETED":
                try:
                    data = self.object_storage.download(chunk.object_path.split("/", 1)[1])
                    chunk_buffer = io.BytesIO(data)
                    observations = read_chunk_from_parquet(chunk_buffer)
                    all_observations.extend(observations)
                except Exception:
                    # If chunk read fails, it will be retried
                    pass
        return all_observations


def process_chunked_import(
    file_bytes: bytes,
    filename: str,
    mappings: Sequence[HistoricalMapping],
    timestamp_column: str = "timestamp",
    chunk_size: int = 10000,
    organization_id: UUID | None = None,
    job_id: UUID | None = None,
) -> ImportJobResult:
    """Convenience function for chunked import processing."""
    processor = ChunkedImportProcessor(chunk_size=chunk_size)
    return processor.process_file(
        file_bytes, filename, mappings, timestamp_column, organization_id, job_id
    )