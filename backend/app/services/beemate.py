"""BeeMate — 내 기기끼리 파일 보내기 (spec/beemate.md).

기기 등록·전송 저장·수신 목록·받음 확인(=삭제)·만료 정리와, 기기별 실시간 채널을 맡는다.

설계 원칙:
  - **소유 판정은 user_id 하나.** 1차 범위는 내 기기끼리라 권한 체계를 새로 만들지 않는다.
    남의 기기·전송은 존재 여부를 흘리지 않도록 전부 404 다.
  - **채널을 기기별로 나눈다.** `file-events` 처럼 한 채널에 싣고 구독자마다 거르는 대신,
    구독 라우트가 기기 소유를 확인하는 것으로 권한 판정을 끝낸다.
  - **받으면 지운다.** 상태 컬럼이 없다 — 행이 있으면 아직 안 받은 것이다. 삭제는 오브젝트
    먼저, 행은 나중. 순서가 반대면 행 삭제 뒤 오브젝트 삭제가 실패했을 때 가리키는 행이 없는
    고아 오브젝트가 남아 어떤 정리도 찾지 못한다. 오브젝트 먼저면 실패해도 행이 남고, 그 행은
    만료 정리가 다시 집는다.
  - **발행은 fail-open.** Redis 가 죽어도 전송은 저장되고, 받는 쪽은 다음 접속 때 목록으로 받는다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import UploadFile
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import SessionFactory
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.models import BeemateDevice, BeemateTransfer, User
from app.schemas.beemate import DeviceResponse, TransferResponse
from app.services.files import _upload_size
from app.services.storage import StorageService

_log = get_logger("app.beemate")

# 온라인 표시 — 스트림 keepalive(30초)마다 TTL 을 갱신한다. 두 번 놓치면 오프라인.
KEEPALIVE_SECONDS = 30.0
ONLINE_TTL_SECONDS = 75

# 만료 정리 한 배치 — 트랜잭션 크기 제한이며, 한 번 부를 때 배치가 덜 찰 때까지 반복한다.
_PURGE_BATCH = 500


class BeemateError(Exception):
    """BeeMate 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_DEVICE_NOT_FOUND = BeemateError(404, "기기를 찾을 수 없습니다.")
_TRANSFER_NOT_FOUND = BeemateError(404, "전송을 찾을 수 없거나 만료되었습니다.")


def events_channel(device_id: int) -> str:
    return f"beemate:events:{device_id}"


def online_key(device_id: int) -> str:
    return f"beemate:online:{device_id}"


def build_object_key(user_id: int, transfer_id: int) -> str:
    return f"beemate/{user_id}/{transfer_id}"


def _ttl() -> timedelta:
    return timedelta(hours=settings.beemate_transfer_ttl_hours)


def expiry_cutoff(now: datetime | None = None) -> datetime:
    """이 시각보다 먼저 만들어진 전송은 만료다."""
    return (now or datetime.now(UTC)) - _ttl()


def to_transfer_response(transfer: BeemateTransfer) -> TransferResponse:
    return TransferResponse(
        id=transfer.id,
        to_device_id=transfer.to_device_id,
        from_device_id=transfer.from_device_id,
        name=transfer.name,
        mime_type=transfer.mime_type,
        size=transfer.size,
        created_at=transfer.created_at,
        expires_at=transfer.created_at + _ttl(),
    )


# --- 기기 -------------------------------------------------------------------


async def get_own_device(
    session: AsyncSession, user: User, device_id: int
) -> BeemateDevice:
    device = await session.get(BeemateDevice, device_id)
    if device is None or device.user_id != user.id:
        raise _DEVICE_NOT_FOUND
    return device


async def _online_flags(redis: Redis, device_ids: list[int]) -> list[bool]:
    if not device_ids:
        return []
    try:
        values = await redis.mget([online_key(i) for i in device_ids])
    except Exception:  # noqa: BLE001 - 온라인 표시는 부가 정보라 Redis 장애로 목록을 막지 않는다
        _log.warning("beemate_online_lookup_failed")
        return [False] * len(device_ids)
    return [v is not None for v in values]


def _device_response(device: BeemateDevice, online: bool) -> DeviceResponse:
    return DeviceResponse(
        id=device.id,
        name=device.name,
        platform=device.platform,
        is_favorite=device.is_favorite,
        is_hidden=device.is_hidden,
        created_at=device.created_at,
        last_seen_at=device.last_seen_at,
        online=online,
    )


