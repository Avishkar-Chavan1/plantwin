from __future__ import annotations

import csv
import io
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urljoin

import httpx
import sqlalchemy as sa
from sqlalchemy.engine import Engine


class HistoricalSource(Protocol):
    """Read-only boundary for file, REST, or database historical-source adapters."""

    def read(self, raw: bytes) -> list[dict[str, object]]: ...


class CsvHistoricalSource:
    def read(self, raw: bytes) -> list[dict[str, object]]:
        try:
            text = raw.decode("utf-8-sig")
            reader = csv.DictReader(io.StringIO(text))
            if not reader.fieldnames:
                raise ValueError("CSV must contain a header row")
            return [dict(row) for row in reader]
        except (UnicodeDecodeError, csv.Error) as exc:
            raise ValueError("CSV must be valid UTF-8 tabular data") from exc


class ParquetHistoricalSource:
    def read(self, raw: bytes) -> list[dict[str, object]]:
        try:
            import pyarrow.parquet as parquet  # type: ignore[import-untyped]
        except ImportError as exc:
            raise RuntimeError("Parquet support requires the 'parquet' project extra") from exc
        try:
            table = parquet.read_table(io.BytesIO(raw))
            return [dict(row) for row in table.to_pylist()]
        except Exception as exc:
            raise ValueError("Parquet file could not be decoded") from exc


def read_historical_source(file_name: str, raw: bytes) -> list[dict[str, object]]:
    extension = file_name.lower().rsplit(".", maxsplit=1)[-1]
    source: HistoricalSource
    if extension == "csv":
        source = CsvHistoricalSource()
    elif extension in {"parquet", "pq"}:
        source = ParquetHistoricalSource()
    else:
        raise ValueError("Supported historical data extensions are .csv, .parquet, and .pq")
    rows = source.read(raw)
    if not rows:
        raise ValueError("Historical dataset contains no data rows")
    return rows


@dataclass(frozen=True)
class RestSourceConfig:
    """Configuration for REST historical source."""
    base_url: str
    endpoint: str
    auth_type: str = "bearer"  # bearer, basic, api_key
    token: str | None = None
    username: str | None = None
    password: str | None = None
    api_key: str | None = None
    api_key_header: str = "X-API-Key"
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    page_size: int = 1000
    timestamp_column: str = "timestamp"
    start_time: datetime | None = None
    end_time: datetime | None = None
    additional_params: dict[str, str] | None = None
    headers: dict[str, str] | None = None


@dataclass(frozen=True)
class DatabaseSourceConfig:
    """Configuration for database historical source."""
    connection_string: str
    query: str
    timestamp_column: str = "timestamp"
    chunk_size: int = 10000
    params: dict[str, Any] | None = None


class RestHistoricalSource:
    """REST API historical source adapter with pagination, retries, and sampling awareness."""

    def __init__(self, config: RestSourceConfig) -> None:
        self.config = config
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            headers = {"Accept": "application/json"}
            if self.config.headers:
                headers.update(self.config.headers)
            if self.config.auth_type == "bearer" and self.config.token:
                headers["Authorization"] = f"Bearer {self.config.token}"
            elif self.config.auth_type == "api_key" and self.config.api_key:
                headers[self.config.api_key_header] = self.config.api_key

            auth = None
            if self.config.auth_type == "basic" and self.config.username and self.config.password:
                auth = (self.config.username, self.config.password)

            self._client = httpx.Client(
                base_url=self.config.base_url,
                headers=headers,
                auth=auth,
                timeout=self.config.timeout_seconds,
            )
        return self._client

    def read(self, raw: bytes) -> list[dict[str, object]]:
        """Read all pages from REST endpoint."""
        all_rows: list[dict[str, object]] = []
        client = self._get_client()
        url = self.config.endpoint
        params = dict(self.config.additional_params or {})

        if self.config.start_time:
            params["start_time"] = self.config.start_time.isoformat()
        if self.config.end_time:
            params["end_time"] = self.config.end_time.isoformat()
        params["limit"] = str(self.config.page_size)
        offset = 0

        while True:
            params["offset"] = str(offset)
            response = self._request_with_retry(client, "GET", url, params=params)
            data = response.json()

            if isinstance(data, dict):
                rows = data.get("data", data.get("results", data.get("items", [])))
                if not rows:
                    break
            elif isinstance(data, list):
                rows = data
                if not rows:
                    break
            else:
                raise ValueError(f"Unexpected response format: {type(data)}")

            all_rows.extend(rows)
            if len(rows) < self.config.page_size:
                break
            offset += len(rows)

        if not all_rows:
            raise ValueError("REST historical source returned no data rows")
        return all_rows

    def _request_with_retry(self, client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = client.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_backoff_seconds * (2 ** attempt))
                else:
                    raise
        raise last_exc  # type: ignore[misc]

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self) -> RestHistoricalSource:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class DatabaseHistoricalSource:
    """Database historical source adapter with chunked reading and sampling awareness."""

    def __init__(self, config: DatabaseSourceConfig) -> None:
        self.config = config
        self._engine: Engine | None = None

    def _get_engine(self) -> Engine:
        if self._engine is None:
            self._engine = sa.create_engine(
                self.config.connection_string,
                pool_pre_ping=True,
                pool_recycle=3600,
            )
        return self._engine

    def read(self, raw: bytes) -> list[dict[str, object]]:
        """Read data from database using configured query with chunking."""
        engine = self._get_engine()
        query = self.config.query
        params = dict(self.config.params or {})

        all_rows: list[dict[str, object]] = []
        offset = 0
        chunk_size = self.config.chunk_size

        with engine.connect() as conn:
            while True:
                paginated_query = f"{query} LIMIT {chunk_size} OFFSET {offset}"
                result = conn.execute(sa.text(paginated_query), params)
                rows = [dict(row._mapping) for row in result]
                if not rows:
                    break
                all_rows.extend(rows)
                if len(rows) < chunk_size:
                    break
                offset += len(rows)

        if not all_rows:
            raise ValueError("Database historical source returned no data rows")
        return all_rows

    def read_streaming(self, callback: callable) -> int:
        """Stream data from database in chunks, calling callback for each chunk."""
        engine = self._get_engine()
        query = self.config.query
        params = dict(self.config.params or {})
        chunk_size = self.config.chunk_size
        offset = 0
        total_rows = 0

        with engine.connect() as conn:
            while True:
                paginated_query = f"{query} LIMIT {chunk_size} OFFSET {offset}"
                result = conn.execute(sa.text(paginated_query), params)
                rows = [dict(row._mapping) for row in result]
                if not rows:
                    break
                callback(rows)
                total_rows += len(rows)
                if len(rows) < chunk_size:
                    break
                offset += len(rows)

        return total_rows

    def close(self) -> None:
        if self._engine:
            self._engine.dispose()
            self._engine = None

    def __enter__(self) -> DatabaseHistoricalSource:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


