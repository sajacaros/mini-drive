"""그룹/멤버 API 요청·응답 스키마 (PRD 6.4)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import GroupRole


class GroupCreateRequest(BaseModel):
    """그룹 생성 (PRD 6.4 POST /api/groups)."""

    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class GroupUpdateRequest(BaseModel):
    """그룹 정보 수정 (PRD 6.4 PUT /api/groups/{id}). 부분 갱신."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None


class GroupResponse(BaseModel):
    """그룹 기본 정보."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None = None
    owner_user_id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class GroupSummaryResponse(GroupResponse):
    """그룹 목록 항목 — 멤버 수와 요청자의 소속 역할 포함 (PRD 6.4 GET /api/groups)."""

    member_count: int
    my_role: str | None = None


class GroupListResponse(BaseModel):
    """페이지네이션된 그룹 목록."""

    items: list[GroupSummaryResponse]
    total: int
    page: int
    size: int


class GroupMemberResponse(BaseModel):
    """활성 그룹원 (이메일/표시명/역할/가입일)."""

    user_id: int
    email: str
    display_name: str
    role: str
    joined_at: datetime


class GroupMemberListResponse(BaseModel):
    """그룹원 목록 (PRD 6.4 GET /api/groups/{id}/members)."""

    group_id: int
    items: list[GroupMemberResponse]


class GroupDetailResponse(GroupSummaryResponse):
    """그룹 상세 — 기본 정보 + 활성 멤버 목록 (PRD 6.4 GET /api/groups/{id})."""

    members: list[GroupMemberResponse]


class MemberInviteRequest(BaseModel):
    """그룹원 초대 (PRD 6.4 POST /api/groups/{id}/members). role 생략 시 member."""

    user_id: int
    role: GroupRole = GroupRole.MEMBER


class MemberRoleUpdateRequest(BaseModel):
    """그룹원 역할 변경 (PRD 6.4 PUT .../members/{userId}/role). admin|member 만."""

    role: GroupRole


class TransferOwnershipRequest(BaseModel):
    """소유권 이전 (PRD 6.4 POST /api/groups/{id}/transfer-ownership)."""

    user_id: int
