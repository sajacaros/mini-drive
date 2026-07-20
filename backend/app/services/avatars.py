"""프로필 아바타 서비스 — 업로드/삭제/조회 (MinIO 저장).

아바타는 사용자당 하나이며 오브젝트 키 `avatars/{userId}` 에 저장한다(덮어쓰기). 프론트가
클라이언트에서 256x256 로 리사이즈해 올리므로 서버측 이미지 처리는 하지 않는다(Pillow 등
신규 의존성 없음) — content-type/크기만 검증한다.

조회는 파일 다운로드와 동일한 게이트웨이 모델(PRD 2.2)을 쓴다: presigned URL 을 브라우저에
노출하지 않고 X-Accel-Redirect 로 nginx→MinIO 스트리밍을 유도한다.
"""

from __future__ import annotations

import os
import time

from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.models import User
from app.services.storage import StorageService
from app.services.users import get_user_by_id

# 허용 이미지 타입 — 프론트 리사이즈 산출물(png/jpeg/webp)만 받는다.
ALLOWED_AVATAR_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})

# 아바타 크기 상한 2MB (256x256 리사이즈 후이므로 충분). nginx client_max_body_size 와 이중 방어.
AVATAR_MAX_SIZE = 2 * 1024 * 1024


class AvatarError(Exception):
    """아바타 조작 실패. HTTP 상태 코드를 함께 전달한다(FileServiceError 패턴)."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def build_avatar_key(user_id: int) -> str:
    """아바타 오브젝트 키: avatars/{userId} (사용자당 하나, 덮어쓰기)."""
    return f"avatars/{user_id}"


def validate_avatar(content_type: str | None, size: int) -> str:
    """아바타 업로드 검증 (DB/스토리지 무관, 단위 테스트 대상).

    content-type 이 허용 목록 밖이면 415, 크기가 상한 초과면 413, 빈 파일이면 422 로
    AvatarError 를 발생시킨다. 통과 시 정규화된 content-type 을 반환한다.
    """
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized not in ALLOWED_AVATAR_TYPES:
        raise AvatarError(415, "PNG, JPEG, WEBP 이미지만 업로드할 수 있습니다.")
    if size <= 0:
        raise AvatarError(422, "빈 파일은 업로드할 수 없습니다.")
    if size > AVATAR_MAX_SIZE:
        raise AvatarError(413, "아바타 이미지 크기가 2MB 를 초과했습니다.")
    return normalized


def _upload_size(upload: UploadFile) -> int:
    """UploadFile 의 바이트 크기(전체 메모리 적재 없이). multipart 파서의 .size 우선."""
    if upload.size is not None:
        return upload.size
    upload.file.seek(0, os.SEEK_END)
    size = upload.file.tell()
    upload.file.seek(0)
    return size


async def set_avatar(
    session: AsyncSession, storage: StorageService, user: User, upload: UploadFile
) -> str:
    """아바타를 MinIO 에 저장하고 users.avatar_url 을 API 경로로 갱신한다.

    흐름: 검증 → MinIO put(content-type 보존, 기존 것 덮어쓰기) → avatar_url 갱신 → commit.
    avatar_url 에는 조회 API 경로 `/api/users/{userId}/avatar?v={epoch}` 를 저장한다(캐시 무효화).
    반환: 저장된 avatar_url.
    """
    size = _upload_size(upload)
    content_type = validate_avatar(upload.content_type, size)

    key = build_avatar_key(user.id)
    await upload.seek(0)
    try:
        await storage.put_async(key, upload.file, size, content_type)
    except Exception as exc:  # noqa: BLE001 - 스토리지 저장 실패는 502 로 통일
        raise AvatarError(502, "아바타 저장에 실패했습니다.") from exc

    # 캐시 무효화를 위해 epoch 쿼리를 붙인다 — 같은 URL 이어도 갱신 즉시 새 이미지가 뜬다.
    avatar_url = f"/api/users/{user.id}/avatar?v={int(time.time())}"
    user.avatar_url = avatar_url
    await session.commit()
    await session.refresh(user)
    return avatar_url


async def clear_avatar(
    session: AsyncSession, storage: StorageService, user: User
) -> None:
    """아바타를 삭제한다 — MinIO 객체 제거(없어도 무시) + avatar_url=NULL. 멱등."""
    await _safe_delete_object(storage, build_avatar_key(user.id))
    user.avatar_url = None
    await session.commit()


async def prepare_avatar_download(
    session: AsyncSession, storage: StorageService, user_id: int
) -> tuple[str, str]:
    """아바타 게이트웨이 스트리밍 준비. 반환: (internal_redirect, mime).

    대상 사용자가 없거나 아바타가 없으면(avatar_url NULL / 오브젝트 부재) 404. content-type 은
    MinIO stat 으로 조회해 저장 시 타입을 보존한다.
    """
    target = await get_user_by_id(session, user_id)
    if target is None or target.avatar_url is None:
        raise AvatarError(404, "아바타를 찾을 수 없습니다.")

    key = build_avatar_key(user_id)
    try:
        stat = await storage.stat_async(key)
    except S3Error as exc:
        raise AvatarError(404, "아바타를 찾을 수 없습니다.") from exc

    mime = getattr(stat, "content_type", None) or "application/octet-stream"
    presigned = await storage.presign_get_async(key)
    return storage.to_internal_redirect(presigned), mime


async def _safe_delete_object(storage: StorageService, key: str) -> None:
    try:
        await storage.delete_async(key)
    except Exception:  # noqa: BLE001 - best-effort 정리, 없어도 무시
        pass
