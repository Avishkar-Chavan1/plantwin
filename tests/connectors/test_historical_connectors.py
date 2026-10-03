"""Tests for read-only historian connectors (REST and database)."""

from __future__ import annotations

from unittest.mock import Mock, patch

import httpx
import pytest
import sqlalchemy as sa
from connectors.historical import (
    CsvHistoricalSource,
    DatabaseHistoricalSource,
    DatabaseSourceConfig,
    ParquetHistoricalSource,
    RestHistoricalSource,
    RestSourceConfig,
    SamplingAwareDatabaseSource,
    SamplingAwareRestSource,
    read_historical_source,
)


class TestCsvHistoricalSource:
    def test_read_valid_csv(self) -> None:
        source = CsvHistoricalSource()
        csv_data = b"timestamp,value\n2024-01-01T00:00:00Z,1.0\n2024-01-01T00:01:00Z,2.0"
        rows = source.read(csv_data)
        assert len(rows) == 2
        assert rows[0]["timestamp"] == "2024-01-01T00:00:00Z"
        assert rows[0]["value"] == "1.0"

    def test_read_empty_csv_raises(self) -> None:
        source = CsvHistoricalSource()
        csv_data = b"timestamp,value\n"
        with pytest.raises(ValueError, match="no data rows"):
            source.read(csv_data)

    def test_read_invalid_csv_raises(self) -> None:
        source = CsvHistoricalSource()
        csv_data = b"not valid csv\xff\xfe"
        with pytest.raises(ValueError, match="UTF-8"):
            source.read(csv_data)

    def test_read_missing_header_raises(self) -> None:
        source = CsvHistoricalSource()
        csv_data = b"\n"
        with pytest.raises(ValueError, match="header row"):
            source.read(csv_data)


class TestParquetHistoricalSource:
    def test_read_valid_parquet(self) -> None:
        pytest.importorskip("pyarrow")
        import io

        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table({
            "timestamp": ["2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z"],
            "value": [1.0, 2.0],
        })
        buf = io.BytesIO()
        pq.write_table(table, buf)
        parquet_data = buf.getvalue()

        source = ParquetHistoricalSource()
        rows = source.read(parquet_data)
        assert len(rows) == 2
        assert rows[0]["timestamp"] == "2024-01-01T00:00:00Z"

    def test_read_invalid_parquet_raises(self) -> None:
        pytest.importorskip("pyarrow")
        source = ParquetHistoricalSource()
        with pytest.raises(ValueError, match="decoded"):
            source.read(b"not parquet")

    def test_read_missing_pyarrow_raises(self) -> None:
        source = ParquetHistoricalSource()
        # Patch the import to raise ImportError
        import builtins
        original_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "pyarrow.parquet":
                raise ImportError("No module named 'pyarrow.parquet'")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="parquet"):
                source.read(b"data")


class TestReadHistoricalSource:
    def test_csv_extension(self) -> None:
        csv_data = b"timestamp,value\n2024-01-01T00:00:00Z,1.0"
        rows = read_historical_source("data.csv", csv_data)
        assert len(rows) == 1

    def test_parquet_extension(self) -> None:
        pytest.importorskip("pyarrow")
        import io

        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table({"timestamp": ["2024-01-01T00:00:00Z"], "value": [1.0]})
        buf = io.BytesIO()
        pq.write_table(table, buf)
        parquet_data = buf.getvalue()

        rows = read_historical_source("data.parquet", parquet_data)
        assert len(rows) == 1

    def test_pq_extension(self) -> None:
        pytest.importorskip("pyarrow")
        import io

        import pyarrow as pa
        import pyarrow.parquet as pq

        table = pa.table({"timestamp": ["2024-01-01T00:00:00Z"], "value": [1.0]})
        buf = io.BytesIO()
        pq.write_table(table, buf)
        parquet_data = buf.getvalue()

        rows = read_historical_source("data.pq", parquet_data)
        assert len(rows) == 1

    def test_unsupported_extension_raises(self) -> None:
        with pytest.raises(ValueError, match="Supported historical data extensions"):
            read_historical_source("data.txt", b"data")


