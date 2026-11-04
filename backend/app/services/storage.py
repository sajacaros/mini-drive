"""StorageService — S3 호환 오브젝트 스토리지 접근 추상화 계층 (PRD 2.2).

서버는 SeaweedFS 의 S3 게이트웨이이고, 클라이언트는 AWS 공식 SDK(boto3)다(2026-11 MinIO 서버와
minio SDK 에서 전환). 표준 S3 API 만 쓰므로 AWS S3 등 다른 S3 호환 서버로 바꿀 때는 엔드포인트만
바꾸면 된다. 호출부는 boto3 를 직접 만지지 않는다 — 이 모듈의 인터페이스와 `ObjectNotFoundError`
만 안다.

boto3 는 동기 API 이므로 비동기 라우트에서는 `asyncio.to_thread` 로 감싼 `*_async` 래퍼를 쓴다.
boto3 클라이언트는 스레드 안전하다(세션은 아니다) — 모듈 전역 인스턴스 하나를 공유한다.

게이트웨이 다운로드 모델(PRD 2.2, 8절): presign_get 은 **내부 전용**(nginx→스토리지, 60초 TTL)
이며 브라우저에 노출하지 않는다. `to_internal_redirect` 로 presigned URL 을 nginx 의
`/_minio/` internal location 경로(X-Accel-Redirect 값)로 변환한다(경로 이름은 이력상 이름).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO
from urllib.parse import urlsplit

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.metrics import observe_upload_bytes

# nginx `location /_minio/` (internal) 접두어 — X-Accel-Redirect 대상 (PRD 8.1).
INTERNAL_REDIRECT_PREFIX = "/_minio"

# 내부 presign TTL — nginx↔스토리지 전용, 브라우저 비노출 (PRD 2.2, 10장).
PRESIGN_TTL_SECONDS = 60

# 스트리밍 업로드 파트 크기(10 MiB). length 가 이 값을 넘으면 SDK 가 멀티파트로 나눠 보낸다 —
# 파일 전체를 메모리에 올리지 않는다.
_UPLOAD_PART_SIZE = 10 * 1024 * 1024
_UPLOAD_CONFIG = TransferConfig(
    multipart_threshold=_UPLOAD_PART_SIZE, multipart_chunksize=_UPLOAD_PART_SIZE
)

# 서버측 복사. 단일 CopyObject 는 S3 규격상 5 GiB 가 상한이라, 그 이상은 SDK 의 관리형 복사가
# UploadPartCopy 로 쪼갠다(바이트는 여전히 backend 를 거치지 않는다). 그 아래는 요청 한 번.
_COPY_CONFIG = TransferConfig(
    multipart_threshold=5 * 1024**3, multipart_chunksize=512 * 1024**2
)

# DeleteObjects 한 요청의 키 상한(S3 규격).
_DELETE_BATCH = 1000

# 객체가 없을 때 S3 가 돌려주는 오류 코드. HEAD 는 본문이 없어 코드 대신 상태값("404")만 온다.
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchObject", "NotFound", "404"})


class ObjectNotFoundError(LookupError):
    """요청한 키의 오브젝트가 없다. 호출부가 SDK 예외 타입을 몰라도 되게 하는 경계 예외."""

    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


@dataclass(frozen=True, slots=True)
class ObjectStat:
    """HEAD 결과 중 앱이 쓰는 것만. etag 는 따옴표를 벗긴 값."""

    size: int
    content_type: str | None
    etag: str


class ObjectStream:
    """GET 응답 본문. `stream(n)` 으로 청크를 읽고 반드시 `close()` 한다(커넥션 반납)."""

    def __init__(self, body: Any) -> None:
        self._body = body

    def stream(self, chunk_size: int) -> Iterator[bytes]:
        chunks: Iterator[bytes] = self._body.iter_chunks(chunk_size)
        return chunks

    def read(self) -> bytes:
        data: bytes = self._body.read()
        return data

    def close(self) -> None:
        self._body.close()


class _LimitedReader:
    """원본 스트림에서 최대 length 바이트만 읽게 한다.

    minio SDK 의 put_object(length) 는 정확히 length 만 읽었지만 boto3 upload_fileobj 는 EOF 까지
    읽는다. 할당량은 length 로 선점하므로, 저장 바이트가 그보다 커지는 일이 없게 같은 계약을 지킨다.
    """

    def __init__(self, raw: BinaryIO, length: int) -> None:
        self._raw = raw
        self._left = length

    def read(self, size: int = -1) -> bytes:
        if self._left <= 0:
            return b""
        n = self._left if size is None or size < 0 else min(size, self._left)
        chunk = self._raw.read(n)
        self._left -= len(chunk)
        return chunk


def _strip_etag(etag: str) -> str:
    # S3 는 ETag 를 따옴표째 준다. DB(업로드 세션)·비교에는 맨 값을 쓴다.
    return etag.strip('"')


def _is_not_found(exc: ClientError) -> bool:
    return str(exc.response.get("Error", {}).get("Code", "")) in _NOT_FOUND_CODES


def make_s3_client(
    *,
    endpoint: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    secure: bool | None = None,
) -> Any:
    """설정값으로 boto3 S3 클라이언트를 만든다. 생성은 네트워크에 접속하지 않는다.

    - addressing_style=path: 가상 호스트(`bucket.host`) 방식은 도커 내부 이름에서 풀리지 않는다.
      경로 방식이어야 presigned URL 의 Host 가 엔드포인트 그대로(`seaweedfs:9000`)가 되고, nginx 가
      같은 Host 를 실어 SigV4 서명이 맞는다(PRD 12).
    - checksum=when_required: botocore 1.36+ 는 기본으로 업로드마다 CRC 체크섬 헤더를 붙인다.
      S3 호환 서버마다 지원이 들쭉날쭉해, 규격상 필수인 경우에만 보내게 해 호환 폭을 넓힌다.
    - region 은 SigV4 서명에만 쓰인다. SeaweedFS 는 값을 가리지 않는다.
    """
    use_tls = settings.minio_secure if secure is None else secure
    host = endpoint or settings.minio_endpoint
    return boto3.client(
        "s3",
        endpoint_url=f"{'https' if use_tls else 'http'}://{host}",
        aws_access_key_id=access_key or settings.minio_access_key,
        aws_secret_access_key=secret_key or settings.minio_secret_key,
        region_name="us-east-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


class StorageService:
    """버킷 하나를 대상으로 하는 S3 추상화. 동기 boto3 클라이언트를 얇게 감싼다."""

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
        self._client = make_s3_client(
            endpoint=self.endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=self.secure,
        )

    # --- 버킷 ------------------------------------------------------------

    def bucket_exists(self) -> bool:
        """버킷이 있는지. 연결·자격 증명 점검(/health)에도 쓴다 — 그 오류는 그대로 올린다."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            if _is_not_found(exc):
                return False
            raise
        return True

    def ensure_bucket(self) -> None:
        """버킷이 없으면 만든다. 운영은 compose 의 seaweedfs-init 이 맡고, 이건 테스트 편의용."""
        if not self.bucket_exists():
            self._client.create_bucket(Bucket=self.bucket)

    def list_keys(self, prefix: str = "") -> list[str]:
        """prefix 아래 모든 키(페이지를 끝까지 따라간다).

        정리·테스트 용도 — 요청 경로에서 쓰지 않는다.
        """
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return keys

    # --- 동기 원시 연산 -----------------------------------------------------

    def put(
        self,
        key: str,
        data: BinaryIO,
        length: int,
        content_type: str | None = None,
    ) -> None:
        """스트림의 앞 length 바이트를 오브젝트로 저장한다. `data` 는 read() 가능한 파일 객체.

        10 MiB 를 넘으면 SDK 가 멀티파트로 청크 스트리밍한다 — 전체를 메모리에 올리지 않는다
        (PRD 3.2).
        """
        self._client.upload_fileobj(
            _LimitedReader(data, length),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type or "application/octet-stream"},
            Config=_UPLOAD_CONFIG,
        )
        # 실제 저장된 업로드 바이트 계측 (PRD 11장). copy(스냅샷)는 put 을 거치지 않으므로 제외.
        observe_upload_bytes(length)

    def _get_object(self, key: str, **kwargs: Any) -> Any:
        try:
            return self._client.get_object(Bucket=self.bucket, Key=key, **kwargs)
        except ClientError as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise

    def get(self, key: str) -> ObjectStream:
        """오브젝트 스트림을 반환한다. 호출자가 close 해야 한다. 없으면 ObjectNotFoundError."""
        return ObjectStream(self._get_object(key)["Body"])

    def get_bytes(self, key: str) -> bytes:
        """오브젝트 전체를 바이트로 내려받는다. 썸네일 생성 등 backend 가 내용을 직접 다뤄야 할 때만
        사용한다(다운로드/미리보기 스트리밍은 게이트웨이 모델을 쓴다). 호출자가 크기를 제한한다.
        """
        stream = self.get(key)
        try:
            return stream.read()
        finally:
            stream.close()

    def get_head_bytes(self, key: str, length: int) -> bytes:
        """오브젝트 앞부분 length 바이트만 범위 요청(GET Range)으로 내려받는다.

        큰 텍스트 미리보기에서 전체를 backend 로 적재하지 않도록 앞부분만 읽는다.
        length<=0 이면 빈 바이트를 반환한다.
        """
        if length <= 0:
            return b""
        stream = ObjectStream(self._get_object(key, Range=f"bytes=0-{length - 1}")["Body"])
        try:
            return stream.read()
        finally:
            stream.close()

    def stat(self, key: str) -> ObjectStat:
        """오브젝트 메타데이터. 없으면 ObjectNotFoundError."""
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise
        return ObjectStat(
            size=int(head["ContentLength"]),
            content_type=head.get("ContentType"),
            etag=_strip_etag(head.get("ETag", "")),
        )

    def exists(self, key: str) -> bool:
        try:
            self.stat(key)
        except ObjectNotFoundError:
            return False
        return True

    def presign_get(self, key: str, ttl: int = PRESIGN_TTL_SECONDS) -> str:
        """GET presigned URL(내부 전용, 기본 60초). 반환값은 절대 브라우저에 노출하지 않는다."""
        url: str = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=ttl,
        )
        return url

    def copy(self, src_key: str, dst_key: str) -> None:
        """버킷 내 서버측 복사(bytes 를 backend 로 내려받지 않음). 버전 스냅샷 생성용(PRD 2.1).

        Content-Type 등 메타데이터는 원본을 따른다. 원본이 없으면 ObjectNotFoundError.
        """
        try:
            self._client.copy(
                {"Bucket": self.bucket, "Key": src_key},
                self.bucket,
                dst_key,
                Config=_COPY_CONFIG,
            )
        except ClientError as exc:
            if _is_not_found(exc):
                raise ObjectNotFoundError(src_key) from exc
            raise

    def delete(self, key: str) -> None:
        """단일 오브젝트 삭제. 존재하지 않아도 예외 없이 성공(S3 remove 시맨틱)."""
        self._client.delete_object(Bucket=self.bucket, Key=key)

    # --- S3 Multipart Upload (재개 가능 업로드, PRD 3.2) --------------------
    #
    # 게이트웨이 모델 유지: presigned 파트 URL 을 클라이언트에 주지 않고, 파트 바이트도
    # backend 를 경유해 여기서 업로드한다. etag 는 전부 따옴표를 벗긴 값으로 주고받는다.

    def create_multipart(self, key: str, content_type: str | None = None) -> str:
        """멀티파트 업로드를 개시하고 upload_id 를 반환한다."""
        res = self._client.create_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            ContentType=content_type or "application/octet-stream",
        )
        upload_id: str = res["UploadId"]
        return upload_id

    def upload_part(
        self, key: str, upload_id: str, part_number: int, data: bytes
    ) -> str:
        """단일 파트를 업로드하고 etag 를 반환한다. 실제 저장 바이트를 계측한다(PRD 11장)."""
        res = self._client.upload_part(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            PartNumber=part_number,
            Body=data,
        )
        observe_upload_bytes(len(data))
        return _strip_etag(res["ETag"])

    def list_parts(self, key: str, upload_id: str) -> list[tuple[int, int, str]]:
        """스테이징된 파트 목록을 (part_number, size, etag) 로 반환한다(part_number 오름차순).

        한 응답은 최대 1000개라 truncation 을 따라간다. SeaweedFS 4.47 은 PartNumberMarker 를
        무시하고 처음부터 다시 주는 것을 실측했다 — 그래서 파트 번호로 중복을 접고, marker 가
        앞으로 가지 않거나 새 파트가 없으면 멈춘다(무한 루프 방지).
        """
        by_number: dict[int, tuple[int, int, str]] = {}
        marker = 0
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key, "UploadId": upload_id}
            if marker:
                kwargs["PartNumberMarker"] = marker
            res = self._client.list_parts(**kwargs)
            before = len(by_number)
            for p in res.get("Parts", []):
                n = int(p["PartNumber"])
                by_number[n] = (n, int(p.get("Size") or 0), _strip_etag(p["ETag"]))
            next_marker = int(res.get("NextPartNumberMarker") or 0)
            if not res.get("IsTruncated") or next_marker <= marker or len(by_number) == before:
                break
            marker = next_marker
        return [by_number[n] for n in sorted(by_number)]

    def complete_multipart(
        self, key: str, upload_id: str, parts: list[tuple[int, str]]
    ) -> None:
        """스테이징된 파트를 병합해 오브젝트를 확정한다. parts=[(part_number, etag), ...]."""
        self._client.complete_multipart_upload(
            Bucket=self.bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [{"PartNumber": pn, "ETag": etag} for pn, etag in parts]
            },
        )

    def abort_multipart(self, key: str, upload_id: str) -> None:
        """멀티파트 업로드를 중단하고 스테이징된 파트를 폐기한다(확정 오브젝트는 건드리지 않음)."""
        self._client.abort_multipart_upload(
            Bucket=self.bucket, Key=key, UploadId=upload_id
        )

    def delete_many(self, keys: list[str]) -> list[str]:
        """여러 오브젝트를 일괄 삭제하고, 삭제 실패한 키 목록을 반환한다.

        DeleteObjects 는 요청당 1000개가 상한이라 나눠 보낸다. Quiet 모드라 응답에는 실패만 온다.
        없는 키는 S3 시맨틱상 성공이다.
        """
        failed: list[str] = []
        for i in range(0, len(keys), _DELETE_BATCH):
            batch = keys[i : i + _DELETE_BATCH]
            res = self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            failed.extend(err["Key"] for err in res.get("Errors", []) if err.get("Key"))
        return failed

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

    async def stat_async(self, key: str) -> ObjectStat:
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

        `http://seaweedfs:9000/minidrive/users/1/5?X-Amz-...`
          → `/_minio/minidrive/users/1/5?X-Amz-...`

        서명은 Host=seaweedfs:9000 기준으로 계산되고, nginx 가
        `proxy_set_header Host seaweedfs:9000` 으로 동일 Host 를 강제하므로 SigV4 서명이
        일치한다 (PRD 12 리스크 대응).
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
