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
    # 파생 필드 (Phase 8-2) — 서비스가 File 인스턴스에 부착하면 채워지고, 없으면 False.
    is_favorite: bool = False
    # 파생 필드 — 조상 폴더 경로("내 드라이브 / A / B"). 최근·즐겨찾기 목록에서 위치 표기용.
    # 서비스가 부착하지 않으면 빈 문자열(일반 목록 응답은 breadcrumb 로 위치를 알 수 있어 생략).
    location: str = ""
    # 파생 필드 (통합 드라이브 — Unix 유사 소유자/그룹/권한 컬럼). 서비스가 부착하지 않으면 기본값.
    #  - owner_name:  파일 소유자(files.user_id)의 표시명.
    #  - group_names: 소유 항목이면 내가 공유한 그룹명들, 공유받은 항목이면 접근을 부여한 그룹명들.
    #  - permission:  요청자의 유효 권한. 소유자는 "owner", 그 외에는 read/write/manage.
    owner_name: str = ""
    group_names: list[str] = Field(default_factory=list)
    permission: str = "owner"


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