class TestRestHistoricalSource:
    @pytest.fixture
    def config(self) -> RestSourceConfig:
        return RestSourceConfig(
            base_url="https://api.example.com",
            endpoint="/historical/data",
            auth_type="bearer",
            token="test-token",
            page_size=2,
            timeout_seconds=5,
            max_retries=1,
        )

    def test_init_creates_client_lazily(self, config: RestSourceConfig) -> None:
        source = RestHistoricalSource(config)
        assert source._client is None
        client = source._get_client()
        assert client is not None
        assert client.base_url == "https://api.example.com"
        assert "Authorization" in client.headers
        assert client.headers["Authorization"] == "Bearer test-token"

    def test_read_paginates(self, config: RestSourceConfig) -> None:
        source = RestHistoricalSource(config)

        # Mock the client
        mock_client = Mock()
        mock_response1 = Mock()
        mock_response1.json.return_value = {
            "data": [
                {"timestamp": "2024-01-01T00:00:00Z", "value": 1.0},
                {"timestamp": "2024-01-01T00:01:00Z", "value": 2.0},
            ]
        }
        mock_response1.raise_for_status.return_value = None

        mock_response2 = Mock()
        mock_response2.json.return_value = {
            "data": [
                {"timestamp": "2024-01-01T00:02:00Z", "value": 3.0},
            ]
        }
        mock_response2.raise_for_status.return_value = None

        mock_response3 = Mock()
        mock_response3.json.return_value = {"data": []}
        mock_response3.raise_for_status.return_value = None

        mock_client.request.side_effect = [mock_response1, mock_response2, mock_response3]
        source._client = mock_client

        rows = source.read(b"")
        assert len(rows) == 3
        assert mock_client.request.call_count == 3

    def test_read_with_list_response(self, config: RestSourceConfig) -> None:
        source = RestHistoricalSource(config)
        mock_client = Mock()
        # First call returns list with 2 items, second call returns empty list (pagination confirmation)
        mock_response1 = Mock()
        mock_response1.json.return_value = [
            {"timestamp": "2024-01-01T00:00:00Z", "value": 1.0},
            {"timestamp": "2024-01-01T00:01:00Z", "value": 2.0},
        ]
        mock_response1.raise_for_status.return_value = None
        mock_response2 = Mock()
        mock_response2.json.return_value = []
        mock_response2.raise_for_status.return_value = None
        mock_client.request.side_effect = [mock_response1, mock_response2]
        source._client = mock_client

        rows = source.read(b"")
        assert len(rows) == 2

    def test_read_empty_raises(self, config: RestSourceConfig) -> None:
        source = RestHistoricalSource(config)
        mock_client = Mock()
        mock_response = Mock()
        mock_response.json.return_value = {"data": []}
        mock_response.raise_for_status.return_value = None
        mock_client.request.return_value = mock_response
        source._client = mock_client

        with pytest.raises(ValueError, match="no data rows"):
            source.read(b"")

    def test_retry_on_failure(self, config: RestSourceConfig) -> None:
        source = RestHistoricalSource(config)
        mock_client = Mock()
        # 3 calls: 1st fails, 2nd succeeds with 1 item, 3rd confirms no more data
        mock_client.request.side_effect = [
            httpx.RequestError("Connection failed"),
            Mock(json=lambda: {"data": [{"timestamp": "2024-01-01T00:00:00Z", "value": 1.0}]}, raise_for_status=lambda: None),
            Mock(json=lambda: {"data": []}, raise_for_status=lambda: None),
        ]
        source._client = mock_client

        rows = source.read(b"")
        assert len(rows) == 1
        assert mock_client.request.call_count == 3

    def test_context_manager(self, config: RestSourceConfig) -> None:
        with RestHistoricalSource(config) as source:
            assert source._get_client() is not None
        assert source._client is None or source._client.is_closed


