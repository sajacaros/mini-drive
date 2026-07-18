"""공유 링크 API 요청/응답 스키마 (PRD 3.4, 6.3).

응답에는 password_hash 를 절대 노출하지 않고, 비밀번호 필요 여부만 boolean 으로 내린다.
공개(무인증) 메타 응답은 오브젝트 키/생성자 등 내부 정보를 담지 않는다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # 런타임 임포트 순환 회피 — from_share 팩토리 타입 힌트 전용.
    from app.models import Share


class ShareCreateRequest(BaseModel):
    """공유 링크 생성 (PRD 6.3 POST /api/shares).

    permission 은 Phase 1 에서 read/download 만 허용한다(편집 권한은 Phase 5). file_id 가
    폴더면 서비스가 400 을 낸다. password 는 옵션이며 argon2 로 해싱해 저장한다.
    """

    file_id: int
    permission: Literal["read", "download"] = "read"
    expires_at: datetime | None = None
    password: str | None = Field(default=None, min_length=1, max_length=128)
    max_downloads: int | None = Field(default=None, ge=1)


class ShareResponse(BaseModel):
    """소유자용 공유 링크 응답 (파일명·다운로드 수·활성 상태 포함) — PRD 3.4, 6.3."""

    id: int
    file_id: int
    file_name: str
    share_url: str
    permission: str
    is_active: bool
    password_required: bool
    expires_at: datetime | None
    max_downloads: int | None
    download_count: int
    created_at: datetime

    @classmethod
    def from_share(cls, share: Share, file_name: str) -> ShareResponse:
        """Share ORM + 파일명으로 응답을 구성한다(password_required 는 해시 존재로 파생)."""
        return cls(
            id=share.id,
            file_id=share.file_id,
            file_name=file_name,
            share_url=share.share_url,
            permission=share.permission,
            is_active=share.is_active,
            password_required=share.password_hash is not None,
            expires_at=share.expires_at,
            max_downloads=share.max_downloads,
            download_count=share.download_count,
            created_at=share.created_at,
        )


class SharePublicMeta(BaseModel):
    """공개(무인증) 공유 메타 (PRD 6.3 GET /api/public/shares/{shareUrl}).

    이 응답에 도달했다는 것은 활성·미만료·파일 존재를 이미 통과했다는 뜻이다(그 외에는
    404/410 으로 응답). password_required 가 True 면 다운로드 시 비밀번호가 필요하다.
    """

    file_name: str
    size: int
    mime_type: str | None = None
    permission: str
    password_required: bool
    expires_at: datetime | None = None


class SharePasswordRequest(BaseModel):
    """공개 다운로드 시 전달하는 비밀번호(필요 시). 본문 전체가 옵션이다."""

    password: str | None = None
