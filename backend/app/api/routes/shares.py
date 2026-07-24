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

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession, RedisClient
from app.api.download import content_disposition, gateway_download_response
from app.api.preview import render_preview
from app.core.metrics import observe_download_bytes
from app.schemas.files import DownloadTicketResponse
from app.schemas.shares import (
    ShareCreateRequest,
    ShareFolderListing,
    ShareListRequest,
    ShareListResponse,
    SharePasswordRequest,
    SharePublicCrumb,
    SharePublicEntry,
    SharePublicMeta,
    ShareResponse,
    ShareStatsResponse,
)
from app.services import archives as archives_service
from app.services import share_stats as share_stats_service
from app.services import shares as shares_service
from app.services import tickets as tickets_service
from app.services.archives import ArchivePlan
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
    """공유 링크 생성 (PRD 6.3). 소유자 또는 write 이상 권한자. 파일/폴더 모두 가능.

    접근 불가 404 / 읽기만 가능하면 403(권한 부족). 폴더 공유는 방문자가 ZIP 으로 받는다.
    """
    try:
        share, file_name, is_folder = await shares_service.create_share(
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
    return ShareResponse.from_share(share, file_name, is_folder=is_folder)


@router.get("", response_model=ShareListResponse)
async def list_shares(
    user: CurrentUser,
    session: DbSession,
    redis: RedisClient,
    active: Annotated[bool | None, Query(description="활성 상태 필터")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ShareListResponse:
    """내 공유 링크 목록 (파일명·다운로드 수·접근 통계·활성 상태 포함) — PRD 3.4, 6.3.

    active 로 활성/비활성을 나눠 보고(생략 시 전체), page/size 로 페이지네이션한다.
    접근 통계(조회 수/마지막 접근)는 현재 페이지 항목만 한 번의 MGET 으로 일괄 조회한다
    (Redis 오류 시 0/None).
    """
    rows, total = await shares_service.list_shares(
        session, user, active=active, page=page, size=size
    )
    stats = await share_stats_service.get_stats_bulk(
        redis, [share.id for share, _, _ in rows]
    )
    items: list[ShareResponse] = []
    for share, name, is_folder in rows:
        s = stats.get(share.id)
        items.append(
            ShareResponse.from_share(
                share,
                name,
                is_folder=is_folder,
                view_count=s.view_count if s else 0,
                last_access_at=s.last_access_at if s else None,
            )
        )
    return ShareListResponse(items=items, total=total, page=page, size=size)


@router.get("/{share_id}/stats", response_model=ShareStatsResponse)
async def share_stats(
    share_id: int, user: CurrentUser, session: DbSession, redis: RedisClient
) -> ShareStatsResponse:
    """단일 공유 링크 접근 통계 (PRD 3.4). 소유자만. 없거나 미소유면 404."""
    try:
        share = await shares_service.get_owned_share(session, user, share_id)
    except ShareServiceError as exc:
        raise _http_error(exc) from exc
    stats = await share_stats_service.get_stats(redis, share.id)
    return ShareStatsResponse(
        share_id=share.id,
        view_count=stats.view_count,
        last_access_at=stats.last_access_at,
        download_count=share.download_count,
    )


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


async def _stream_share_archive(session: DbSession, plan: ArchivePlan) -> StreamingResponse:
    """폴더 공유의 ZIP 스트리밍 응답 (파일 아카이브 다운로드와 같은 패턴).

    인가된 바이트를 계측하고, 스트리밍이 수 분 걸릴 수 있으므로 DB 커넥션을 먼저 놓아준다
    (의존성 teardown 의 close 는 멱등하다). `X-Accel-Buffering: no` 로 nginx 버퍼링을 끈다.
    """
    observe_download_bytes(plan.total_bytes)
    await session.close()
    return StreamingResponse(
        archives_service.stream_archive(get_storage(), plan.entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": content_disposition(plan.filename),
            "X-Accel-Buffering": "no",
        },
    )


@public_router.get("/download")
async def public_download_by_ticket(
    session: DbSession, redis: RedisClient, ticket: str = Query(...)
) -> Response:
    """티켓 기반 무헤더 공개 스트리밍 다운로드. 인가·횟수 소모는 티켓 발급 시 완료됐다.

    티켓을 원자적으로 소비(1회용)하고 유효성만 확인해 스트리밍한다 — 파일(kind=share)은
    게이트웨이(X-Accel-Redirect), 폴더(kind=share-archive)는 backend 스트리밍 ZIP.
    만료/재사용/무효 404.
    """
    payload = await tickets_service.consume_ticket(redis, ticket)
    if payload is None or payload.get("kind") not in {"share", "share-archive"}:
        raise HTTPException(status_code=404, detail="유효하지 않거나 만료된 티켓입니다.")
    if payload["kind"] == "share-archive":
        try:
            plan = await shares_service.prepare_ticketed_share_archive(
                session, int(payload["share_id"]), int(payload["file_id"])
            )
        except ShareServiceError as exc:
            raise _http_error(exc) from exc
        return await _stream_share_archive(session, plan)
    try:
        internal, filename, mime = await shares_service.prepare_ticketed_share_download(
            session, get_storage(), int(payload["file_id"])
        )
    except ShareServiceError as exc:
        raise _http_error(exc) from exc
    return gateway_download_response(internal, filename, mime)


@public_router.get("/{share_url}", response_model=SharePublicMeta)
async def public_meta(
    share_url: str, session: DbSession, redis: RedisClient
) -> SharePublicMeta:
    """공개 공유 메타 (PRD 6.3). 없음 404 / 비활성·만료·삭제 410.

    유효한 메타 조회 1건을 접근 통계(조회 수/마지막 접근)에 기록한다(best-effort) — PRD 3.4.
    """
    try:
        share, file = await shares_service.get_share_meta(session, share_url)
    except ShareServiceError as exc:
        raise _http_error(exc) from exc
    await share_stats_service.record_access(redis, share.id)
    return SharePublicMeta(
        file_name=file.name,
        is_folder=file.is_folder,
        size=file.size,
        mime_type=file.mime_type,
        permission=share.permission,
        password_required=share.password_hash is not None,
        expires_at=share.expires_at,
    )


@public_router.post("/{share_url}/list", response_model=ShareFolderListing)
async def public_folder_listing(
    share_url: str,
    session: DbSession,
    redis: RedisClient,
    payload: ShareListRequest | None = None,
) -> ShareFolderListing:
    """폴더 공유 웹 탐색 목록 (구글 드라이브식 폴더 링크). 횟수와 무관한 열람이다.

    folder_id 생략 시 공유 루트, 지정 시 루트의 자손 폴더만 허용(트리 밖 404 — 존재 여부
    비노출). 검증: 활성→만료→폴더(400)→비밀번호(401). 접근 통계를 기록한다.
    """
    password = payload.password if payload is not None else None
    folder_id = payload.folder_id if payload is not None else None
    try:
        share, folder, crumbs, children = await shares_service.list_share_folder(
            session, share_url, password, folder_id
        )
    except ShareServiceError as exc:
        raise _http_error(exc) from exc
    await share_stats_service.record_access(redis, share.id)
    return ShareFolderListing(
        folder=SharePublicCrumb(id=folder.id, name=folder.name),
        breadcrumbs=[SharePublicCrumb(id=i, name=n) for i, n in crumbs],
        entries=[
            SharePublicEntry(
                id=child.id,
                name=child.name,
                is_folder=child.is_folder,
                size=child.size,
                mime_type=child.mime_type,
                updated_at=child.updated_at,
            )
            for child in children
        ],
    )


@public_router.post("/{share_url}/files/{file_id}/preview")
async def public_child_preview(
    share_url: str,
    file_id: int,
    session: DbSession,
    redis: RedisClient,
    payload: SharePasswordRequest | None = None,
) -> Response:
    """폴더 공유 안의 파일 하나 미리보기. 트리 밖 file_id 는 404 (존재 여부 비노출).

    단일 파일 공유의 미리보기와 같은 규약(이미지/PDF 인라인·텍스트 head·미지원 415)이고,
    다운로드 횟수와 무관하다. 접근 통계를 기록한다.
    """
    password = payload.password if payload is not None else None
    try:
        plan, filename, share_id = await shares_service.prepare_share_child_preview(
            session, get_storage(), share_url, password, file_id
        )
    except ShareServiceError as exc:
        raise _http_error(exc) from exc
    await share_stats_service.record_access(redis, share_id)
    return render_preview(plan, filename)


@public_router.post(
    "/{share_url}/files/{file_id}/download-ticket",
    response_model=DownloadTicketResponse,
)
async def public_child_download_ticket(
    share_url: str,
    file_id: int,
    session: DbSession,
    redis: RedisClient,
    payload: SharePasswordRequest | None = None,
) -> DownloadTicketResponse:
    """폴더 공유 안의 항목 하나 다운로드 티켓 — 파일은 원본, 하위 폴더는 ZIP.

    폴더 공유는 max_downloads 가 없어(생성 시 422) 횟수로 막히지 않지만, download_count 는
    통계로 계속 쌓는다. 하위 폴더 ZIP 은 발급 시점에 계획(상한 413)을 세워 본다.
    """
    password = payload.password if payload is not None else None
    try:
        share, node, _crumbs = await shares_service.authorize_share_child(
            session, share_url, password, file_id
        )
        if node.is_folder:
            await shares_service.plan_share_archive(session, share, node)
        await shares_service.consume_download_quota(session, share)
    except ShareServiceError as exc:
        raise _http_error(exc) from exc

    await share_stats_service.record_access(redis, share.id)

    ticket_payload = (
        {"kind": "share-archive", "share_id": share.id, "file_id": node.id}
        if node.is_folder
        else {"kind": "share", "file_id": node.id}
    )
    token = await tickets_service.issue_ticket(redis, ticket_payload)
    return DownloadTicketResponse(
        ticket=token,
        url=f"/api/public/shares/download?ticket={token}",
        expires_in=tickets_service.TICKET_TTL_SECONDS,
    )


@public_router.post("/{share_url}/preview")
async def public_preview(
    share_url: str,
    session: DbSession,
    redis: RedisClient,
    payload: SharePasswordRequest | None = None,
) -> Response:
    """공개 미리보기 (PRD 3.2, 3.4). 검증: 활성→만료→폴더(400)→비밀번호(401).

    다운로드 횟수(max_downloads)는 소모하지 않는다 — 미리보기는 열람이지 다운로드가 아니다.
    이미지/PDF 는 게이트웨이 인라인, 텍스트는 앞부분 인라인 본문, 미지원은 415. 접근 통계 기록.
    """
    password = payload.password if payload is not None else None
    try:
        plan, filename, share_id = await shares_service.prepare_share_preview(
            session, get_storage(), share_url, password
        )
    except ShareServiceError as exc:
        raise _http_error(exc) from exc
    await share_stats_service.record_access(redis, share_id)
    return render_preview(plan, filename)


@public_router.post("/{share_url}/download")
async def public_download(
    share_url: str,
    session: DbSession,
    redis: RedisClient,
    payload: SharePasswordRequest | None = None,
) -> Response:
    """공개 다운로드 (PRD 6.3, 2.2). 검증 순서: 활성→만료→비밀번호(401)→횟수(410).

    통과 시 download_count 원자적 증가 후 파일은 X-Accel-Redirect 스트리밍, 폴더는 backend
    스트리밍 ZIP. 폴더는 아카이브 계획(413 상한)을 횟수 소모 **전에** 세운다 — 내려받을 수
    없는 다운로드로 횟수를 깎지 않기 위해서다. 권한 구분(read/download)은 X-Share-Permission
    헤더로 응답에 담는다. 접근 통계도 기록한다.
    """
    password = payload.password if payload is not None else None
    plan: ArchivePlan | None = None
    try:
        share, file = await shares_service.authorize_share_access(
            session, share_url, password
        )
        if file.is_folder:
            plan = await shares_service.plan_share_archive(session, share, file)
        await shares_service.consume_download_quota(session, share)
        if not file.is_folder:
            internal, filename, mime = await shares_service.presign_share_file(
                get_storage(), file
            )
    except ShareServiceError as exc:
        raise _http_error(exc) from exc

    await share_stats_service.record_access(redis, share.id)

    if plan is not None:
        response: Response = await _stream_share_archive(session, plan)
    else:
        response = gateway_download_response(internal, filename, mime)
    response.headers["X-Share-Permission"] = share.permission
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
    티켓을 발급한다. 폴더는 발급 시점에 아카이브 계획을 실제로 세워 본다(횟수 소모 전 413) —
    소비 시 같은 판정을 다시 한다. 브라우저는 `GET /api/public/shares/download?ticket=...`
    로 스트리밍한다.
    """
    password = payload.password if payload is not None else None
    try:
        share, file = await shares_service.authorize_share_access(
            session, share_url, password
        )
        if file.is_folder:
            await shares_service.plan_share_archive(session, share, file)
        await shares_service.consume_download_quota(session, share)
    except ShareServiceError as exc:
        raise _http_error(exc) from exc

    await share_stats_service.record_access(redis, share.id)

    ticket_payload = (
        {"kind": "share-archive", "share_id": share.id, "file_id": file.id}
        if file.is_folder
        else {"kind": "share", "file_id": file.id}
    )
    token = await tickets_service.issue_ticket(redis, ticket_payload)
    return DownloadTicketResponse(
        ticket=token,
        url=f"/api/public/shares/download?ticket={token}",
        expires_in=tickets_service.TICKET_TTL_SECONDS,
    )