class TestRestSourceConfig:
    def test_basic_auth(self) -> None:
        config = RestSourceConfig(
            base_url="https://api.example.com",
            endpoint="/data",
            auth_type="basic",
            username="user",
            password="pass",
        )
        source = RestHistoricalSource(config)
        client = source._get_client()
        assert client.auth is not None
        assert client.auth._auth_header == "Basic dXNlcjpwYXNz"

    def test_api_key_auth(self) -> None:
        config = RestSourceConfig(
            base_url="https://api.example.com",
            endpoint="/data",
            auth_type="api_key",
            api_key="secret-key",
            api_key_header="X-Custom-Key",
        )
        source = RestHistoricalSource(config)
        client = source._get_client()
        assert client.headers["X-Custom-Key"] == "secret-key"

    def test_custom_headers(self) -> None:
        config = RestSourceConfig(
            base_url="https://api.example.com",
            endpoint="/data",
            headers={"X-Custom": "value"},
        )
        source = RestHistoricalSource(config)
        client = source._get_client()
        assert client.headers["X-Custom"] == "value"


class TestDatabaseHistoricalSource:
    @pytest.fixture
    def mock_engine(self) -> Mock:
        return Mock(spec=sa.Engine)

    def test_read_chunks(self) -> None:
        config = DatabaseSourceConfig(
            connection_string="postgresql://user:pass@localhost/db",
            query="SELECT * FROM historical_data WHERE timestamp > :start",
            params={"start": "2024-01-01"},
            chunk_size=2,
        )
        source = DatabaseHistoricalSource(config)

        # Mock engine and connection
        mock_conn = Mock()
        mock_result1 = Mock()
        mock_row1 = Mock()
        mock_row1._mapping = {"timestamp": "2024-01-01T00:00:00Z", "value": 1.0}
        mock_row2 = Mock()
        mock_row2._mapping = {"timestamp": "2024-01-01T00:01:00Z", "value": 2.0}
        mock_result1.__iter__ = Mock(return_value=iter([mock_row1, mock_row2]))
        mock_result2 = Mock()
        mock_row3 = Mock()
        mock_row3._mapping = {"timestamp": "2024-01-01T00:02:00Z", "value": 3.0}
        mock_result2.__iter__ = Mock(return_value=iter([mock_row3]))
        mock_result3 = Mock()
        mock_result3.__iter__ = Mock(return_value=iter([]))

        mock_conn.execute.side_effect = [mock_result1, mock_result2, mock_result3]

        mock_engine = Mock()
        mock_context = Mock()
        mock_context.__enter__ = Mock(return_value=mock_conn)
        mock_context.__exit__ = Mock(return_value=None)
        mock_engine.connect.return_value = mock_context
        source._engine = mock_engine

        rows = source.read(b"")
        assert len(rows) == 3
        assert mock_conn.execute.call_count == 3

    def test_read_streaming(self) -> None:
        config = DatabaseSourceConfig(
            connection_string="postgresql://user:pass@localhost/db",
            query="SELECT * FROM historical_data",
            chunk_size=2,
        )
        source = DatabaseHistoricalSource(config)

        mock_conn = Mock()
        mock_result1 = Mock()
        mock_row1 = Mock()
        mock_row1._mapping = {"timestamp": "2024-01-01T00:00:00Z", "value": 1.0}
        mock_row2 = Mock()
        mock_row2._mapping = {"timestamp": "2024-01-01T00:01:00Z", "value": 2.0}
        mock_result1.__iter__ = Mock(return_value=iter([mock_row1, mock_row2]))
        mock_result2 = Mock()
        mock_row3 = Mock()
        mock_row3._mapping = {"timestamp": "2024-01-01T00:02:00Z", "value": 3.0}
        mock_result2.__iter__ = Mock(return_value=iter([mock_row3]))
        mock_result3 = Mock()
        mock_result3.__iter__ = Mock(return_value=iter([]))

        mock_conn.execute.side_effect = [mock_result1, mock_result2, mock_result3]

        mock_engine = Mock()
        mock_context = Mock()
        mock_context.__enter__ = Mock(return_value=mock_conn)
        mock_context.__exit__ = Mock(return_value=None)
        mock_engine.connect.return_value = mock_context
        source._engine = mock_engine

        chunks_received = []
        def callback(rows):
            chunks_received.append(rows)

        total = source.read_streaming(callback)
        assert total == 3
        assert len(chunks_received) == 2
        assert len(chunks_received[0]) == 2
        assert len(chunks_received[1]) == 1

    def test_read_empty_raises(self) -> None:
        config = DatabaseSourceConfig(
            connection_string="postgresql://user:pass@localhost/db",
            query="SELECT * FROM historical_data",
        )
        source = DatabaseHistoricalSource(config)

        mock_conn = Mock()
        mock_result = Mock()
        mock_result._mapping = []
        mock_result.__iter__ = Mock(return_value=iter([]))

        mock_conn.execute.return_value = mock_result

        mock_engine = Mock()
        mock_context = Mock()
        mock_context.__enter__ = Mock(return_value=mock_conn)
        mock_context.__exit__ = Mock(return_value=None)
        mock_engine.connect.return_value = mock_context
        source._engine = mock_engine

        with pytest.raises(ValueError, match="no data rows"):
            source.read(b"")


