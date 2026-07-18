"""셋업 위저드 API 스키마 (PRD 3.6.2, 6.1)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.user import DEFAULT_MAX_STORAGE
from app.schemas.auth import Email


class SetupStatusResponse(BaseModel):
    """셋업 필요 여부 (무인증). 프론트가 admin 0명일 때 /setup 으로 유도하는 데 쓴다."""

    setup_required: bool
    admin_exists: bool


class SetupRequest(BaseModel):
    """첫 부팅 셋업 입력 — 첫 admin + 초기 가입 코드 + 기본 할당량."""

    admin_email: Email
    admin_password: str = Field(min_length=8, max_length=128)
    # None → 서버가 추측 불가 코드를 자동 생성한다.
    signup_code: str | None = Field(default=None, min_length=1, max_length=64)
    default_max_storage: int = Field(default=DEFAULT_MAX_STORAGE, ge=0)


class SetupResponse(BaseModel):
    admin_email: str
    signup_code: str
    default_max_storage: int
    message: str = "셋업이 완료되었습니다. 관리자 계정으로 로그인하세요."
