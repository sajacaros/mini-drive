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
    DeviceCreateRequest,
    DeviceResponse,
    DeviceUpdateRequest,
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
    user: CurrentUser, session: DbSession, redis: RedisClient
) -> list[DeviceResponse]:
    return await beemate_service.list_devices(session, redis, user)


@router.post(
    "/devices", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED
)
async def create_device(
    payload: DeviceCreateRequest, user: CurrentUser, session: DbSession
) -> DeviceResponse:
    try:
        return await beemate_service.create_device(
            session, user, payload.name, payload.platform
        )
    except BeemateError as exc:
        raise _http_error(exc) from exc


@router.patch("/devices/{device_id}", response_model=DeviceResponse)
async def rename_device(
    device_id: int,
    payload: DeviceUpdateRequest,
    user: CurrentUser,
    session: DbSession,
    redis: RedisClient,
) -> DeviceResponse:
    try:
        return await beemate_service.rename_device(
            session, redis, user, device_id, payload.name
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


@router.get("/devices/{device_id}/events")
async def device_events(
    device_id: int, user: CurrentUser, session: DbSession
) -> StreamingResponse:
    """이 기기로 오는 전송의 SSE 스트림.

    `/api/files/events` 와 같은 처리다 — Bearer 인증(fetch 스트리밍), rate limit 없음, 요청
    세션은 소유 확인 뒤 스트림 전에 놓아준다(스트림 수명 내내 커넥션을 붙들지 않게).
    """
    try:
        device = await beemate_service.get_own_device(session, user, device_id)
    except BeemateError as exc:
        raise _http_error(exc) from exc
    await session.close()
    await beemate_service.mark_seen(device.id)
    return StreamingResponse(
        beemate_service.subscribe_device_events(device.id),
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
