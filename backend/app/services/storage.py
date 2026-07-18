"""StorageService — MinIO(S3 호환) 접근 추상화 계층 (PRD 2.2).

인터페이스(`put/get/stat/presign_get/delete`)로 오브젝트 스토리지를 감싸 추후 AWS S3
전환 시 코드 변경을 최소화한다. MinIO SDK 는 동기 API 이므로, 비동기 라우트에서 호출할 때는
`asyncio.to_thread` 로 감싼 async 래퍼(`*_async`)를 사용한다.

게이트웨이 다운로드 모델(PRD 2.2, 8절): presign_get 은 **내부 전용**(nginx→MinIO, 60초 TTL)
이며 브라우저에 노출하지 않는다. `to_internal_redirect` 로 presigned URL 을 nginx 의
`/_minio/` internal location 경로(X-Accel-Redirect 값)로 변환한다.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import BinaryIO
from urllib.parse import urlsplit

from minio import Minio
from minio.deleteobjects import DeleteObject

from app.core.config import settings

# nginx `location /_minio/` (internal) 접두어 — X-Accel-Redirect 대상 (PRD 8.1).
INTERNAL_REDIRECT_PREFIX = "/_minio"

# 내부 presign TTL — nginx↔MinIO 전용, 브라우저 비노출 (PRD 2.2, 10장).
PRESIGN_TTL_SECONDS = 60

# 스트리밍 업로드 파트 크기(10 MiB). length 가 이 값을 초과하면 SDK 가 멀티파트로 전송한다.
_UPLOAD_PART_SIZE = 10 * 1024 * 1024


class StorageService:
    """MinIO 버킷 하나를 대상으로 하는 S3 추상화. 동기 SDK 를 얇게 감싼다."""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        secure: bool | None = None,
    ) -> None:
        self.endpoint = endpoint or settings.minio_endpoint
        self.bucket = bucket or settings.minio_bucket
        self.secure = settings.minio_secure if secure is None else secure
        self._client = Minio(
            self.endpoint,
            access_key=access_key or settings.minio_access_key,
            secret_key=secret_key or settings.minio_secret_key,
            secure=self.secure,
        )

    # --- 동기 원시 연산 -----------------------------------------------------

    def put(
        self,
        key: str,
        data: BinaryIO,
        length: int,
        content_type: str | None = None,
    ) -> None:
        """스트림을 오브젝트로 저장한다. `data` 는 read() 가능한 파일 객체.

        length 를 알 때는 그대로 전달하고, part_size 를 함께 지정해 대용량은 멀티파트로
        전송한다(전체 메모리 적재 없이 청크 스트리밍). PRD 3.2 스트리밍 업로드.
        """
        self._client.put_object(
            self.bucket,
            key,
            data,
            length,
            content_type=content_type or "application/octet-stream",
            part_size=_UPLOAD_PART_SIZE,
        )

    def get(self, key: str):
        """오브젝트 스트림(urllib3 응답)을 반환한다. 호출자가 close/release 해야 한다."""
        return self._client.get_object(self.bucket, key)

    def stat(self, key: str):
        """오브젝트 메타데이터. 없으면 minio.error.S3Error(NoSuchKey)."""
        return self._client.stat_object(self.bucket, key)

    def presign_get(self, key: str, ttl: int = PRESIGN_TTL_SECONDS) -> str:
        """GET presigned URL(내부 전용, 기본 60초). 반환값은 절대 브라우저에 노출하지 않는다."""
        return self._client.presigned_get_object(
            self.bucket, key, expires=timedelta(seconds=ttl)
        )

    def delete(self, key: str) -> None:
        """단일 오브젝트 삭제. 존재하지 않아도 예외 없이 성공(S3 remove 시맨틱)."""
        self._client.remove_object(self.bucket, key)

    def delete_many(self, keys: list[str]) -> list[str]:
        """여러 오브젝트를 일괄 삭제하고, 삭제 실패한 키 목록을 반환한다."""
        if not keys:
            return []
        errors = self._client.remove_objects(
            self.bucket, (DeleteObject(k) for k in keys)
        )
        return [err.object_name for err in errors]

    # --- 비동기 래퍼 (blocking SDK 를 스레드로) -----------------------------

    async def put_async(
        self,
        key: str,
        data: BinaryIO,
        length: int,
        content_type: str | None = None,
    ) -> None:
        await asyncio.to_thread(self.put, key, data, length, content_type)

    async def stat_async(self, key: str):
        return await asyncio.to_thread(self.stat, key)

    async def presign_get_async(self, key: str, ttl: int = PRESIGN_TTL_SECONDS) -> str:
        return await asyncio.to_thread(self.presign_get, key, ttl)

    async def delete_async(self, key: str) -> None:
        await asyncio.to_thread(self.delete, key)

    async def delete_many_async(self, keys: list[str]) -> list[str]:
        return await asyncio.to_thread(self.delete_many, keys)

    # --- X-Accel-Redirect 변환 ---------------------------------------------

    def to_internal_redirect(self, presigned_url: str) -> str:
        """presigned URL 을 nginx `/_minio/` internal 경로로 변환한다 (PRD 2.2, 8.1).

        `http://minio:9000/minidrive/users/1/5?X-Amz-...`
          → `/_minio/minidrive/users/1/5?X-Amz-...`

        서명은 Host=minio:9000 기준으로 계산되고, nginx 가 `proxy_set_header Host minio:9000`
        으로 동일 Host 를 강제하므로 SigV4 서명이 일치한다 (PRD 12 리스크 대응).
        """
        parts = urlsplit(presigned_url)
        path = f"{INTERNAL_REDIRECT_PREFIX}{parts.path}"
        if parts.query:
            return f"{path}?{parts.query}"
        return path


# 모듈 전역 인스턴스 — 요청마다 클라이언트를 새로 만들지 않는다.
storage_service = StorageService()


def get_storage() -> StorageService:
    """FastAPI 의존성/서비스에서 공유 StorageService 를 얻는다."""
    return storage_service
