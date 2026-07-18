"""공유 링크 라우터 (PRD 6.3).

두 라우터로 나눈다:
  - router        : 인증 사용자용 `/api/shares` (생성/목록/비활성화)
  - public_router : 무인증 공개 접근 `/api/public/shares/{shareUrl}` (메타/다운로드)

PRD 6.3 의 `GET /api/shares/{shareUrl}/preview` 는 공개 경로(`/api/public/shares/...`)로
대체한다. 이유: `/api/shares/{id}`(int, DELETE)와 `/api/shares/{shareUrl}`(str, GET)이 같은
경로 패턴이라 라우팅이 모호해지고, 인증 라우터와 무인증 라우터가 한 prefix 에 섞인다. 공개
접근을 별도 prefix 로 분리하면 경로 충돌이 사라지고 인증/무인증 경계도 명확해진다.

공개 접근은 게이트웨이 모델(PRD 2.2)이라 매 요청 DB 로 검증 → 비활성화/만료/횟수 초과 즉시 반영.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import CurrentUser, DbSession, RedisClient
from app.api.download import gateway_download_response
from app.schemas.files import DownloadTicketResponse
from app.schemas.shares import (
    SharePasswordRequest,
    SharePublicMeta,
    ShareCreateRequest,
    ShareResponse,
)
from app.services import shares as shares_service
from app.services import tickets as tickets_service
from app.services.shares import ShareServiceError
from app.services.storage import get_storage

router = APIRouter()
public_router = APIRouter()


def _http_error(exc: ShareServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# --- 인증 사용자용 (/api/shares) --------------------------------------------


@router.post("", response_model=ShareResponse, status_code=status.HTTP_201_CREATED)
async def create_share(
    payload: ShareCreateRequest, user: CurrentUser, session: DbSession
) -> ShareResponse:
    """공유 링크 생성 (PRD 6.3). 소유자만, 폴더 불가(400)."""
    try:
        share, file_name = await shares_service.create_share(
            session,
            user,
            file_id=payload.file_id,
            permission=payload.permission,
            expires_at=payload.expires_at,
            password=payload.password,
            max_downloads=payload.max_downloads,
        )
    except ShareServiceError as exc:
        raise _http_error(exc) from exc
    return ShareResponse.from_share(share, file_name)


@router.get("", response_model=list[ShareResponse])
async def list_shares(user: CurrentUser, session: DbSession) -> list[ShareResponse]:
    """내 공유 링크 목록 (파일명·다운로드 수·활성 상태 포함) — PRD 6.3."""
    rows = await shares_service.list_shares(session, user)
    return [ShareResponse.from_share(share, name) for share, name in rows]


@router.delete("/{share_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disable_share(
    share_id: int, user: CurrentUser, session: DbSession
) -> Response:
    """공유 링크 비활성화 (PRD 3.4). is_active=FALSE (이력 보존), 다음 요청부터 410."""
    try:
        await shares_service.disable_share(session, user, share_id)
    except ShareServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- 공개(무인증) 접근 (/api/public/shares/{shareUrl}) ----------------------

# 주의: 고정 경로 `/download` 는 `/{share_url}` 보다 먼저 선언해야 한다. FastAPI 는 선언 순서로
# 매칭하므로, 뒤에 두면 `download` 가 share_url 로 잡혀 티켓 다운로드가 동작하지 않는다.


@public_router.get("/download")
async def public_download_by_ticket(
    session: DbSession, redis: RedisClient, ticket: str = Query(...)
) -> Response:
    """티켓 기반 무헤더 공개 스트리밍 다운로드. 인가·횟수 소모는 티켓 발급 시 완료됐다.

    티켓을 원자적으로 소비(1회용)하고 파일 유효성만 확인해 스트리밍한다. 만료/재사용/무효 404.
    """
    payload = await tickets_service.consume_ticket(redis, ticket)
    if payload is None or payload.get("kind") != "share":
        raise HTTPException(status_code=404, detail="유효하지 않거나 만료된 티켓입니다.")
    try:
        internal, filename, mime = await shares_service.prepare_ticketed_share_download(
            session, get_storage(), int(payload["file_id"])
        )
    except ShareServiceError as exc:
        raise _http_error(exc) from exc
    return gateway_download_response(internal, filename, mime)


@public_router.get("/{share_url}", response_model=SharePublicMeta)
async def public_meta(share_url: str, session: DbSession) -> SharePublicMeta:
    """공개 공유 메타 (PRD 6.3). 없음 404 / 비활성·만료·삭제 410."""
    try:
        share, file = await shares_service.get_share_meta(session, share_url)
    except ShareServiceError as exc:
        raise _http_error(exc) from exc
    return SharePublicMeta(
        file_name=file.name,
        size=file.size,
        mime_type=file.mime_type,
        permission=share.permission,
        password_required=share.password_hash is not None,
        expires_at=share.expires_at,
    )


@public_router.post("/{share_url}/download")
async def public_download(
    share_url: str,
    session: DbSession,
    payload: SharePasswordRequest | None = None,
) -> Response:
    """공개 다운로드 (PRD 6.3, 2.2). 검증 순서: 활성→만료→비밀번호(401)→횟수(410).

    통과 시 download_count 원자적 증가 후 X-Accel-Redirect 스트리밍(파일 다운로드와 동일 패턴).
    권한 구분(read/download)은 X-Share-Permission 헤더로 응답에 담는다.
    """
    password = payload.password if payload is not None else None
    try:
        internal, filename, mime, permission = await shares_service.prepare_share_download(
            session, get_storage(), share_url, password
        )
    except ShareServiceError as exc:
        raise _http_error(exc) from exc

    response = gateway_download_response(internal, filename, mime)
    response.headers["X-Share-Permission"] = permission
    return response


@public_router.post("/{share_url}/download-ticket", response_model=DownloadTicketResponse)
async def public_download_ticket(
    share_url: str,
    session: DbSession,
    redis: RedisClient,
    payload: SharePasswordRequest | None = None,
) -> DownloadTicketResponse:
    """공개 다운로드 티켓 발급 (브라우저 대용량 다운로드용).

    직접 다운로드와 동일하게 활성/만료/비밀번호/횟수를 검증하고 횟수를 소모한 뒤, 60초 1회용
    티켓을 발급한다. 브라우저는 `GET /api/public/shares/download?ticket=...` 로 스트리밍한다.
    """
    password = payload.password if payload is not None else None
    try:
        file, _permission = await shares_service.authorize_share_download(
            session, share_url, password
        )
    except ShareServiceError as exc:
        raise _http_error(exc) from exc

    token = await tickets_service.issue_ticket(
        redis, {"kind": "share", "file_id": file.id}
    )
    return DownloadTicketResponse(
        ticket=token,
        url=f"/api/public/shares/download?ticket={token}",
        expires_in=tickets_service.TICKET_TTL_SECONDS,
    )
