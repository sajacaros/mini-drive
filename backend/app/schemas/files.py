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
    indexing_excluded: bool = False
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


class FileUpdateRequest(BaseModel):
    """파일 속성 부분 갱신 (PATCH /api/files/{id}). 현재는 인덱싱 제외 플래그만 — PRD 3.7.2."""

    indexing_excluded: bool | None = None


class FileVersionResponse(BaseModel):
    """버전 히스토리 항목 (PRD 3.3, 6.2). 오브젝트 키는 노출하지 않는다."""

    version: int
    size: int
    mime_type: str | None = None
    uploaded_by: int
    uploaded_by_name: str
    uploaded_at: datetime
    is_current: bool


class FileVersionListResponse(BaseModel):
    """파일 버전 목록 (PRD 6.2 GET /api/files/{id}/versions)."""

    file_id: int
    current_version: int
    items: list[FileVersionResponse]


class DownloadTicketResponse(BaseModel):
    """단기 일회성 다운로드 티켓 (브라우저 대용량 다운로드용).

    `url` 로 무헤더 GET 하면 스트리밍 다운로드가 시작된다. 티켓은 60초 후 만료되며 1회만 유효.
    """

    ticket: str
    url: str
    expires_in: int
