"""파일/폴더 라우터 (PRD 6.2).

라우트 순서 주의: 고정 경로(/upload, /trash)를 `/{file_id}` 보다 먼저 선언해 우선 매칭시킨다.
다운로드는 게이트웨이 모델(PRD 2.2) — presigned URL 을 직접 발급하지 않고 X-Accel-Redirect 로
nginx→MinIO 스트리밍을 유도한다.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

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

from app.api.deps import CurrentUser, DbSession
from app.schemas.files import (
    FileListResponse,
    FileRenameRequest,
    FileResponse,
    FolderCreateRequest,
)
from app.services import files as files_service
from app.services.files import FileServiceError
from app.services.storage import get_storage

router = APIRouter()


def _http_error(exc: FileServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _content_disposition(filename: str) -> str:
    """RFC 5987 filename* (UTF-8) + ASCII fallback 로 첨부 헤더를 만든다."""
    ascii_fallback = filename.encode("ascii", "replace").decode("ascii").replace('"', "")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(filename)}"


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

    return Response(
        status_code=status.HTTP_200_OK,
        headers={
            "X-Accel-Redirect": internal,
            "Content-Type": mime,
            "Content-Disposition": _content_disposition(filename),
        },
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
