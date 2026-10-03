from __future__ import annotations

import hashlib
import io
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, BinaryIO, cast
from uuid import UUID

from apps.api.processtwin_api.config import get_settings
from apps.api.processtwin_api.models import ObjectStorageArtifact
from minio import Minio
from minio.error import S3Error


@dataclass(frozen=True)
class UploadResult:
    bucket: str
    object_key: str
    size_bytes: int
    checksum_sha256: str
    etag: str
    version_id: str | None
    content_type: str


class ObjectStorageError(RuntimeError):
    pass


class ObjectStorageService:
    """MinIO/S3 object storage service for historical data artifacts."""

    def __init__(self) -> None:
        settings = get_settings()
        self._endpoint = settings.minio_endpoint
        self._access_key = settings.minio_access_key
        self._secret_key = settings.minio_secret_key
        self._bucket = settings.minio_bucket
        self._secure = settings.minio_secure
        self._client: Minio | None = None

    def _get_client(self) -> Minio:
        if self._client is None:
            if not all([self._endpoint, self._access_key, self._secret_key, self._bucket]):
                raise ObjectStorageError(
                    "Object storage not configured: MINIO_ENDPOINT, MINIO_ACCESS_KEY, "
                    "MINIO_SECRET_KEY, and MINIO_BUCKET must be set"
                )
            # mypy: these are checked to be non-None above
            assert self._endpoint is not None
            assert self._access_key is not None
            assert self._secret_key is not None
            assert self._bucket is not None
            self._client = Minio(
                self._endpoint,
                access_key=self._access_key,
                secret_key=self._secret_key,
                secure=self._secure,
            )
            # Ensure bucket exists
            assert self._bucket is not None
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
        return self._client

    def upload(
        self,
        data: bytes,
        object_key: str,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> UploadResult:
        """Upload bytes to object storage."""
        client = self._get_client()
        checksum = hashlib.sha256(data).hexdigest()
        stream = io.BytesIO(data)
        assert self._bucket is not None
        result = client.put_object(
            self._bucket,
            object_key,
            stream,
            length=len(data),
            content_type=content_type,
            metadata=metadata or {},  # type: ignore[arg-type]
        )
        return UploadResult(
            bucket=self._bucket,
            object_key=object_key,
            size_bytes=len(data),
            checksum_sha256=checksum,
            etag=result.etag or "",
            version_id=result.version_id,
            content_type=content_type,
        )

    def upload_streaming(
        self,
        stream: BinaryIO,
        object_key: str,
        content_type: str = "application/octet-stream",
        metadata: dict[str, str] | None = None,
    ) -> UploadResult:
        """Upload a streaming file to object storage, computing checksum on the fly."""
        client = self._get_client()
        hasher = hashlib.sha256()
        chunks: list[bytes] = []
        
        # Read in chunks to compute checksum and buffer
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            hasher.update(chunk)
            chunks.append(chunk)
        
        data = b"".join(chunks)
        checksum = hasher.hexdigest()
        
        data_stream = io.BytesIO(data)
        assert self._bucket is not None
        result = client.put_object(
            self._bucket,
            object_key,
            data_stream,
            length=len(data),
            content_type=content_type,
            metadata=metadata or {},  # type: ignore[arg-type]
        )
        return UploadResult(
            bucket=self._bucket,
            object_key=object_key,
            size_bytes=len(data),
            checksum_sha256=checksum,
            etag=result.etag or "",
            version_id=result.version_id,
            content_type=content_type,
        )

    def download(self, object_key: str) -> bytes:
        """Download object as bytes."""
        client = self._get_client()
        assert self._bucket is not None
        try:
            response = client.get_object(self._bucket, object_key)
            return response.read()
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                raise ObjectStorageError(f"Object not found: {object_key}") from exc
            raise ObjectStorageError(f"Download failed: {exc}") from exc

    def download_streaming(self, object_key: str) -> Generator[bytes, None, None]:
        """Download object as a stream of chunks."""
        client = self._get_client()
        assert self._bucket is not None
        try:
            response = client.get_object(self._bucket, object_key)
            while True:
                chunk = response.read(8192)
                if not chunk:
                    break
                yield chunk
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                raise ObjectStorageError(f"Object not found: {object_key}") from exc
            raise ObjectStorageError(f"Download failed: {exc}") from exc
        finally:
            response.close()
            response.release_conn()

    def delete(self, object_key: str) -> None:
        """Delete an object."""
        client = self._get_client()
        assert self._bucket is not None
        client.remove_object(self._bucket, object_key)

    def exists(self, object_key: str) -> bool:
        """Check if object exists."""
        client = self._get_client()
        assert self._bucket is not None
        try:
            client.stat_object(self._bucket, object_key)
            return True
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                return False
            raise

    def get_presigned_url(
        self, object_key: str, expires_seconds: int = 3600
    ) -> str:
        """Generate a presigned URL for downloading."""
        client = self._get_client()
        assert self._bucket is not None
        from datetime import timedelta
        return client.presigned_get_object(self._bucket, object_key, expires=timedelta(seconds=expires_seconds))

    def record_artifact(
        self,
        session: Any,
        organization_id: UUID,
        upload_result: UploadResult,
        uploaded_by: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ObjectStorageArtifact:
        """Record artifact metadata in database."""
        artifact = ObjectStorageArtifact(
            organization_id=organization_id,
            bucket=upload_result.bucket,
            object_key=upload_result.object_key,
            content_type=upload_result.content_type,
            size_bytes=upload_result.size_bytes,
            checksum_sha256=upload_result.checksum_sha256,
            etag=upload_result.etag,
            version_id=upload_result.version_id,
            artifact_metadata=metadata,
            uploaded_by=uploaded_by,
        )
        session.add(artifact)
        session.flush()
        return artifact

    def find_by_checksum(
        self, session: Any, organization_id: UUID, checksum_sha256: str
    ) -> ObjectStorageArtifact | None:
        """Find existing artifact by checksum for deduplication."""
        result = session.query(ObjectStorageArtifact).filter(
            ObjectStorageArtifact.organization_id == organization_id,
            ObjectStorageArtifact.checksum_sha256 == checksum_sha256,
        ).first()
        return cast(ObjectStorageArtifact | None, result)


# Convenience functions for generating object keys
def generate_import_job_key(organization_id: UUID, job_id: UUID, filename: str) -> str:
    """Generate object key for an import job's source file."""
    return f"imports/{organization_id}/{job_id}/{filename}"


def generate_chunk_key(organization_id: UUID, job_id: UUID, chunk_index: int) -> str:
    """Generate object key for a chunk file."""
    return f"imports/{organization_id}/{job_id}/chunks/chunk_{chunk_index:06d}.parquet"


def generate_dataset_artifact_key(
    organization_id: UUID, dataset_id: UUID, version: int, suffix: str
) -> str:
    """Generate object key for dataset-related artifacts."""
    return f"datasets/{organization_id}/{dataset_id}/v{version}/{suffix}"