def sort_devices(devices: list[DeviceResponse]) -> list[DeviceResponse]:
    """즐겨찾기 → 켜진 기기 → 나머지. 같은 무리 안에서는 등록 순.

    즐겨찾기는 꺼져 있어도 맨 위다 — 사용자가 고른 자리라 접속 상태로 흔들리면 안 된다.
    즐겨찾기끼리는 켜진 것을 앞에 둔다.
    """
    return sorted(
        devices,
        key=lambda d: (not d.is_favorite, not d.online, d.created_at, d.id),
    )


async def list_devices(
    session: AsyncSession, redis: Redis, user: User, *, all_devices: bool = False
) -> list[DeviceResponse]:
    """내 기기. 기본은 **보낼 곳 목록** — 즐겨찾기(꺼져 있어도) + 지금 켜진 기기, 숨김 제외.

    드라이브는 어디서든 접속하므로 한 번 열어 본 PC 가 전부 기기로 남는다. 그것을 다 보여 주면
    고르기 어렵다. 그래서 사용자가 고른 기기(★)와 지금 받을 수 있는 기기만 내놓는다.
    관리 화면은 all_devices 로 전부 본다.
    """
    rows = (
        await session.execute(select(BeemateDevice).where(BeemateDevice.user_id == user.id))
    ).scalars().all()
    flags = await _online_flags(redis, [d.id for d in rows])
    devices = [_device_response(d, f) for d, f in zip(rows, flags, strict=True)]
    if not all_devices:
        devices = [d for d in devices if not d.is_hidden and (d.is_favorite or d.online)]
    return sort_devices(devices)


async def register_device(
    session: AsyncSession, user: User, client_id: str, name: str, platform: str
) -> BeemateDevice:
    """client_id 로 기기를 찾고, 없으면 만든다(upsert). 있으면 이름을 덮어쓰지 않는다.

    사용자가 바꾼 이름을 다음 접속이 브라우저 기본 이름으로 되돌리면 안 되기 때문이다.
    같은 client_id 로 동시에 두 번 붙으면(탭 두 개가 거의 동시에 열림) 한쪽이 유일 인덱스에
    걸리고, 그쪽은 먼저 만들어진 행을 다시 읽어 쓴다.
    """
    found = await _find_by_client(session, user.id, client_id)
    if found is not None:
        return found
    count = await session.scalar(
        select(func.count()).select_from(BeemateDevice).where(BeemateDevice.user_id == user.id)
    )
    if (count or 0) >= settings.beemate_max_devices:
        raise BeemateError(
            409,
            f"기기는 {settings.beemate_max_devices}대까지 등록됩니다. "
            "BeeMate 기기 관리에서 쓰지 않는 기기를 지워 주세요.",
        )
    device = BeemateDevice(user_id=user.id, client_id=client_id, name=name, platform=platform)
    session.add(device)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        found = await _find_by_client(session, user.id, client_id)
        if found is None:
            raise
        return found
    await session.refresh(device)
    return device


async def _find_by_client(
    session: AsyncSession, user_id: int, client_id: str
) -> BeemateDevice | None:
    return (
        await session.execute(
            select(BeemateDevice).where(
                BeemateDevice.user_id == user_id, BeemateDevice.client_id == client_id
            )
        )
    ).scalar_one_or_none()


async def register_device_response(
    session: AsyncSession, redis: Redis, user: User, client_id: str, name: str, platform: str
) -> DeviceResponse:
    device = await register_device(session, user, client_id, name, platform)
    [online] = await _online_flags(redis, [device.id])
    return _device_response(device, online)


async def update_device(
    session: AsyncSession,
    redis: Redis,
    user: User,
    device_id: int,
    *,
    name: str | None = None,
    is_favorite: bool | None = None,
    is_hidden: bool | None = None,
) -> DeviceResponse:
    device = await get_own_device(session, user, device_id)
    if name is not None:
        device.name = name
    if is_favorite is not None:
        device.is_favorite = is_favorite
    if is_hidden is not None:
        device.is_hidden = is_hidden
    await session.commit()
    await session.refresh(device)
    [online] = await _online_flags(redis, [device.id])
    return _device_response(device, online)