class SamplingAwareRestSource(RestHistoricalSource):
    """REST source with sampling rate detection and adaptive fetching."""

    def __init__(self, config: RestSourceConfig, expected_interval_seconds: float = 60.0) -> None:
        super().__init__(config)
        self.expected_interval = expected_interval_seconds
        self.detected_interval: float | None = None

    def read_with_sampling_detection(self, raw: bytes) -> tuple[list[dict[str, object]], float]:
        """Read data and detect actual sampling interval."""
        rows = self.read(raw)
        if len(rows) < 2:
            return rows, self.expected_interval

        timestamps = []
        ts_col = self.config.timestamp_column
        for row in rows:
            ts_val = row.get(ts_col)
            if ts_val:
                try:
                    if isinstance(ts_val, str):
                        ts = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
                    elif isinstance(ts_val, datetime):
                        ts = ts_val
                    else:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    timestamps.append(ts)
                except (ValueError, TypeError):
                    continue

        if len(timestamps) >= 2:
            timestamps.sort()
            intervals = [(timestamps[i+1] - timestamps[i]).total_seconds() for i in range(len(timestamps)-1)]
            self.detected_interval = sum(intervals) / len(intervals)
        else:
            self.detected_interval = self.expected_interval

        return rows, self.detected_interval


class SamplingAwareDatabaseSource(DatabaseHistoricalSource):
    """Database source with sampling rate detection and gap analysis."""

    def __init__(self, config: DatabaseSourceConfig, expected_interval_seconds: float = 60.0) -> None:
        super().__init__(config)
        self.expected_interval = expected_interval_seconds
        self.detected_interval: float | None = None
        self.gaps: list[tuple[datetime, datetime, float]] = []

    def read_with_sampling_analysis(self, raw: bytes) -> tuple[list[dict[str, object]], float, list[tuple[datetime, datetime, float]]]:
        """Read data and analyze sampling intervals and gaps."""
        rows = self.read(raw)
        if len(rows) < 2:
            return rows, self.expected_interval, []

        timestamps = []
        ts_col = self.config.timestamp_column
        for row in rows:
            ts_val = row.get(ts_col)
            if ts_val:
                try:
                    if isinstance(ts_val, str):
                        ts = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
                    elif isinstance(ts_val, datetime):
                        ts = ts_val
                    else:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=UTC)
                    timestamps.append(ts)
                except (ValueError, TypeError):
                    continue

        if len(timestamps) >= 2:
            timestamps.sort()
            intervals = [(timestamps[i+1] - timestamps[i]).total_seconds() for i in range(len(timestamps)-1)]
            self.detected_interval = sum(intervals) / len(intervals)

            # Detect gaps (interval > 3x expected)
            for i, interval in enumerate(intervals):
                if interval > self.expected_interval * 3:
                    self.gaps.append((timestamps[i], timestamps[i+1], interval))
        else:
            self.detected_interval = self.expected_interval

        return rows, self.detected_interval, self.gaps