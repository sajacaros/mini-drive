"""파일/폴더 라우터 (PRD 6.2).

라우트 순서 주의: 고정 경로(/upload, /trash)를 `/{file_id}` 보다 먼저 선언해 우선 매칭시킨다.
다운로드는 게이트웨이 모델(PRD 2.2) — presigned URL 을 직접 발급하지 않고 X-Accel-Redirect 로
nginx→MinIO 스트리밍을 유도한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi import File as FileParam
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession, RedisClient, rate_limit_user
from app.api.download import content_disposition as _content_disposition
from app.api.download import gateway_download_response, gateway_inline_response
from app.api.preview import render_preview
from app.core.config import settings
from app.core.metrics import observe_download_bytes
from app.models.enums import UserStatus
from app.schemas.files import (
    ArchiveTicketRequest,
    BatchUploadItem,
    BatchUploadResponse,
    DownloadTicketResponse,
    FileListResponse,
    FileMoveRequest,
    FileRenameRequest,
    FileResponse,
    FileVersionListResponse,
    FileVersionResponse,
    FolderCreateRequest,
)
from app.schemas.permissions import (
    DirectPermissionResponse,
    FilePermissionsResponse,
    InheritedPermissionResponse,
    PermissionGrantRequest,
    PermissionUpdateRequest,
    SharedItemResponse,
    SharedWithMeResponse,
)
from app.schemas.uploads import (
    ResumableInitRequest,
    ResumablePartResponse,
    ResumableReuploadInitRequest,
    ResumableSessionResponse,
)
from app.schemas.wiki import (
    WikiFolderScopeResponse,
    WikiSetRequest,
    WikiStateResponse,
)
from app.services import archives as archives_service
from app.services import favorites as favorites_service
from app.services import file_events as file_events_service
from app.services import files as files_service
from app.services import permissions as permissions_service
from app.services import recents as recents_service
from app.services import tickets as tickets_service
from app.services import uploads as uploads_service
from app.services import wiki as wiki_service
from app.services.files import FileServiceError
from app.services.permissions import PermissionServiceError
from app.services.storage import get_storage
from app.services.users import get_user_by_id
from app.services.wiki import WikiServiceError

# _content_disposition 은 app.api.download.content_disposition 로 승격되어 공유 라우트와
# 공용으로 쓰인다. 기존 임포트 경로 호환을 위해 이 모듈에서도 별칭으로 노출한다.
__all__ = ["router", "_content_disposition"]

router = APIRouter()


def _http_error(exc: FileServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _perm_http_error(exc: PermissionServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# --- 고정 경로 (/{file_id} 보다 먼저) ---------------------------------------


@router.get("/events")
async def file_events(user: CurrentUser) -> StreamingResponse:
    """파일 변경 실시간 이벤트 스트림 (SSE, Phase 8-1).

    인증은 기존 Bearer 의존(fetch 스트리밍이 헤더를 붙인다 — EventSource 는 헤더 불가라 쓰지
    않는다). Redis pub/sub `file-events` 를 구독해 **구독자 권한 필터**(read 이상)를 통과한
    이벤트만 전달하고, 30초마다 keepalive 주석(ping)을 보낸다. 클라이언트 종료 시 구독 정리.

    rate limit 은 이 경로에 걸지 않는다(장수명 스트림). 미들웨어는 async generator 를 그대로
    스트리밍하므로 워커를 막지 않는다.
    """
    return StreamingResponse(
        file_events_service.subscribe_file_events(user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx 등 프록시의 응답 버퍼링 비활성화 — 이벤트 즉시 전달.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/favorites", response_model=FileListResponse)
async def list_favorites(
    user: CurrentUser,
    session: DbSession,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
) -> FileListResponse:
    """내 즐겨찾기 목록 (Phase 8-2). 삭제·접근권 상실 파일은 숨긴다(조회 시 권한 재검증)."""
    items, total = await favorites_service.list_favorites(session, user, page, size)
    await files_service.annotate_location(session, user, items)
    await files_service.annotate_listing_meta(session, user, items)
    responses = [FileResponse.model_validate(f) for f in items]
    return FileListResponse(items=responses, total=total, page=page, size=size)


@router.get("/recent", response_model=list[FileResponse])
async def list_recent(
    user: CurrentUser,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=recents_service.MAX_RECENT_LIMIT)] = (
        recents_service.DEFAULT_RECENT_LIMIT
    ),
) -> list[FileResponse]:
    """최근 이용 항목 (Phase 8-3, 최신순). 삭제·접근불가 제외."""
    items = await recents_service.list_recent(session, user, limit)
    await favorites_service.annotate_is_favorite(session, user, items)
    await files_service.annotate_location(session, user, items)
    await files_service.annotate_listing_meta(session, user, items)
    return [FileResponse.model_validate(f) for f in items]


@router.get("/shared-with-me", response_model=SharedWithMeResponse)
async def shared_with_me(user: CurrentUser, session: DbSession) -> SharedWithMeResponse:
    """내 소속 그룹에 공유된 항목(부여 지점) 목록 (PRD 3.1.3). 폴더로 진입해 하위 탐색."""
    items = await permissions_service.list_shared_with_me(session, user)
    # 소유자명은 배치로, permission/group_names 는 이미 서비스가 돌려준 부여 행에서 그대로 채운다
    # (공유받은 항목이므로 permission = 부여 수준, group_names = 부여 그룹명 한 건).
    await files_service.annotate_owner_names(session, [item.file for item in items])
    for item in items:
        item.file.permission = item.permission  # type: ignore[attr-defined]
        item.file.group_names = [item.group_name]  # type: ignore[attr-defined]
    return SharedWithMeResponse(
        items=[
            SharedItemResponse(
                file=FileResponse.model_validate(item.file),
                group_id=item.group_id,
                group_name=item.group_name,
                permission=item.permission,
            )
            for item in items
        ]
    )


@router.post(
    "/upload",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_user("upload", "rate_limit_upload_per_min"))],
)
async def upload(
    user: CurrentUser,
    session: DbSession,
    file: Annotated[UploadFile, FileParam(...)],
    parent_id: Annotated[int | None, Form()] = None,
) -> FileResponse:
    """파일 업로드 (multipart, 스트리밍) — PRD 3.2. parent_id 생략 시 루트."""
    try:
        created = await files_service.upload_file(
            session, get_storage(), user, file, parent_id
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return FileResponse.model_validate(created)


@router.post(
    "/batch",
    response_model=BatchUploadResponse,
    dependencies=[Depends(rate_limit_user("upload", "rate_limit_upload_per_min"))],
)
async def batch_upload(
    user: CurrentUser,
    session: DbSession,
    files: Annotated[list[UploadFile], FileParam()] = [],  # noqa: B006 - FastAPI 기본값
    paths: Annotated[list[str], Form()] = [],  # noqa: B006
    dirs: Annotated[list[str], Form()] = [],  # noqa: B006
    parent_id: Annotated[int | None, Form()] = None,
) -> BatchUploadResponse:
    """배치 업로드 — 여러 파일을 각자의 상대 경로대로 저장한다 (폴더 업로드).

    `paths[i]` 는 `files[i]` 의 파일명을 포함한 상대 경로("docs/img/a.png")다. 중간 폴더는
    없으면 만들고 있으면 재사용한다. `dirs` 만 보내고 `files` 를 비우면 폴더 트리만 확정하며,
    응답 `folders` 맵으로 상위 경로의 폴더 id 를 받아갈 수 있다.

    부분 실패는 정상이다 — 항목별 성패를 `items` 로 돌려주고 HTTP 는 200 이다.

    본문 크기 상한은 BatchBodyLimitMiddleware 가 파싱 전에 Content-Length 로 건다.
    여기서는 파싱 후에 알 수 있는 개수/합계를 이중으로 확인한다.
    """
    if len(files) != len(paths):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="files 와 paths 의 개수가 다릅니다.",
        )
    if len(files) > settings.max_batch_files:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"한 배치에 담을 수 있는 파일은 {settings.max_batch_files}개까지입니다.",
        )
    total = sum(files_service._upload_size(f) for f in files)
    if total > settings.max_batch_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="요청 본문이 배치 상한을 초과했습니다.",
        )

    try:
        items, folders = await files_service.batch_upload(
            session, get_storage(), user, files, paths, dirs, parent_id
        )
    except FileServiceError as exc:
        # 최상위 parent_id 해석 실패 등 배치 전체가 성립하지 않는 경우만 여기로 온다.
        raise _http_error(exc) from exc

    return BatchUploadResponse(
        items=[BatchUploadItem.model_validate(it) for it in items],
        folders=folders,
        succeeded=sum(1 for it in items if it["status"] == "created"),
        failed=sum(1 for it in items if it["status"] == "error"),
    )


# --- 재개 가능 업로드 (PRD 3.2) — 고정 prefix /uploads 로 /{file_id} 충돌 회피 ---


def _session_response(
    row, parts: list[tuple[int, int, str]] | None = None
) -> ResumableSessionResponse:
    """UploadSession + (선택) 스테이징 파트 목록을 재개 응답으로 변환."""
    return ResumableSessionResponse(
        session_id=row.id,
        kind=row.kind,
        file_id=row.file_id,
        part_size=row.part_size,
        total_parts=uploads_service._total_parts(row.total_size, row.part_size),
        total_size=row.total_size,
        uploaded_parts=uploads_service.uploaded_part_numbers(parts or []),
        received_bytes=uploads_service.received_bytes(parts or []),
        expires_at=row.expires_at,
    )


@router.post(
    "/uploads",
    response_model=ResumableSessionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_user("upload", "rate_limit_upload_per_min"))],
)
async def init_resumable_upload(
    payload: ResumableInitRequest, user: CurrentUser, session: DbSession
) -> ResumableSessionResponse:
    """새 파일 재개 업로드 세션 개시 (PRD 3.2). part_size 로 청크를 나눠 이어올린다."""
    try:
        row = await uploads_service.init_new_upload(
            session,
            get_storage(),
            user,
            filename=payload.filename,
            parent_id=payload.parent_id,
            total_size=payload.total_size,
            mime_type=payload.mime_type,
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return _session_response(row)


@router.get("/uploads/{session_id}", response_model=ResumableSessionResponse)
async def get_resumable_session(
    session_id: str, user: CurrentUser, session: DbSession
) -> ResumableSessionResponse:
    """세션 상태/재개 정보 (PRD 3.2). 이미 올라간 파트 번호로 남은 파트만 이어올린다."""
    try:
        row, parts = await uploads_service.session_status(
            session, get_storage(), user, session_id
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return _session_response(row, parts)


@router.put(
    "/uploads/{session_id}/parts/{part_number}",
    response_model=ResumablePartResponse,
)
async def upload_resumable_part(
    session_id: str,
    part_number: int,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> ResumablePartResponse:
    """단일 파트 업로드 (PRD 3.2). 본문은 raw 바이트(backend 경유 스트리밍).

    같은 파트를 다시 올리면 덮어써 재시도를 지원한다. 파트 크기는 part_size(마지막 제외) 고정.
    """
    data = await _read_capped(request, settings.resumable_part_size)
    try:
        pn, size, etag = await uploads_service.upload_part(
            session, get_storage(), user, session_id, part_number, data
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return ResumablePartResponse(part_number=pn, size=size, etag=etag)


@router.post(
    "/uploads/{session_id}/complete",
    response_model=FileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def complete_resumable_upload(
    session_id: str, user: CurrentUser, session: DbSession
) -> FileResponse:
    """업로드 완료 = 파트 병합 → 파일 확정 (PRD 3.2). 새 파일/새 버전 경로로 합류."""
    try:
        file = await uploads_service.complete_upload(
            session, get_storage(), user, session_id
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return FileResponse.model_validate(file)


@router.delete("/uploads/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def abort_resumable_upload(
    session_id: str, user: CurrentUser, session: DbSession
) -> Response:
    """업로드 세션 중단 (PRD 3.2). 스테이징 파트를 폐기하고 세션을 무효화한다."""
    try:
        await uploads_service.abort_upload(session, get_storage(), user, session_id)
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _read_capped(request: Request, cap: int) -> bytes:
    """요청 본문을 스트리밍으로 읽되 cap 바이트를 넘으면 413 으로 조기 차단(OOM 방지)."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="파트가 허용 크기를 초과했습니다.",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/trash", response_model=list[FileResponse])