async def delete_device(
    session: AsyncSession, storage: StorageService, user: User, device_id: int
) -> None:
    """기기를 지운다. 그 기기로 가던 전송의 오브젝트를 먼저 지우고, 행은 CASCADE 가 지운다.

    오브젝트 삭제가 일부 실패하면 기기를 지우지 않는다 — 지우면 행이 CASCADE 로 사라져
    남은 오브젝트를 아무도 찾을 수 없다.
    """
    device = await get_own_device(session, user, device_id)
    keys = list(
        (
            await session.execute(
                select(BeemateTransfer.object_key).where(
                    BeemateTransfer.to_device_id == device.id
                )
            )
        ).scalars()
    )
    if keys:
        failed = await storage.delete_many_async(keys)
        if failed:
            _log.warning(
                "beemate_device_delete_objects_failed", device_id=device.id, failed=len(failed)
            )
            raise BeemateError(502, "저장소 정리에 실패했습니다. 잠시 후 다시 시도해 주세요.")
    await session.delete(device)
    await session.commit()


async def mark_seen(device_id: int) -> None:
    """스트림 연결 시각을 남긴다. 짧은 세션을 따로 연다(요청 세션은 스트림 전에 놓아준다)."""
    async with SessionFactory() as session:
        device = await session.get(BeemateDevice, device_id)
        if device is not None:
            device.last_seen_at = datetime.now(UTC)
            await session.commit()


# --- 전송 -------------------------------------------------------------------


async def create_transfer(
    session: AsyncSession,
    storage: StorageService,
    user: User,
    upload: UploadFile,
    to_device_id: int,
    from_device_id: int | None,
) -> TransferResponse:
    """보내기. 흐름: 기기 확인 → 크기 확인 → 내 만료분 정리 → 대기 합계 확인 → 행 → 저장 → 발행.

    대기 합계 상한은 동시 업로드끼리 서로를 못 보므로 조금 넘칠 수 있다(소프트 상한). 저장소를
    채우는 것을 막는 용도라 정확할 필요가 없고, 잠금을 걸 만큼의 값어치가 없다.
    """
    to_device = await get_own_device(session, user, to_device_id)
    if from_device_id is not None:
        await get_own_device(session, user, from_device_id)

    size = _upload_size(upload)
    if size > settings.beemate_max_file_bytes:
        limit_mib = settings.beemate_max_file_bytes // (1024 * 1024)
        raise BeemateError(
            413, f"파일이 너무 큽니다({limit_mib} MiB 까지). 큰 파일은 드라이브에 올려 주세요."
        )

    await purge_expired(session, storage, user_id=user.id)

    pending = await session.scalar(
        select(func.coalesce(func.sum(BeemateTransfer.size), 0)).where(
            BeemateTransfer.user_id == user.id
        )
    )
    if int(pending or 0) + size > settings.beemate_max_pending_bytes:
        raise BeemateError(
            413, "아직 받지 않은 파일이 너무 많습니다. 받는 기기에서 BeeMate 를 열어 받아 주세요."
        )

    name = (upload.filename or "untitled").strip() or "untitled"
    name = name.replace("/", "_").replace("\\", "_")[:255]
    mime = (upload.content_type or "application/octet-stream")[:100]
    transfer = BeemateTransfer(
        user_id=user.id,
        to_device_id=to_device.id,
        from_device_id=from_device_id,
        name=name,
        mime_type=mime,
        size=size,
        object_key="",
    )
    session.add(transfer)
    await session.flush()
    transfer.object_key = build_object_key(user.id, transfer.id)

    await upload.seek(0)
    try:
        await storage.put_async(transfer.object_key, upload.file, size, mime)
    except Exception as exc:
        await session.rollback()
        await _safe_delete(storage, transfer.object_key)
        raise BeemateError(502, "오브젝트 스토리지 저장에 실패했습니다.") from exc

    try:
        await session.commit()
    except Exception:
        await session.rollback()
        await _safe_delete(storage, transfer.object_key)
        raise
    await session.refresh(transfer)

    response = to_transfer_response(transfer)
    await publish_transfer(response)
    return response


async def list_pending(
    session: AsyncSession, user: User, device_id: int
) -> list[TransferResponse]:
    device = await get_own_device(session, user, device_id)
    rows = (
        await session.execute(
            select(BeemateTransfer)
            .where(
                BeemateTransfer.to_device_id == device.id,
                BeemateTransfer.created_at > expiry_cutoff(),
            )
            .order_by(BeemateTransfer.created_at, BeemateTransfer.id)
        )
    ).scalars().all()
    return [to_transfer_response(t) for t in rows]


