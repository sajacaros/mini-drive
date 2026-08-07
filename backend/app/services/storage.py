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
from minio.commonconfig import CopySource
from minio.datatypes import Object, Part
from minio.deleteobjects import DeleteObject
from urllib3.response import BaseHTTPResponse

from app.core.config import settings
from app.core.metrics import observe_upload_bytes

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
        # 실제 저장된 업로드 바이트 계측 (PRD 11장). copy(스냅샷)는 put 을 거치지 않으므로 제외.
        observe_upload_bytes(length)

    def get(self, key: str) -> BaseHTTPResponse:
        """오브젝트 스트림(urllib3 응답)을 반환한다. 호출자가 close/release 해야 한다."""
        return self._client.get_object(self.bucket, key)

    def get_bytes(self, key: str) -> bytes:
        """오브젝트 전체를 바이트로 내려받는다. 썸네일 생성 등 backend 가 내용을 직접 다뤄야 할 때만
        사용한다(다운로드/미리보기 스트리밍은 게이트웨이 모델을 쓴다). 호출자가 크기를 제한한다.
        """
        resp = self._client.get_object(self.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def get_head_bytes(self, key: str, length: int) -> bytes:
        """오브젝트 앞부분 length 바이트만 범위 요청(GET Range)으로 내려받는다.

        큰 텍스트 미리보기에서 전체를 backend 로 적재하지 않도록 앞부분만 읽는다.
        length<=0 이면 빈 바이트를 반환한다.
        """
        if length <= 0:
            return b""
        resp = self._client.get_object(self.bucket, key, offset=0, length=length)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def stat(self, key: str) -> Object:
        """오브젝트 메타데이터. 없으면 minio.error.S3Error(NoSuchKey)."""
        return self._client.stat_object(self.bucket, key)

    def presign_get(self, key: str, ttl: int = PRESIGN_TTL_SECONDS) -> str:
        """GET presigned URL(내부 전용, 기본 60초). 반환값은 절대 브라우저에 노출하지 않는다."""
        return self._client.presigned_get_object(
            self.bucket, key, expires=timedelta(seconds=ttl)
        )

    def copy(self, src_key: str, dst_key: str) -> None:
        """버킷 내 서버측 복사(bytes 를 backend 로 내려받지 않음). 버전 스냅샷 생성용(PRD 2.1).

        같은 버킷 내 `src_key → dst_key`. 원본이 없으면 minio.error.S3Error(NoSuchKey).
        """
        self._client.copy_object(
            self.bucket, dst_key, CopySource(self.bucket, src_key)
        )

    def delete(self, key: str) -> None:
        """단일 오브젝트 삭제. 존재하지 않아도 예외 없이 성공(S3 remove 시맨틱)."""
        self._client.remove_object(self.bucket, key)

    # --- S3 Multipart Upload (재개 가능 업로드, PRD 3.2) --------------------
    #
    # MinIO SDK 는 멀티파트를 공개 API 로 노출하지 않으므로 내부 메서드를 얇게 감싼다.
    # 게이트웨이 모델 유지: presigned 파트 URL 을 클라이언트에 주지 않고, 파트 바이트도
    # backend 를 경유해 여기서 업로드한다.

    def create_multipart(self, key: str, content_type: str | None = None) -> str:
        """멀티파트 업로드를 개시하고 upload_id 를 반환한다."""
        return self._client._create_multipart_upload(
            self.bucket,
            key,
            {"Content-Type": content_type or "application/octet-stream"},
        )

    def upload_part(
        self, key: str, upload_id: str, part_number: int, data: bytes
    ) -> str:
        """단일 파트를 업로드하고 etag 를 반환한다. 실제 저장 바이트를 계측한다(PRD 11장)."""
        etag = self._client._upload_part(
            self.bucket, key, data, None, upload_id, part_number
        )
        observe_upload_bytes(len(data))
        return etag

    def list_parts(self, key: str, upload_id: str) -> list[tuple[int, int, str]]:
        """스테이징된 파트 목록을 (part_number, size, etag) 로 반환한다(part_number 오름차순).

        MinIO/S3 는 한 응답에 최대 1000개만 주므로 truncation 을 따라가며 전부 모은다.
        """
        collected: list[tuple[int, int, str]] = []
        marker: str | None = None
        while True:
            res = self._client._list_parts(
                self.bucket, key, upload_id, part_number_marker=marker
            )
            # p.size 는 Optional — 스테이징 파트에는 늘 값이 있지만 타입상 열려 0 으로 접는다.
            collected.extend((p.part_number, p.size or 0, p.etag) for p in res.parts)
            if not res.is_truncated:
                break
            marker = res.next_part_number_marker
        collected.sort(key=lambda t: t[0])
        return collected

    def complete_multipart(
        self, key: str, upload_id: str, parts: list[tuple[int, str]]
    ) -> None:
        """스테이징된 파트를 병합해 오브젝트를 확정한다. parts=[(part_number, etag), ...]."""
        self._client._complete_multipart_upload(
            self.bucket,
            key,
            upload_id,
            [Part(pn, etag) for pn, etag in parts],
        )

    def abort_multipart(self, key: str, upload_id: str) -> None:
        """멀티파트 업로드를 중단하고 스테이징된 파트를 폐기한다(확정 오브젝트는 건드리지 않음)."""
        self._client._abort_multipart_upload(self.bucket, key, upload_id)

    def delete_many(self, keys: list[str]) -> list[str]:
        """여러 오브젝트를 일괄 삭제하고, 삭제 실패한 키 목록을 반환한다."""
        if not keys:
            return []
        errors = self._client.remove_objects(
            self.bucket, (DeleteObject(k) for k in keys)
        )
        # DeleteError 의 키 필드는 `name` 이다(`object_name` 이 아니다 — minio.deleteobjects).
        # 삭제가 하나라도 실패하면 여기서 AttributeError 가 나던 자리였고, purge_tree 는 이
        # 호출을 감싸지 않아 DB 커밋이 끝난 뒤 500 으로 터졌다. name 은 Optional 이라 걸러 낸다.
        return [err.name for err in errors if err.name]

    # --- 비동기 래퍼 (blocking SDK 를 스레드로) -----------------------------

    async def put_async(
        self,
        key: str,
        data: BinaryIO,
        length: int,
        content_type: str | None = None,
    ) -> None:
        await asyncio.to_thread(self.put, key, data, length, content_type)

    async def get_bytes_async(self, key: str) -> bytes:
        return await asyncio.to_thread(self.get_bytes, key)

    async def get_head_bytes_async(self, key: str, length: int) -> bytes:
        return await asyncio.to_thread(self.get_head_bytes, key, length)

    async def stat_async(self, key: str) -> Object:
        return await asyncio.to_thread(self.stat, key)

    async def presign_get_async(self, key: str, ttl: int = PRESIGN_TTL_SECONDS) -> str:
        return await asyncio.to_thread(self.presign_get, key, ttl)

    async def copy_async(self, src_key: str, dst_key: str) -> None:
        await asyncio.to_thread(self.copy, src_key, dst_key)

    async def delete_async(self, key: str) -> None:
        await asyncio.to_thread(self.delete, key)

    async def delete_many_async(self, keys: list[str]) -> list[str]:
        return await asyncio.to_thread(self.delete_many, keys)

    async def create_multipart_async(
        self, key: str, content_type: str | None = None
    ) -> str:
        return await asyncio.to_thread(self.create_multipart, key, content_type)

    async def upload_part_async(
        self, key: str, upload_id: str, part_number: int, data: bytes
    ) -> str:
        return await asyncio.to_thread(
            self.upload_part, key, upload_id, part_number, data
        )

    async def list_parts_async(
        self, key: str, upload_id: str
    ) -> list[tuple[int, int, str]]:
        return await asyncio.to_thread(self.list_parts, key, upload_id)

    async def complete_multipart_async(
        self, key: str, upload_id: str, parts: list[tuple[int, str]]
    ) -> None:
        await asyncio.to_thread(self.complete_multipart, key, upload_id, parts)

    async def abort_multipart_async(self, key: str, upload_id: str) -> None:
        await asyncio.to_thread(self.abort_multipart, key, upload_id)

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
