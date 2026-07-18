"""가입 코드 관리 API 스키마 (PRD 6.7)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SignupCodeCreateRequest(BaseModel):
    """가입 코드 발급. code 미지정 시 서버가 추측 불가 코드를 자동 생성한다."""

    memo: str = Field(default="", max_length=200)
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)
    code: str | None = Field(default=None, min_length=1, max_length=64)


class SignupCodeUpdateRequest(BaseModel):
    """가입 코드 수정 (부분 갱신). 지정한 필드만 변경된다.

    expires_at/max_uses 는 명시적으로 null 을 보내면 "무기한/무제한"으로 초기화된다
    (미지정과 구분하기 위해 라우터에서 exclude_unset 을 사용한다).
    """

    is_active: bool | None = None
    memo: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)


class SignupCodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    memo: str
    expires_at: datetime | None = None
    max_uses: int | None = None
    use_count: int
    is_active: bool
    created_by: int
    created_at: datetime


class SignupCodeListResponse(BaseModel):
    items: list[SignupCodeResponse]
    total: int
    page: int
    size: int