class TestSamplingAwareRestSource:
    def test_sampling_detection(self) -> None:
        config = RestSourceConfig(
            base_url="https://api.example.com",
            endpoint="/data",
            timestamp_column="timestamp",
        )
        source = SamplingAwareRestSource(config, expected_interval_seconds=60.0)

        # Mock read to return data with known interval
        with patch.object(source, "read") as mock_read:
            mock_read.return_value = [
                {"timestamp": "2024-01-01T00:00:00Z", "value": 1.0},
                {"timestamp": "2024-01-01T00:01:00Z", "value": 2.0},
                {"timestamp": "2024-01-01T00:02:00Z", "value": 3.0},
            ]
            rows, interval = source.read_with_sampling_detection(b"")
            assert len(rows) == 3
            assert abs(interval - 60.0) < 0.1

    def test_sampling_detection_with_jitter(self) -> None:
        config = RestSourceConfig(
            base_url="https://api.example.com",
            endpoint="/data",
            timestamp_column="timestamp",
        )
        source = SamplingAwareRestSource(config, expected_interval_seconds=60.0)

        with patch.object(source, "read") as mock_read:
            # 59s, 61s, 60s intervals
            mock_read.return_value = [
                {"timestamp": "2024-01-01T00:00:00Z", "value": 1.0},
                {"timestamp": "2024-01-01T00:00:59Z", "value": 2.0},
                {"timestamp": "2024-01-01T00:02:00Z", "value": 3.0},
                {"timestamp": "2024-01-01T00:03:00Z", "value": 4.0},
            ]
            rows, interval = source.read_with_sampling_detection(b"")
            assert len(rows) == 4
            assert abs(interval - 60.0) < 2.0  # Average should be ~60s


class TestSamplingAwareDatabaseSource:
    def test_sampling_analysis(self) -> None:
        config = DatabaseSourceConfig(
            connection_string="postgresql://user:pass@localhost/db",
            query="SELECT * FROM historical_data",
            timestamp_column="timestamp",
        )
        source = SamplingAwareDatabaseSource(config, expected_interval_seconds=60.0)

        with patch.object(source, "read") as mock_read:
            mock_read.return_value = [
                {"timestamp": "2024-01-01T00:00:00Z", "value": 1.0},
                {"timestamp": "2024-01-01T00:01:00Z", "value": 2.0},
                {"timestamp": "2024-01-01T00:05:00Z", "value": 3.0},  # 4 min gap (240s > 180s)
                {"timestamp": "2024-01-01T00:06:00Z", "value": 4.0},
            ]
            rows, interval, gaps = source.read_with_sampling_analysis(b"")
            assert len(rows) == 4
            assert abs(interval - 120.0) < 50.0  # Average of 60, 240, 60 = 120
            assert len(gaps) == 1
            assert gaps[0][2] > 180  # Gap > 3 minutes (240s gap)

    def test_no_gaps_when_regular(self) -> None:
        config = DatabaseSourceConfig(
            connection_string="postgresql://user:pass@localhost/db",
            query="SELECT * FROM historical_data",
            timestamp_column="timestamp",
        )
        source = SamplingAwareDatabaseSource(config, expected_interval_seconds=60.0)

        with patch.object(source, "read") as mock_read:
            mock_read.return_value = [
                {"timestamp": "2024-01-01T00:00:00Z", "value": 1.0},
                {"timestamp": "2024-01-01T00:01:00Z", "value": 2.0},
                {"timestamp": "2024-01-01T00:02:00Z", "value": 3.0},
            ]
            rows, interval, gaps = source.read_with_sampling_analysis(b"")
            assert len(gaps) == 0