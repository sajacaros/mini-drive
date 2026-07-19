"""위키 API 요청/응답 스키마 (PRD 6.8, Phase 7-3).

위키 **페이지** 자체는 드라이브 파일이므로 조회/버전/이름 변경은 기존 파일 API(6.2)를 쓴다.
여기서는 스페이스/소스/잡/Lint 의 요청·응답만 정의한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # 런타임 임포트 순환 회피 — 팩토리 타입 힌트 전용.
    from app.models import WikiJob, WikiSpace
    from app.services.wiki import LintReport, SourceInfo, SpaceDetail


class WikiSpaceCreateRequest(BaseModel):
    """스페이스 생성 (PRD 6.8). group 스코프는 group_id 필수 + 그룹 admin 이상."""

    name: str = Field(min_length=1, max_length=100)
    scope: str = Field(pattern="^(personal|group)$")
    group_id: int | None = Field(default=None)


class WikiSourceRegisterRequest(BaseModel):
    """소스 등록 (PRD 6.8). recursive 는 폴더일 때 하위 재귀 포함 여부."""

    file_id: int
    recursive: bool = Field(default=True)


class WikiSpaceResponse(BaseModel):
    """스페이스 요약 (목록/생성 응답)."""

    id: int
    name: str
    scope: str
    user_id: int | None
    group_id: int | None
    root_folder_id: int
    created_at: datetime

    @classmethod
    def from_space(cls, s: WikiSpace) -> WikiSpaceResponse:
        return cls(
            id=s.id,
            name=s.name,
            scope=s.scope,
            user_id=s.user_id,
            group_id=s.group_id,
            root_folder_id=s.root_folder_id,
            created_at=s.created_at,
        )


class WikiSourceResponse(BaseModel):
    """등록 소스 한 건 (상세 응답)."""

    file_id: int
    file_name: str
    recursive: bool
    status: str
    last_ingested_version: int | None

    @classmethod
    def from_info(cls, info: SourceInfo) -> WikiSourceResponse:
        return cls(
            file_id=info.file_id,
            file_name=info.file_name,
            recursive=info.recursive,
            status=info.status,
            last_ingested_version=info.last_ingested_version,
        )


class WikiSpaceDetailResponse(BaseModel):
    """스페이스 상세 (PRD 6.8) — 소스 목록·상태, 최근 log.md 항목, index.md 카탈로그 요약."""

    id: int
    name: str
    scope: str
    user_id: int | None
    group_id: int | None
    root_folder_id: int
    created_at: datetime
    sources: list[WikiSourceResponse]
    recent_log: list[str]
    index_entries: list[str]

    @classmethod
    def from_detail(cls, detail: SpaceDetail) -> WikiSpaceDetailResponse:
        s = detail.space
        return cls(
            id=s.id,
            name=s.name,
            scope=s.scope,
            user_id=s.user_id,
            group_id=s.group_id,
            root_folder_id=s.root_folder_id,
            created_at=s.created_at,
            sources=[WikiSourceResponse.from_info(i) for i in detail.sources],
            recent_log=detail.recent_log,
            index_entries=detail.index_entries,
        )


class WikiJobResponse(BaseModel):
    """위키 잡 이력 한 건 (PRD 6.8 GET /jobs)."""

    id: int
    kind: str
    status: str
    file_id: int | None
    retries: int
    error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_job(cls, j: WikiJob) -> WikiJobResponse:
        return cls(
            id=j.id,
            kind=j.kind,
            status=j.status,
            file_id=j.file_id,
            retries=j.retries,
            error=j.error,
            created_at=j.created_at,
            updated_at=j.updated_at,
        )


class WikiLintResponse(BaseModel):
    """Lint 결과 (PRD 6.8 POST /lint) — 결정적 자동 수정 + 휴리스틱 리포트."""

    auto_fixed: list[str]
    reports: list[str]

    @classmethod
    def from_report(cls, report: LintReport) -> WikiLintResponse:
        return cls(auto_fixed=report.auto_fixed, reports=report.reports)
