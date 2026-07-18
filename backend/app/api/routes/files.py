"""파일/폴더 라우터 (PRD 6.2).

라우트 순서 주의: 고정 경로(/upload, /trash)를 `/{file_id}` 보다 먼저 선언해 우선 매칭시킨다.
다운로드는 게이트웨이 모델(PRD 2.2) — presigned URL 을 직접 발급하지 않고 X-Accel-Redirect 로
nginx→MinIO 스트리밍을 유도한다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi import File as FileParam

from app.api.deps import CurrentUser, DbSession, RedisClient
from app.api.download import content_disposition as _content_disposition
from app.api.download import gateway_download_response
from app.models.enums import UserStatus
from app.schemas.files import (
    DownloadTicketResponse,
    FileListResponse,
    FileRenameRequest,
    FileResponse,
    FileVersionListResponse,
    FileVersionResponse,
    FolderCreateRequest,
)
from app.services import files as files_service
from app.services import tickets as tickets_service
from app.services.files import FileServiceError
from app.services.storage import get_storage
from app.services.users import get_user_by_id

# _content_disposition 은 app.api.download.content_disposition 로 승격되어 공유 라우트와
# 공용으로 쓰인다. 기존 임포트 경로 호환을 위해 이 모듈에서도 별칭으로 노출한다.
__all__ = ["router", "_content_disposition"]

router = APIRouter()


def _http_error(exc: FileServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# --- 고정 경로 (/{file_id} 보다 먼저) ---------------------------------------


@router.post("/upload", response_model=FileResponse, status_code=status.HTTP_201_CREATED)
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
) -> FileListResponse:
    """폴더 내 항목 목록 (폴더 우선 + 이름순, 페이지네이션) — PRD 6.2."""
    try:
        items, total = await files_service.list_children(session, user, parent_id, page, size)
    except FileServiceError as exc:
        raise _http_error(exc) from exc
    return FileListResponse(
        items=[FileResponse.model_validate(f) for f in items],
        total=total,
        page=page,
        size=size,
    )


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


# --- /{file_id} ------------------------------------------------------------


@router.get("/{file_id}", response_model=FileResponse)
async def get_metadata(file_id: int, user: CurrentUser, session: DbSession) -> FileResponse:
    """파일/폴더 메타데이터 (PRD 6.2)."""
    try:
        node = files_service.ensure_file_access(
            user, await files_service.get_file(session, file_id)
        )
    except FileServiceError as exc:
        raise _http_error(exc) from exc
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
        file = files_service.ensure_file_access(
            user, await files_service.get_file(session, file_id)
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
        file = files_service.ensure_file_access(
            user, await files_service.get_file(session, file_id)
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
