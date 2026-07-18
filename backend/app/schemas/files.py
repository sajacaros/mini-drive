"""파일/폴더 API 요청·응답 스키마 (PRD 6.2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FileResponse(BaseModel):
    """파일 또는 폴더 메타데이터 (오브젝트 스토리지 키는 노출하지 않는다)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    parent_folder_id: int | None
    name: str
    mime_type: str | None = None
    size: int
    is_folder: bool
    is_deleted: bool
    current_version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class FileListResponse(BaseModel):
    """페이지네이션된 파일 목록 (PRD 6.2 GET /api/files)."""

    items: list[FileResponse]
    total: int
    page: int
    size: int


class FolderCreateRequest(BaseModel):
    """폴더 생성 (PRD 6.2 POST /api/files). parent_id 생략 시 루트 폴더 하위."""

    name: str = Field(min_length=1, max_length=255)
    parent_id: int | None = None


class FileRenameRequest(BaseModel):
    """이름 변경 (PRD 6.2 PUT /api/files/{id})."""

    name: str = Field(min_length=1, max_length=255)
