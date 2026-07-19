"""Admin 사용자 관리 API 스키마 (PRD 6.7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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


# --- 전체 그룹 조회 (PRD 6.7) ------------------------------------------------


class AdminGroupResponse(BaseModel):
    """그룹 요약 (owner 정보 + 멤버 수 + 소유 파일 수)."""

    id: int
    name: str
    description: str | None = None
    owner_id: int
    owner_email: str
    is_active: bool
    member_count: int
    file_count: int
    created_at: datetime


class AdminGroupListResponse(BaseModel):
    items: list[AdminGroupResponse]
    total: int
    page: int
    size: int


# --- 전체 공유 링크 조회 (PRD 6.7) -------------------------------------------


class AdminShareResponse(BaseModel):
    """공유 링크 요약 (파일명 + 생성자 이메일 포함, 파일 내용은 노출하지 않음 — 3.6.4)."""

    id: int
    file_id: int
    file_name: str
    created_by: int
    creator_email: str
    permission: str
    is_active: bool
    download_count: int
    max_downloads: int | None = None
    expires_at: datetime | None = None
    created_at: datetime


class AdminShareListResponse(BaseModel):
    items: list[AdminShareResponse]
    total: int
    page: int
    size: int


# --- 대시보드 통계 (PRD 6.7) -------------------------------------------------


class ShareCounts(BaseModel):
    active: int
    inactive: int
    total: int


class TopUser(BaseModel):
    email: str
    storage_used: int
    max_storage: int


class AdminStatsResponse(BaseModel):
    """대시보드용 단일 응답. 파일 메타데이터 집계만 담는다 (내용 접근 없음 — 3.6.4)."""

    total_users: int
    users_by_status: dict[str, int]
    total_files: int
    total_folders: int
    total_storage_used: int
    total_shares: ShareCounts
    total_groups: int
    top_users: list[TopUser]


# --- 감사 로그 조회 (PRD 5.9, 6.7) -------------------------------------------


class AdminAuditLogResponse(BaseModel):
    id: int
    actor_id: int
    actor_email: str
    action: str
    target_type: str
    target_id: int | None = None
    detail: dict[str, Any] | None = None
    created_at: datetime


class AdminAuditLogListResponse(BaseModel):
    items: list[AdminAuditLogResponse]
    total: int
    page: int
    size: int
