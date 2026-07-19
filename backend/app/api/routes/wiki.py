"""위키 라우터 (PRD 6.8, Phase 7-3) — 스페이스/소스/잡/Lint.

위키 **페이지** 자체는 드라이브 파일이므로 조회/버전/이름 변경은 기존 파일 API(6.2)를 쓴다.
모든 엔드포인트는 get_current_user 인가를 통과하며 스페이스 접근 자격을 재검증한다 — admin 도
예외 없이 접근 자격이 필요하다(운영 지표 조회 엔드포인트는 두지 않는다, PRD 3.7.3/6.8).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser, DbSession
from app.schemas.wiki import (
    WikiJobResponse,
    WikiLintResponse,
    WikiSourceRegisterRequest,
    WikiSourceResponse,
    WikiSpaceCreateRequest,
    WikiSpaceDetailResponse,
    WikiSpaceResponse,
)
from app.services import wiki as wiki_service
from app.services.storage import get_storage
from app.services.wiki import WikiServiceError

router = APIRouter()


def _http_error(exc: WikiServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.post(
    "/spaces", response_model=WikiSpaceResponse, status_code=status.HTTP_201_CREATED
)
async def create_space(
    body: WikiSpaceCreateRequest, user: CurrentUser, session: DbSession
) -> WikiSpaceResponse:
    """스페이스 생성 (PRD 6.8). group 스코프는 그룹 admin 이상만."""
    try:
        space = await wiki_service.create_space(
            session,
            get_storage(),
            user,
            name=body.name,
            scope=body.scope,
            group_id=body.group_id,
        )
    except WikiServiceError as exc:
        raise _http_error(exc) from exc
    return WikiSpaceResponse.from_space(space)


@router.get("/spaces", response_model=list[WikiSpaceResponse])
async def list_spaces(
    user: CurrentUser, session: DbSession
) -> list[WikiSpaceResponse]:
    """내가 접근 가능한 스페이스 목록 (personal 소유 + 소속 그룹)."""
    spaces = await wiki_service.list_spaces(session, user)
    return [WikiSpaceResponse.from_space(s) for s in spaces]


@router.get("/spaces/{space_id}", response_model=WikiSpaceDetailResponse)
async def get_space(
    space_id: int, user: CurrentUser, session: DbSession
) -> WikiSpaceDetailResponse:
    """스페이스 상세 — index.md 요약, 소스 목록·상태, 최근 log.md 항목 (PRD 6.8)."""
    try:
        detail = await wiki_service.get_detail(
            session, get_storage(), user, space_id
        )
    except WikiServiceError as exc:
        raise _http_error(exc) from exc
    return WikiSpaceDetailResponse.from_detail(detail)


@router.post(
    "/spaces/{space_id}/sources",
    response_model=WikiSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_source(
    space_id: int,
    body: WikiSourceRegisterRequest,
    user: CurrentUser,
    session: DbSession,
) -> WikiSourceResponse:
    """소스 등록 (PRD 6.8) — 스코프 read 검증 후 Ingest 잡 큐잉. 스코프 read 불가면 403."""
    try:
        source = await wiki_service.register_source(
            session, user, space_id, file_id=body.file_id, recursive=body.recursive
        )
    except WikiServiceError as exc:
        raise _http_error(exc) from exc
    # 파일명은 상세 응답과 형태를 맞춘다(등록 직후 목록에 바로 반영되도록).
    from app.services import files as files_service

    file = await files_service.get_file(session, source.file_id)
    return WikiSourceResponse(
        file_id=source.file_id,
        file_name=file.name if file else "",
        recursive=source.recursive,
        status=source.status,
        last_ingested_version=source.last_ingested_version,
    )


@router.delete(
    "/spaces/{space_id}/sources/{file_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_source(
    space_id: int, file_id: int, user: CurrentUser, session: DbSession
) -> None:
    """소스 제거 (PRD 6.8). 페이지 자동 삭제는 하지 않고 log.md 에 기록한다(Lint 로 안내)."""
    try:
        await wiki_service.remove_source(
            session, get_storage(), user, space_id, file_id
        )
    except WikiServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/spaces/{space_id}/lint", response_model=WikiLintResponse)
async def run_lint(
    space_id: int, user: CurrentUser, session: DbSession
) -> WikiLintResponse:
    """Lint 수동 실행 (PRD 6.8) — index 정합성 자동 수정 + stale/권한/링크 리포트."""
    try:
        report = await wiki_service.lint_space(
            session, get_storage(), user, space_id
        )
    except WikiServiceError as exc:
        raise _http_error(exc) from exc
    return WikiLintResponse.from_report(report)


@router.get("/spaces/{space_id}/jobs", response_model=list[WikiJobResponse])
async def list_jobs(
    space_id: int, user: CurrentUser, session: DbSession
) -> list[WikiJobResponse]:
    """스페이스 잡 이력 조회 (PRD 6.8). 스페이스 접근자만."""
    try:
        jobs = await wiki_service.list_jobs(session, user, space_id)
    except WikiServiceError as exc:
        raise _http_error(exc) from exc
    return [WikiJobResponse.from_job(j) for j in jobs]


@router.delete("/spaces/{space_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_space(
    space_id: int, user: CurrentUser, session: DbSession
) -> None:
    """스페이스 삭제 (PRD 6.8). wiki_* rows CASCADE, 위키 페이지 폴더는 드라이브에 잔존."""
    try:
        await wiki_service.delete_space(session, user, space_id)
    except WikiServiceError as exc:
        raise _http_error(exc) from exc