async def trash(user: CurrentUser, session: DbSession) -> list[FileResponse]:
    """휴지통 목록 — 직접 삭제된 최상위 항목만 (PRD 6.2)."""
    rows = await files_service.list_trash(session, user)
    return [FileResponse.model_validate(r) for r in rows]


@router.get("", response_model=FileListResponse)
async def list_files(
    user: CurrentUser,
    session: DbSession,
    parent_id: Annotated[int | None, Query(alias="parentId")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 50,
    folders_only: Annotated[bool, Query(alias="foldersOnly")] = False,
) -> FileListResponse:
    """폴더 내 항목 목록 (폴더 우선 + 이름순, 페이지네이션) — PRD 6.2.

    foldersOnly=true 면 하위 폴더만 반환한다(이동 대상 선택기).
    """
    try:
        items, total = await files_service.list_children(
            session, user, parent_id, page, size, folders_only
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    await favorites_service.annotate_is_favorite(session, user, items)
    await files_service.annotate_listing_meta(session, user, items)
    responses = [FileResponse.model_validate(f) for f in items]
    return FileListResponse(items=responses, total=total, page=page, size=size)


@router.post("", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderCreateRequest, user: CurrentUser, session: DbSession
) -> FileResponse:
    """폴더 생성 (PRD 6.2). 같은 폴더 내 동명 시 409."""
    try:
        folder = await files_service.create_folder(session, user, payload.name, payload.parent_id)
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return FileResponse.model_validate(folder)


@router.get("/download")
async def download_by_ticket(
    session: DbSession, redis: RedisClient, ticket: str = Query(...)
) -> Response:
    """티켓 기반 무헤더 스트리밍 다운로드 (브라우저 대용량 다운로드용).

    발급 시 이미 인가된 티켓을 원자적으로 소비(1회용)하고, 저장된 사용자/파일/버전으로
    게이트웨이 다운로드를 재구성한다. 만료/재사용/무효 티켓은 404.
    """
    payload = await tickets_service.consume_ticket(redis, ticket)
    if payload is None or payload.get("kind") != "file":
        raise HTTPException(status_code=404, detail="유효하지 않거나 만료된 티켓입니다.")

    user = await get_user_by_id(session, int(payload["uid"]))
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="유효하지 않거나 만료된 티켓입니다.")

    file_id = int(payload["file_id"])
    version = payload.get("version")
    try:
        if version is None:
            internal, filename, mime = await files_service.prepare_download(
                session, get_storage(), user, file_id
            )
        else:
            internal, filename, mime = await files_service.prepare_version_download(
                session, get_storage(), user, file_id, int(version)
            )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return gateway_download_response(internal, filename, mime)


# --- ZIP 아카이브 다운로드 (폴더 / 다중 선택) --------------------------------
#
# 여기만 게이트웨이 모델의 예외다: X-Accel-Redirect 는 오브젝트 하나만 흘려보낼 수 있어,
# 여러 오브젝트를 한 파일로 묶으려면 backend 가 스트리밍 주체가 되어야 한다.


@router.post("/download-archive-ticket", response_model=DownloadTicketResponse)
async def issue_archive_download_ticket(
    payload: ArchiveTicketRequest,
    user: CurrentUser,
    session: DbSession,
    redis: RedisClient,
) -> DownloadTicketResponse:
    """폴더/다중 선택 ZIP 다운로드 티켓 발급.

    발급 시점에 전체 계획(접근 판정 + 개수/용량 상한)을 실제로 세워 본다 — 스트리밍이
    시작된 뒤에는 실패를 HTTP 상태로 알릴 수 없기 때문이다. 계획 자체는 버리고, 소비 시
    같은 판정을 다시 한다(그 사이 권한이 바뀌었을 수 있다).
    """
    try:
        await archives_service.plan_archive(session, user, payload.file_ids)
    except FileServiceError as exc:
        raise _http_error(exc) from exc

    token = await tickets_service.issue_ticket(
        redis, {"kind": "archive", "file_ids": payload.file_ids, "uid": user.id}
    )
    return DownloadTicketResponse(
        ticket=token,
        url=f"/api/files/download-archive?ticket={token}",
        expires_in=tickets_service.TICKET_TTL_SECONDS,
    )


@router.get("/download-archive")
async def download_archive_by_ticket(
    session: DbSession, redis: RedisClient, ticket: str = Query(...)
) -> StreamingResponse:
    """티켓 기반 ZIP 스트리밍 다운로드 (무헤더 GET).

    티켓을 원자적으로 소비한 뒤 접근 판정을 다시 하고, MinIO 에서 읽은 청크를 그대로 ZIP
    프레임에 실어 내보낸다. `X-Accel-Buffering: no` 로 nginx 가 응답 전체를 버퍼링하지
    않게 해 첫 바이트가 바로 브라우저에 닿게 한다.
    """
    payload = await tickets_service.consume_ticket(redis, ticket)
    if payload is None or payload.get("kind") != "archive":
        raise HTTPException(status_code=404, detail="유효하지 않거나 만료된 티켓입니다.")

    user = await get_user_by_id(session, int(payload["uid"]))
    if user is None or user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=404, detail="유효하지 않거나 만료된 티켓입니다.")

    try:
        plan = await archives_service.plan_archive(
            session, user, [int(i) for i in payload["file_ids"]]
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc

    # 인가된 바이트를 계측한다 (PRD 11장) — 단일 다운로드의 게이트웨이 계측과 같은 기준.
    observe_download_bytes(plan.total_bytes)
    # 스트리밍은 수 분이 걸릴 수 있다. DB 커넥션을 그동안 붙들지 않도록 먼저 놓아준다
    # (의존성 teardown 의 close 는 멱등하다).
    await session.close()

    return StreamingResponse(
        archives_service.stream_archive(get_storage(), plan.entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": _content_disposition(plan.filename),
            "X-Accel-Buffering": "no",
        },
    )


# --- /{file_id} ------------------------------------------------------------


@router.get("/{file_id}", response_model=FileResponse)
async def get_metadata(file_id: int, user: CurrentUser, session: DbSession) -> FileResponse:
    """파일/폴더 메타데이터 (PRD 6.2)."""
    try:
        node = await files_service.ensure_file_access(
            session, user, await files_service.get_file(session, file_id)
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    node.is_favorite = await favorites_service.is_favorite(session, user.id, node.id)  # type: ignore[attr-defined]
    await files_service.annotate_listing_meta(session, user, [node])
    return FileResponse.model_validate(node)


@router.get("/{file_id}/download")
async def download(file_id: int, user: CurrentUser, session: DbSession) -> Response:
    """게이트웨이 스트리밍 다운로드 (PRD 2.2, 6.2).

    presigned URL 을 브라우저에 직접 주지 않고, 내부 presign(60s)을 X-Accel-Redirect 로
    nginx 에 넘겨 MinIO 스트리밍을 유도한다. 폴더면 400.
    """
    try:
        internal, filename, mime = await files_service.prepare_download(
            session, get_storage(), user, file_id
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc

    return gateway_download_response(internal, filename, mime)


@router.get("/{file_id}/thumbnail")
async def thumbnail(file_id: int, user: CurrentUser, session: DbSession) -> Response:
    """썸네일 조회 (PRD 3.2). read 권한 필요, 게이트웨이 인라인(image/png) 스트리밍.

    이미지가 아니거나 아직 생성되지 않았으면 404 — 프론트는 기본 아이콘으로 폴백한다.
    다운로드와 동일한 접근 검사를 거치며 admin 우회 경로는 없다(파일 내용이므로).
    """
    try:
        internal = await files_service.prepare_thumbnail(
            session, get_storage(), user, file_id
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return gateway_inline_response(internal, f"thumb-{file_id}.png", "image/png")


@router.get("/{file_id}/preview")
async def preview(file_id: int, user: CurrentUser, session: DbSession) -> Response:
    """파일 미리보기 (PRD 3.2). read 권한 필요.

    이미지/PDF 는 게이트웨이 인라인 스트리밍, 텍스트는 앞부분(최대 1MiB) 인라인 본문,
    미지원 형식은 415(구조화 detail)로 프론트가 다운로드로 폴백하게 한다. 폴더 400.
    """
    try:
        plan, filename = await files_service.prepare_preview(
            session, get_storage(), user, file_id
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    # 미리보기 성공 = 최근 이용 기록 (fail-open). 공개 공유(무인증) 경로는 이 라우트를 타지 않는다.
    await recents_service.record_recent(session, user.id, file_id)
    return render_preview(plan, filename)


@router.put("/{file_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
async def add_favorite(file_id: int, user: CurrentUser, session: DbSession) -> Response:
    """즐겨찾기 등록 (Phase 8-2, 멱등). read 이상 접근 가능해야 하며 불가 시 404(존재 은닉)."""
    try:
        await favorites_service.add_favorite(session, user, file_id)
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{file_id}/favorite", status_code=status.HTTP_204_NO_CONTENT)
async def remove_favorite(
    file_id: int, user: CurrentUser, session: DbSession
) -> Response:
    """즐겨찾기 해제 (Phase 8-2, 멱등). 접근권과 무관하게 해제 가능(없으면 no-op)."""
    await favorites_service.remove_favorite(session, user, file_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{file_id}/upload", response_model=FileResponse, status_code=status.HTTP_201_CREATED
)
async def reupload(
    file_id: int,
    user: CurrentUser,
    session: DbSession,
    file: Annotated[UploadFile, FileParam(...)],
    base_version: Annotated[int | None, Form()] = None,
) -> FileResponse:
    """재업로드 = 새 버전 (PRD 3.3). base_version 이 현재 버전과 다르면 409(충돌).

    미전달 시 충돌 검사 없이 강제 덮어쓰기. 직전 원본은 스냅샷으로 보존된다.
    """
    try:
        updated = await files_service.reupload_file(
            session, get_storage(), user, file_id, file, base_version
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return FileResponse.model_validate(updated)


@router.post(
    "/{file_id}/uploads",
    response_model=ResumableSessionResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_user("upload", "rate_limit_upload_per_min"))],
)
async def init_resumable_reupload(
    file_id: int,
    payload: ResumableReuploadInitRequest,
    user: CurrentUser,
    session: DbSession,
) -> ResumableSessionResponse:
    """기존 파일 재업로드(새 버전) 재개 세션 개시 (PRD 3.2, 3.3).

    base_version 이 현재 버전과 다르면 409(충돌). 완료 시 기존 재업로드와 동일한 버저닝 경로.
    """
    try:
        row = await uploads_service.init_version_upload(
            session,
            get_storage(),
            user,
            file_id,
            total_size=payload.total_size,
            mime_type=payload.mime_type,
            base_version=payload.base_version,
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return _session_response(row)


@router.get("/{file_id}/versions", response_model=FileVersionListResponse)
async def list_versions(
    file_id: int, user: CurrentUser, session: DbSession
) -> FileVersionListResponse:
    """버전 히스토리 (PRD 6.2). 최신 버전이 먼저 온다."""
    try:
        file, rows = await files_service.list_versions(session, user, file_id)
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return FileVersionListResponse(
        file_id=file.id,
        current_version=file.current_version,
        items=[
            FileVersionResponse(
                version=v.version,
                size=v.size,
                mime_type=v.mime_type,
                uploaded_by=v.uploaded_by,
                uploaded_by_name=name,
                uploaded_at=v.uploaded_at,
                is_current=(v.version == file.current_version),
            )
            for v, name in rows
        ],
    )


@router.get("/{file_id}/versions/{version}/download")
async def download_version(
    file_id: int, version: int, user: CurrentUser, session: DbSession
) -> Response:
    """특정 버전 게이트웨이 다운로드 (PRD 6.2). 파일명에 버전 표기."""
    try:
        internal, filename, mime = await files_service.prepare_version_download(
            session, get_storage(), user, file_id, version
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return gateway_download_response(internal, filename, mime)


@router.post(
    "/{file_id}/versions/{version}/restore", response_model=FileResponse
)
async def restore_version(
    file_id: int, version: int, user: CurrentUser, session: DbSession
) -> FileResponse:
    """과거 버전을 새 버전으로 복사 생성 (PRD 3.3, 이력 보존)."""
    try:
        updated = await files_service.restore_version(
            session, get_storage(), user, file_id, version
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return FileResponse.model_validate(updated)


@router.post("/{file_id}/download-ticket", response_model=DownloadTicketResponse)
async def issue_download_ticket(
    file_id: int, user: CurrentUser, session: DbSession, redis: RedisClient
) -> DownloadTicketResponse:
    """현재 버전 다운로드 티켓 발급 (브라우저 대용량 다운로드용). 인가 후 60초 1회용 티켓."""
    try:
        file = await files_service.ensure_file_access(
            session, user, await files_service.get_file(session, file_id)
        )
        if file.is_folder:
            raise FileServiceError(400, "폴더는 다운로드할 수 없습니다.")
        if file.is_deleted:
            raise FileServiceError(404, "파일을 찾을 수 없습니다.")
    except FileServiceError as exc:
        raise _http_error(exc) from exc

    token = await tickets_service.issue_ticket(
        redis, {"kind": "file", "file_id": file.id, "version": None, "uid": user.id}
    )
    # 다운로드 티켓 발급 성공 = 최근 이용 기록 (fail-open).
    await recents_service.record_recent(session, user.id, file.id)
    return DownloadTicketResponse(
        ticket=token,
        url=f"/api/files/download?ticket={token}",
        expires_in=tickets_service.TICKET_TTL_SECONDS,
    )


@router.post(
    "/{file_id}/versions/{version}/download-ticket",
    response_model=DownloadTicketResponse,
)
async def issue_version_download_ticket(
    file_id: int,
    version: int,
    user: CurrentUser,
    session: DbSession,
    redis: RedisClient,
) -> DownloadTicketResponse:
    """특정 버전 다운로드 티켓 발급 (브라우저 대용량 다운로드용). 인가 후 60초 1회용."""
    try:
        file = await files_service.ensure_file_access(
            session, user, await files_service.get_file(session, file_id)
        )
        if file.is_folder:
            raise FileServiceError(400, "폴더는 다운로드할 수 없습니다.")
        # 버전 존재 검증까지 수행해 발급 시점에 인가+유효성을 함께 확정한다.
        await files_service.prepare_version_download(
            session, get_storage(), user, file_id, version
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc

    token = await tickets_service.issue_ticket(
        redis,
        {"kind": "file", "file_id": file.id, "version": version, "uid": user.id},
    )
    # 다운로드 티켓 발급 성공 = 최근 이용 기록 (fail-open).
    await recents_service.record_recent(session, user.id, file.id)
    return DownloadTicketResponse(
        ticket=token,
        url=f"/api/files/download?ticket={token}",
        expires_in=tickets_service.TICKET_TTL_SECONDS,
    )


@router.put("/{file_id}", response_model=FileResponse)
async def rename(
    file_id: int, payload: FileRenameRequest, user: CurrentUser, session: DbSession
) -> FileResponse:
    """이름 변경 (PRD 6.2). 동명 충돌 시 409."""
    try:
        file = await files_service.rename_file(session, user, file_id, payload.name)
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return FileResponse.model_validate(file)


@router.post("/{file_id}/move", response_model=FileResponse)
async def move(
    file_id: int, payload: FileMoveRequest, user: CurrentUser, session: DbSession
) -> FileResponse:
    """다른 폴더로 이동 (PRD 6.2). 대상 위치 동명 충돌 시 409, 순환 이동은 400."""
    try:
        file = await files_service.move_file(session, user, file_id, payload.parent_id)
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return FileResponse.model_validate(file)


@router.post("/{file_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete(file_id: int, user: CurrentUser, session: DbSession) -> Response:
    """소프트 삭제 (PRD 6.2). 폴더면 하위 전체 재귀."""
    try:
        await files_service.soft_delete(session, user, file_id)
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{file_id}/permanent-delete", status_code=status.HTTP_204_NO_CONTENT)
async def permanent_delete(file_id: int, user: CurrentUser, session: DbSession) -> Response:
    """영구 삭제 (PRD 6.2). 휴지통 항목만 허용."""
    try:
        await files_service.permanent_delete(session, get_storage(), user, file_id)
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{file_id}/restore-trash", response_model=FileResponse)
async def restore(file_id: int, user: CurrentUser, session: DbSession) -> FileResponse:
    """휴지통 복구 (PRD 6.2). 부모가 삭제됐으면 루트로, 동명 충돌 시 409."""
    try:
        file = await files_service.restore_trash(session, user, file_id)
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return FileResponse.model_validate(file)


# --- 파일 그룹 권한 (PRD 6.5) — 폴더도 files 행이므로 /folders 별칭 없이 통일 ----


@router.post(
    "/{file_id}/permissions",
    response_model=DirectPermissionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def grant_permission(
    file_id: int,
    payload: PermissionGrantRequest,
    user: CurrentUser,
    session: DbSession,
) -> DirectPermissionResponse:
    """그룹 권한 부여 (PRD 6.5). 소유자/manage 권한자만. (file,group) 중복은 upsert.

    read/write/manage 모두 부여할 수 있다. manage 는 받는 쪽이 이 항목을 **다른 그룹에도 열어줄
    수 있는** 권한 위임이므로(+ 영구 삭제), 부여 UI 에서 그 범위를 명시한다.
    """
    try:
        row = await permissions_service.grant_permission(
            session,
            user,
            file_id,
            payload.group_id,
            payload.permission,
            payload.inherit_to_children,
            payload.expires_at,
        )
    except PermissionServiceError as exc:
        raise _perm_http_error(exc) from exc
    return await _direct_permission_response(session, row)


@router.get("/{file_id}/permissions", response_model=FilePermissionsResponse)
async def list_permissions(
    file_id: int, user: CurrentUser, session: DbSession
) -> FilePermissionsResponse:
    """직접 부여 목록 + 유효 상속 권한 목록 (PRD 6.5/6.6). manage 권한자만."""
    try:
        direct, inherited = await permissions_service.list_permissions(
            session, user, file_id
        )
    except PermissionServiceError as exc:
        raise _perm_http_error(exc) from exc
    return FilePermissionsResponse(
        file_id=file_id,
        direct=[
            DirectPermissionResponse(
                group_id=d.group_id,
                group_name=d.group_name,
                permission=d.permission,
                inherit_to_children=d.inherit_to_children,
                granted_at=d.granted_at,
                expires_at=d.expires_at,
                granted_by=d.granted_by,
            )
            for d in direct
        ],
        inherited=[
            InheritedPermissionResponse(
                group_id=i.group_id,
                group_name=i.group_name,
                permission=i.permission,
                source_file_id=i.source_file_id,
                source_file_name=i.source_file_name,
                depth=i.depth,
                expires_at=i.expires_at,
            )
            for i in inherited
        ],
    )


@router.put(
    "/{file_id}/permissions/{group_id}", response_model=DirectPermissionResponse
)
async def update_permission(
    file_id: int,
    group_id: int,
    payload: PermissionUpdateRequest,
    user: CurrentUser,
    session: DbSession,
) -> DirectPermissionResponse:
    """그룹 권한 수정 (PRD 6.5). permission/inherit_to_children/expires_at 부분 갱신.

    부여와 같은 정책 — read/write/manage 간 승격·강등 모두 허용한다.
    """
    try:
        row = await permissions_service.update_permission(
            session,
            user,
            file_id,
            group_id,
            payload.permission,
            payload.inherit_to_children,
            payload.expires_at,
            expires_at_set="expires_at" in payload.model_fields_set,
        )
    except PermissionServiceError as exc:
        raise _perm_http_error(exc) from exc
    return await _direct_permission_response(session, row)


@router.delete(
    "/{file_id}/permissions/{group_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_permission(
    file_id: int, group_id: int, user: CurrentUser, session: DbSession
) -> Response:
    """그룹 권한 회수 (PRD 6.5). 소유자/manage 권한자만."""
    try:
        await permissions_service.revoke_permission(session, user, file_id, group_id)
    except PermissionServiceError as exc:
        raise _perm_http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- 위키 (spec/wiki-index.md) ------------------------------------------------
#
# 경로가 /api/files/* 이므로 권한 부여와 같은 이유로 이 라우터에 둔다. 위키 문서 목록·질의는
# /api/wiki/* 라 routes/wiki.py 가 담당한다.


def _wiki_http_error(exc: WikiServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _wiki_state_response(overview: wiki_service.WikiOverview) -> WikiStateResponse:
    scope = overview.scope
    return WikiStateResponse(
        file_id=overview.file.id,
        is_folder=overview.file.is_folder,
        enabled=overview.state.enabled,
        explicit=overview.state.explicit,
        source_file_id=overview.state.source_file_id,
        public=overview.public,
        indexable=overview.verdict.ok,
        reason=overview.verdict.reason,
        status=overview.status,
        indexed_version=overview.indexed_version,
        folder_scope=(
            WikiFolderScopeResponse(
                target_count=scope.target_count,
                skipped_by_format=scope.skipped_by_format,
                skipped_by_size=scope.skipped_by_size,
                skipped_by_permission=scope.skipped_by_permission,
            )
            if scope is not None
            else None
        ),
    )


@router.get("/{file_id}/wiki", response_model=WikiStateResponse)
async def get_wiki(
    file_id: int, user: CurrentUser, session: DbSession
) -> WikiStateResponse:
    """위키 토글 상태 — 인덱싱 여부/상속 출처/전사 공개/대상 판정. 소유자·manage 만."""
    try:
        overview = await wiki_service.get_overview(session, user, file_id)
    except WikiServiceError as exc:
        raise _wiki_http_error(exc) from exc
    return _wiki_state_response(overview)


@router.put("/{file_id}/wiki", response_model=WikiStateResponse)
async def set_wiki(
    file_id: int, payload: WikiSetRequest, user: CurrentUser, session: DbSession
) -> WikiStateResponse:
    """위키 인덱싱 on/off + 전사 공개 (spec/wiki-index.md). 소유자·manage 만.

    `enabled` 는 null 이 '상속으로 되돌리기'라는 뜻이라 '생략'과 구분해야 한다 —
    본문에 없으면 인덱싱 설정을 건드리지 않는다.
    """
    sent = payload.model_fields_set
    try:
        await wiki_service.set_wiki(
            session,
            user,
            file_id,
            enabled=payload.enabled,
            enabled_set="enabled" in sent,
            public=payload.public if "public" in sent else None,
        )
        overview = await wiki_service.get_overview(session, user, file_id)
    except WikiServiceError as exc:
        raise _wiki_http_error(exc) from exc
    return _wiki_state_response(overview)


@router.delete("/{file_id}/wiki/tree", status_code=status.HTTP_204_NO_CONTENT)
async def purge_wiki_tree(
    file_id: int, user: CurrentUser, session: DbSession
) -> Response:
    """이 파일의 트리를 즉시 지운다 (유예를 기다리지 않음). 소유자·manage 만.

    위키를 끄면 트리는 재켜기 비용 때문에 유예 기간 동안 보관된다. 규정상 파생물이 남으면
    안 되는 경우를 위한 탈출구다(spec/wiki-index.md 「끄기 — 차단은 즉시, 삭제는 유예」).
    """
    try:
        await wiki_service.purge_tree_now(session, user, file_id)
    except WikiServiceError as exc:
        raise _wiki_http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _direct_permission_response(
    session: DbSession, row: object
) -> DirectPermissionResponse:
    """FileGroupPermission 행을 그룹명과 함께 응답으로 변환."""
    from app.models import Group

    group = await session.get(Group, row.group_id)  # type: ignore[attr-defined]
    return DirectPermissionResponse(
        group_id=row.group_id,  # type: ignore[attr-defined]
        group_name=group.name if group else "",
        permission=row.permission,  # type: ignore[attr-defined]
        inherit_to_children=row.inherit_to_children,  # type: ignore[attr-defined]
        granted_at=row.granted_at,  # type: ignore[attr-defined]
        expires_at=row.expires_at,  # type: ignore[attr-defined]
        granted_by=row.granted_by,  # type: ignore[attr-defined]
    )