async def get_own_transfer(
    session: AsyncSession, user: User, transfer_id: int
) -> BeemateTransfer:
    transfer = await session.get(BeemateTransfer, transfer_id)
    if (
        transfer is None
        or transfer.user_id != user.id
        or transfer.created_at <= expiry_cutoff()
    ):
        raise _TRANSFER_NOT_FOUND
    return transfer


async def prepare_content(
    session: AsyncSession, storage: StorageService, user: User, transfer_id: int
) -> tuple[str, str, str]:
    """게이트웨이 다운로드 준비 — (internal_redirect, filename, mime)."""
    transfer = await get_own_transfer(session, user, transfer_id)
    presigned = await storage.presign_get_async(transfer.object_key)
    return storage.to_internal_redirect(presigned), transfer.name, transfer.mime_type


async def delete_transfer(
    session: AsyncSession, storage: StorageService, user: User, transfer_id: int
) -> None:
    """받음 확인 = 삭제. 보낸 쪽 취소도 같은 경로다.

    이미 없으면 404 — 멱등하게 다루는 건 클라이언트 몫이다.

    만료 여부는 보지 않는다 — 만료된 것도 지울 수 있어야 정리가 앞당겨진다.
    """
    transfer = await session.get(BeemateTransfer, transfer_id)
    if transfer is None or transfer.user_id != user.id:
        raise _TRANSFER_NOT_FOUND
    try:
        await storage.delete_async(transfer.object_key)
    except Exception as exc:
        raise BeemateError(502, "저장소 정리에 실패했습니다. 잠시 후 다시 시도해 주세요.") from exc
    await session.delete(transfer)
    await session.commit()


async def _safe_delete(storage: StorageService, key: str) -> None:
    try:
        await storage.delete_async(key)
    except Exception:  # noqa: BLE001 - best-effort 정리
        _log.warning("beemate_object_cleanup_failed", key=key)


# --- 만료 정리 ---------------------------------------------------------------


@dataclass
class PurgeResult:
    deleted: int = 0
    bytes_reclaimed: int = 0
    failed: int = 0
    # 오래 안 쓴 기기 정리 몫(purger 회차에서만 채운다).
    devices_removed: int = 0


async def purge_expired(
    session: AsyncSession,
    storage: StorageService,
    *,
    user_id: int | None = None,
    now: datetime | None = None,
) -> PurgeResult:
    """만료 전송을 지운다. user_id 를 주면 그 사용자 몫만(보내기 직전), 없으면 전체(purger).

    오브젝트 삭제에 실패한 키의 행은 남겨 두고 이번 호출에서 다시 고르지 않는다 — 다음 회차가
    다시 시도한다. 그래야 실패 하나가 무한 재선택으로 호출을 붙잡지 않는다.
    """
    cutoff = expiry_cutoff(now)
    result = PurgeResult()
    skip: set[int] = set()
    while True:
        stmt = (
            select(BeemateTransfer.id, BeemateTransfer.object_key, BeemateTransfer.size)
            .where(BeemateTransfer.created_at <= cutoff)
            .order_by(BeemateTransfer.id)
            .limit(_PURGE_BATCH)
        )
        if user_id is not None:
            stmt = stmt.where(BeemateTransfer.user_id == user_id)
        if skip:
            stmt = stmt.where(BeemateTransfer.id.not_in(skip))
        rows = (await session.execute(stmt)).all()
        if not rows:
            break
        failed_keys = set(await storage.delete_many_async([r.object_key for r in rows]))
        done = [r for r in rows if r.object_key not in failed_keys]
        skip.update(r.id for r in rows if r.object_key in failed_keys)
        if done:
            await session.execute(
                delete(BeemateTransfer).where(BeemateTransfer.id.in_([r.id for r in done]))
            )
            await session.commit()
        result.deleted += len(done)
        result.bytes_reclaimed += sum(r.size for r in done)
        result.failed += len(rows) - len(done)
        if len(rows) < _PURGE_BATCH:
            break
    return result


