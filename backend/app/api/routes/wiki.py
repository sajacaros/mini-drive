"""위키 라우터 (전사 단일 위키, wiki-v2 4.2, Phase 7-4) — 개요/소스/잡/Lint/승격.

위키 v2 재설계로 스페이스 엔드포인트를 전면 제거했다. 위키 **페이지** 자체는 드라이브 파일이므로
조회/버전/이름 변경은 기존 파일 API(6.2)를 쓴다. 개요·잡·Lint·승격은 로그인 사용자 누구나
접근하고(전사 자산), 공유 체크/해제만 항목 소유자로 제한된다(wiki-v2 D1).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.wiki import (
    WikiJobResponse,
    WikiLintResponse,
    WikiOverviewResponse,
    WikiPromoteRequest,
    WikiPromoteResponse,
    WikiSourceRegisterRequest,
    WikiSourceResponse,
)
from app.services import chat as chat_service
from app.services import files as files_service
from app.services import wiki as wiki_service
from app.services.chat import ChatServiceError
from app.services.storage import get_storage
from app.services.wiki import WikiServiceError

router = APIRouter()


def _http_error(exc: WikiServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("", response_model=WikiOverviewResponse)
async def get_overview(
    user: CurrentUser, session: DbSession
) -> WikiOverviewResponse:
    """위키 개요 (wiki-v2 4.2) — 카탈로그·최근 로그·공유 소스 현황. 로그인 사용자 누구나."""
    try:
        overview = await wiki_service.get_overview(session, get_storage(), user)
    except WikiServiceError as exc:
        raise _http_error(exc) from exc
    return WikiOverviewResponse.from_overview(overview)


@router.post(
    "/sources",
    response_model=WikiSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_source(
    body: WikiSourceRegisterRequest, user: CurrentUser, session: DbSession
) -> WikiSourceResponse:
    """위키 공유 체크 (wiki-v2 4.2). 항목 소유자만(403). 부트스트랩 → 소스 등록 → Ingest 큐잉."""
    try:
        source = await wiki_service.register_source(
            session, get_storage(), user, file_id=body.file_id
        )
    except WikiServiceError as exc:
        raise _http_error(exc) from exc
    file = await files_service.get_file(session, source.file_id)
    return WikiSourceResponse(
        file_id=source.file_id,
        file_name=file.name if file else "",
        status=source.status,
        last_ingested_version=source.last_ingested_version,
        added_by=source.added_by,
    )


@router.delete("/sources/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_source(
    file_id: int, user: CurrentUser, session: DbSession
) -> None:
    """위키 공유 해제 (wiki-v2 4.2). 소유자만. 페이지는 잔존, log.md 에 기록(D5)."""
    try:
        await wiki_service.remove_source(session, get_storage(), user, file_id)
    except WikiServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/lint", response_model=WikiLintResponse)
async def run_lint(user: CurrentUser, session: DbSession) -> WikiLintResponse:
    """Lint 수동 실행 (wiki-v2 4.2). 로그인 사용자 누구나(결정적 자동 수정뿐, D6)."""
    try:
        report = await wiki_service.lint_wiki(session, get_storage(), user)
    except WikiServiceError as exc:
        raise _http_error(exc) from exc
    return WikiLintResponse.from_report(report)


@router.get("/jobs", response_model=list[WikiJobResponse])
async def list_jobs(user: CurrentUser, session: DbSession) -> list[WikiJobResponse]:
    """위키 잡 이력 조회 (wiki-v2 4.2). 로그인 사용자 누구나."""
    jobs = await wiki_service.list_jobs(session, user)
    return [WikiJobResponse.from_job(j) for j in jobs]


@router.post("/promote", response_model=WikiPromoteResponse)
async def promote_answer(
    body: WikiPromoteRequest, user: CurrentUser, session: DbSession
) -> WikiPromoteResponse:
    """챗 답변을 위키 페이지로 승격한다 (wiki-v2 4.2, D7). 로그인 사용자 누구나(전사 출판 동의).

    본인 assistant 메시지만 승격 가능(타인/부재 404). 인용 링크는 클릭 시점에 권한 재검증되므로
    승격이 원본 권한을 넓히지 않는다.
    """
    try:
        message = await chat_service.get_owned_assistant_message(
            session, user, body.message_id
        )
    except ChatServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    try:
        page = await wiki_service.promote_answer(
            session,
            get_storage(),
            user,
            message_id=message.id,
            title=body.title,
            content=message.content,
            citations=message.citations,
        )
    except WikiServiceError as exc:
        raise _http_error(exc) from exc
    return WikiPromoteResponse(file_id=page.id, name=page.name)
