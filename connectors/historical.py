from __future__ import annotations

import csv
import io
from typing import Protocol


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


class RestHistoricalSource:
    """Future REST adapter contract; transport and credentials are deliberately injected."""

    def read(self, raw: bytes) -> list[dict[str, object]]:
        raise NotImplementedError("REST source transport is not configured")


class DatabaseHistoricalSource:
    """Future database adapter contract; callers must provide a read-only connection."""

    def read(self, raw: bytes) -> list[dict[str, object]]:
        raise NotImplementedError("Database source transport is not configured")