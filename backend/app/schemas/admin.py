"""Admin 사용자 관리 API 스키마 (PRD 6.7)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import UserRole, UserStatus


class AdminUserResponse(BaseModel):
    """사용자 목록/상세 (password_hash 제외, 사용량 포함)."""

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


class AdminUserListResponse(BaseModel):
    items: list[AdminUserResponse]
    total: int
    page: int
    size: int


class UserUpdateRequest(BaseModel):
    """활성/비활성 전환, 할당량 조정, role 변경 (부분 갱신).

    status 는 active/inactive 만 허용한다 (pending→active 는 approve, →rejected 는 reject 전용).
    """

    status: UserStatus | None = None
    role: UserRole | None = None
    max_storage: int | None = Field(default=None, ge=0)
