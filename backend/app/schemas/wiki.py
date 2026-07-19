"""위키 API 요청/응답 스키마 (전사 단일 위키, wiki-v2 4.2, Phase 7-4).

위키 **페이지** 자체는 드라이브 파일이므로 조회/버전/이름 변경은 기존 파일 API(6.2)를 쓴다.
여기서는 개요/소스/잡/Lint/승격의 요청·응답만 정의한다(스페이스 스키마는 전면 제거).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # 런타임 임포트 순환 회피 — 팩토리 타입 힌트 전용.
    from app.models import WikiJob
    from app.services.wiki import LintReport, SourceInfo, WikiOverview


class WikiSourceRegisterRequest(BaseModel):
    """위키 공유 체크 (wiki-v2 4.2 POST /sources). 소유자만. 폴더는 항상 재귀(옵션 없음)."""

    file_id: int


class WikiSourceResponse(BaseModel):
    """공유 소스 한 건 (개요/등록 응답)."""

    file_id: int
    file_name: str
    status: str
    last_ingested_version: int | None
    added_by: int

    @classmethod
    def from_info(cls, info: SourceInfo) -> WikiSourceResponse:
        return cls(
            file_id=info.file_id,
            file_name=info.file_name,
            status=info.status,
            last_ingested_version=info.last_ingested_version,
            added_by=info.added_by,
        )


class WikiOverviewResponse(BaseModel):
    """위키 개요 (wiki-v2 4.2 GET /api/wiki) — 카탈로그·최근 로그·공유 소스 현황."""

    root_folder_id: int
    sources: list[WikiSourceResponse]
    recent_log: list[str]
    index_entries: list[str]

    @classmethod
    def from_overview(cls, ov: WikiOverview) -> WikiOverviewResponse:
        return cls(
            root_folder_id=ov.root_folder_id,
            sources=[WikiSourceResponse.from_info(i) for i in ov.sources],
            recent_log=ov.recent_log,
            index_entries=ov.index_entries,
        )


class WikiJobResponse(BaseModel):
    """위키 잡 이력 한 건 (wiki-v2 4.2 GET /jobs)."""

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
    """Lint 결과 (wiki-v2 4.2 POST /lint) — 결정적 자동 수정 + 휴리스틱 리포트."""

    auto_fixed: list[str]
    reports: list[str]

    @classmethod
    def from_report(cls, report: LintReport) -> WikiLintResponse:
        return cls(auto_fixed=report.auto_fixed, reports=report.reports)


class WikiPromoteRequest(BaseModel):
    """답변 승격 (wiki-v2 4.2 POST /promote). 원본 assistant 메시지 + 페이지 제목."""

    message_id: int
    title: str = Field(min_length=1, max_length=100)


class WikiPromoteResponse(BaseModel):
    """승격 결과 — 생성/갱신된 위키 페이지(드라이브 파일) id 와 이름."""

    file_id: int
    name: str
