"""BeeMate API — 내 기기끼리 파일 보내기 (spec/beemate.md)."""

from __future__ import annotations

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi import File as FileParam
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentUser, DbSession, RedisClient, rate_limit_user
from app.api.download import gateway_download_response
from app.schemas.beemate import (
    CLIENT_ID_PATTERN,
    DeviceRegisterRequest,
    DeviceResponse,
    DeviceUpdateRequest,
    Platform,
    TransferResponse,
)
from app.services import beemate as beemate_service
from app.services.beemate import BeemateError
from app.services.storage import get_storage

router = APIRouter()


def _http_error(exc: BeemateError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# --- 기기 -------------------------------------------------------------------


@router.get("/devices", response_model=list[DeviceResponse])
async def list_devices(
    user: CurrentUser,
    session: DbSession,
    redis: RedisClient,
    all: bool = Query(False),
) -> list[DeviceResponse]:
    """보낼 곳 목록(기본) = 즐겨찾기 + 켜진 기기, 숨김 제외. `?all=true` 는 관리용 전체.

    순서는 둘 다 즐겨찾기 → 켜진 기기 → 나머지.
    """
    return await beemate_service.list_devices(session, redis, user, all_devices=all)


@router.post("/devices", response_model=DeviceResponse)
async def register_device(
    payload: DeviceRegisterRequest, user: CurrentUser, session: DbSession, redis: RedisClient
) -> DeviceResponse:
    """자동 등록(upsert) — 폰 앱이 로그인 직후 부른다. 이미 있으면 그대로 돌려준다."""
    try:
        return await beemate_service.register_device_response(
            session, redis, user, payload.client_id, payload.name, payload.platform
        )
    except BeemateError as exc:
        raise _http_error(exc) from exc


@router.patch("/devices/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: int,
    payload: DeviceUpdateRequest,
    user: CurrentUser,
    session: DbSession,
    redis: RedisClient,
) -> DeviceResponse:
    """이름·즐겨찾기·숨김 부분 수정."""
    try:
        return await beemate_service.update_device(
            session,
            redis,
            user,
            device_id,
            name=payload.name,
            is_favorite=payload.is_favorite,
            is_hidden=payload.is_hidden,
        )
    except BeemateError as exc:
        raise _http_error(exc) from exc


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(device_id: int, user: CurrentUser, session: DbSession) -> Response:
    try:
        await beemate_service.delete_device(session, get_storage(), user, device_id)
    except BeemateError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/events")
async def events(
    user: CurrentUser,
    session: DbSession,
    client_id: Annotated[str, Query(pattern=CLIENT_ID_PATTERN)],
    name: Annotated[str, Query(min_length=1, max_length=200)],
    platform: Annotated[Platform, Query()],
) -> StreamingResponse:
    """이 클라이언트의 SSE 스트림. 붙는 순간 기기를 찾거나 만든다(등록 화면이 없다).

    프레임: `hello`(기기 id) 한 번, 이후 `pending` 신호만. 내용은 싣지 않는다 — 신호를 받으면
    `GET /transfers` 로 당겨 간다. `/api/files/events` 와 같은 처리로 Bearer 인증(fetch 스트리밍),
    rate limit 없음, 요청 세션은 스트림 전에 놓아준다.
    """
    try:
        device = await beemate_service.register_device(
            session, user, client_id, name.strip()[:100] or "기기", platform
        )
    except BeemateError as exc:
        raise _http_error(exc) from exc
    device_id = device.id
    await session.close()
    await beemate_service.mark_seen(device_id)
    return StreamingResponse(
        beemate_service.subscribe_device_events(device_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# --- 전송 -------------------------------------------------------------------


@router.post(
    "/transfers",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_user("beemate", "rate_limit_beemate_per_min"))],
)
async def create_transfer(
    user: CurrentUser,
    session: DbSession,
    file: Annotated[UploadFile, FileParam(...)],
    to_device_id: Annotated[int, Form()],
    from_device_id: Annotated[int | None, Form()] = None,
) -> TransferResponse:
    try:
        return await beemate_service.create_transfer(
            session, get_storage(), user, file, to_device_id, from_device_id
        )
    except BeemateError as exc:
        raise _http_error(exc) from exc


@router.get("/transfers", response_model=list[TransferResponse])
async def list_transfers(
    user: CurrentUser, session: DbSession, device_id: int = Query(...)
) -> list[TransferResponse]:
    try:
        return await beemate_service.list_pending(session, user, device_id)
    except BeemateError as exc:
        raise _http_error(exc) from exc


@router.get("/transfers/{transfer_id}/content")
async def transfer_content(
    transfer_id: int, user: CurrentUser, session: DbSession
) -> Response:
    """게이트웨이 스트리밍 다운로드 (X-Accel-Redirect). 드라이브 다운로드와 같은 경로다."""
    try:
        internal, filename, mime = await beemate_service.prepare_content(
            session, get_storage(), user, transfer_id
        )
    except BeemateError as exc:
        raise _http_error(exc) from exc
    return gateway_download_response(internal, filename, mime)


@router.delete("/transfers/{transfer_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_transfer(
    transfer_id: int, user: CurrentUser, session: DbSession
) -> Response:
    try:
        await beemate_service.delete_transfer(session, get_storage(), user, transfer_id)
    except BeemateError as exc:
        raise _http_error(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
