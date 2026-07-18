"""파일/폴더 그룹 권한 API 요청·응답 스키마 (PRD 6.5/6.6)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import GroupPermission
from app.schemas.files import FileResponse


class PermissionGrantRequest(BaseModel):
    """그룹 권한 부여 (PRD 6.5 POST /api/files/{id}/permissions).

    (file, group) 중복은 409 가 아니라 upsert(수정)로 처리한다.
    """

    group_id: int
    permission: GroupPermission = GroupPermission.READ
    inherit_to_children: bool = True
    expires_at: datetime | None = None


class PermissionUpdateRequest(BaseModel):
    """그룹 권한 수정 (PRD 6.5 PUT .../permissions/{groupId}). 부분 갱신.

    expires_at 은 명시적으로 null 을 보내면 만료 해제(영구)로 갱신된다.
    """

    permission: GroupPermission | None = None
    inherit_to_children: bool | None = None
    expires_at: datetime | None = None


class DirectPermissionResponse(BaseModel):
    """파일에 직접 부여된 그룹 권한."""

    group_id: int
    group_name: str
    permission: str
    inherit_to_children: bool
    granted_at: datetime
    expires_at: datetime | None = None
    granted_by: int


class InheritedPermissionResponse(BaseModel):
    """조상 폴더에서 상속되어 유효한 그룹 권한 (어디서/어느 그룹으로 오는지)."""

    group_id: int
    group_name: str
    permission: str
    source_file_id: int
    source_file_name: str
    depth: int
    expires_at: datetime | None = None


class FilePermissionsResponse(BaseModel):
    """파일 권한 목록 — 직접 부여 + 유효 상속 (PRD 6.5/6.6 통합)."""

    file_id: int
    direct: list[DirectPermissionResponse]
    inherited: list[InheritedPermissionResponse]


class InheritedPermissionsResponse(BaseModel):
    """상속된 유효 권한만 (PRD 6.6 GET /api/permissions/inherited/{fileId})."""

    file_id: int
    inherited: list[InheritedPermissionResponse]


class PermissionCheckResponse(BaseModel):
    """내 유효 권한 (PRD 6.6 GET /api/permissions/check/{fileId})."""

    file_id: int
    permission: str = Field(description="read | write | manage | none")
    via: str = Field(description="owner | group | admin | none")
    source_file_id: int | None = None


class SharedItemResponse(BaseModel):
    """공유된 항목(부여 지점) 하나 — 대상 파일/폴더 + 그룹명 + 권한."""

    model_config = ConfigDict(from_attributes=True)

    file: FileResponse
    group_id: int
    group_name: str
    permission: str


class SharedWithMeResponse(BaseModel):
    """내 소속 그룹에 공유된 항목 목록 (PRD 3.1.3 진입점)."""

    items: list[SharedItemResponse]
