"""인증 API 요청/응답 스키마 (PRD 6.1)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# 경량 이메일 형식 검사 (email-validator 의존성 회피). 사내 폐쇄망 전제라 형식만 확인한다.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_email(value: str) -> str:
    value = value.strip().lower()
    if not _EMAIL_RE.match(value):
        raise ValueError("올바른 이메일 형식이 아닙니다.")
    return value


Email = Annotated[str, Field(max_length=255), AfterValidator(_validate_email)]


class RegisterRequest(BaseModel):
    email: Email
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=100)
    # 가입 코드제 — 관리자 발급 코드 필수 (PRD 3.1, 5.11).
    signup_code: str = Field(min_length=1, max_length=64)


class RegisterResponse(BaseModel):
    id: int
    email: str
    status: str
    message: str = "회원가입이 완료되었습니다. 바로 로그인할 수 있습니다."


class LoginRequest(BaseModel):
    email: Email
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    """access + refresh JWT 쌍 (PRD 10장: access 15분 / refresh 7일)."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access 토큰 만료까지 남은 초


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    """현재 사용자 정보 (password_hash 제외) — GET /api/auth/me."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    avatar_url: str | None = None
    role: str
    status: str
    storage_used: int
    max_storage: int
    created_at: datetime
    updated_at: datetime