async def purge_stale_devices(
    session: AsyncSession, storage: StorageService, *, now: datetime | None = None
) -> int:
    """오래 안 쓴 기기를 지운다 — 즐겨찾기가 아니고 BEEMATE_DEVICE_STALE_DAYS 동안 접속이 없는 것.

    아무 PC 에서나 한 번 열어 본 브라우저가 관리 목록에 끝없이 쌓이지 않게 한다. 즐겨찾기는
    사용자가 고른 것이라 오래 꺼져 있어도 남긴다. 그 기기로 가던 전송의 오브젝트를 먼저 지우고
    (보통은 TTL 로 이미 없다) 행을 지운다. 오브젝트 삭제가 실패한 기기는 남겨 다음 회차에 맡긴다.
    """
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.beemate_device_stale_days)
    seen = func.coalesce(BeemateDevice.last_seen_at, BeemateDevice.created_at)
    stale = list(
        (
            await session.execute(
                select(BeemateDevice.id).where(
                    BeemateDevice.is_favorite.is_(False), seen < cutoff
                )
            )
        ).scalars()
    )
    if not stale:
        return 0
    rows = (
        await session.execute(
            select(BeemateTransfer.to_device_id, BeemateTransfer.object_key).where(
                BeemateTransfer.to_device_id.in_(stale)
            )
        )
    ).all()
    failed = set(await storage.delete_many_async([r.object_key for r in rows])) if rows else set()
    blocked = {r.to_device_id for r in rows if r.object_key in failed}
    doomed = [i for i in stale if i not in blocked]
    if doomed:
        await session.execute(delete(BeemateDevice).where(BeemateDevice.id.in_(doomed)))
        await session.commit()
    return len(doomed)


# --- 실시간 ------------------------------------------------------------------


# 스트림에 싣는 것은 "가져갈 것이 생겼다"는 신호뿐이다. 내용(파일명·크기)은 싣지 않는다 —
# 받는 쪽은 신호를 받으면 목록을 당겨 간다(pull). 그래야 실시간 경로와 다시 켜졌을 때의 경로가
# 하나로 같고, 신호를 놓쳐도 다음 신호나 재접속 때 빠짐없이 받는다.
PENDING_SIGNAL = json.dumps({"type": "pending"})


async def publish_transfer(transfer: TransferResponse) -> None:
    """받을 기기의 채널로 "새 전송 있음" 신호를 보낸다 (fail-open)."""
    try:
        await redis_client.publish(events_channel(transfer.to_device_id), PENDING_SIGNAL)
    except Exception:  # noqa: BLE001 - 발행 실패가 전송 저장을 되돌리면 안 된다
        _log.warning("beemate_publish_failed", transfer_id=transfer.id)


async def _touch_online(device_id: int) -> None:
    try:
        await redis_client.set(online_key(device_id), "1", ex=ONLINE_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - 온라인 표시는 부가 정보
        pass


async def subscribe_device_events(device_id: int) -> AsyncGenerator[str, None]:
    """기기 하나의 SSE 스트림. 소유 확인은 라우트가 끝냈다.

    구독을 마친 뒤 `: connected` 와 `hello`(이 연결의 기기 id)를 보낸다. 클라이언트는 hello 를
    받은 **뒤에** 밀린 목록을 당겨야 그 사이 전송을 놓치지 않는다(spec/beemate.md 「접속 순서」).
    이후 프레임은 전부 `{"type":"pending"}` 신호다.
    """
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(events_channel(device_id))
    await _touch_online(device_id)
    try:
        yield ": connected\n\n"
        yield f"data: {json.dumps({'type': 'hello', 'device_id': device_id})}\n\n"
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=KEEPALIVE_SECONDS
            )
            await _touch_online(device_id)
            if message is None:
                yield ": ping\n\n"
                continue
            data = message.get("data")
            if isinstance(data, bytes):
                data = data.decode("utf-8", "replace")
            if not isinstance(data, str):
                continue
            try:
                json.loads(data)
            except ValueError:
                continue
            yield f"data: {data}\n\n"
    finally:
        try:
            await redis_client.delete(online_key(device_id))
        except Exception:  # noqa: BLE001
            pass
        try:
            await pubsub.unsubscribe(events_channel(device_id))
            await pubsub.aclose()  # type: ignore[no-untyped-call]
        except Exception:  # noqa: BLE001 - 정리 실패는 무시(연결은 어차피 종료)
            pass
