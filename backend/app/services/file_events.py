"""파일 변경 실시간 이벤트 (Phase 8-1, spec/drive-ux-phase8.md).

파일 뮤테이션(업로드·버전·폴더 생성·이름 변경·삭제·복원·권한 부여/회수)을 Redis pub/sub
채널 하나(`file-events`)로 발행하고, `GET /api/files/events` SSE 구독 루프가 **구독자별
권한 필터**를 적용해 read 이상 접근 가능한 이벤트만 전달한다.

설계 원칙:
  - 발행은 fail-open — Redis 장애로 발행이 실패해도 파일 조작 자체는 막지 않는다(경고만).
  - 권한 필터가 유출 방지의 핵심이다. 필터가 없으면 타인 파일의 존재/이름이 메타로 샌다.
    소프트 삭제는 files 행이 남으므로 delete 이벤트도 권한 판정이 가능하다.
  - 구독 루프는 async generator 로 구현해 uvicorn 워커를 막지 않는다. 권한 판정은 요청
    세션을 장시간 붙들지 않도록 이벤트마다 짧은 세션을 열어 수행한다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from app.core.database import SessionFactory
from app.core.logging import get_logger
from app.core.redis import redis_client
from app.models import File, User
from app.services import permissions as permissions_service

_log = get_logger("app.file_events")

# 단일 pub/sub 채널 — 프로세스/워커 수와 무관하게 동작한다.
FILE_EVENTS_CHANNEL = "file-events"

# 유휴 시 keepalive 주석(ping) 전송 주기(초). 프록시/브라우저 타임아웃으로 인한 끊김 방지.
KEEPALIVE_SECONDS = 30.0

# 이벤트 타입 — 짧은 문자열. 프론트가 parent_folder_id 기준으로 현재 폴더 갱신을 판단한다.
EventType = str  # upload | version | folder | rename | move | delete | restore | permission


async def publish_file_event(
    *,
    type: EventType,
    file_id: int,
    parent_folder_id: int | None,
    actor_id: int,
    name: str,
    ts: str | None = None,
) -> None:
    """파일 변경 이벤트를 `file-events` 채널로 발행한다 (fail-open).

    Redis 장애 시 경고만 남기고 조용히 넘어간다 — 발행 실패가 파일 조작을 되돌리면 안 된다.
    """
    payload: dict[str, Any] = {
        "type": type,
        "file_id": file_id,
        "parent_folder_id": parent_folder_id,
        "actor_id": actor_id,
        "name": name,
        "ts": ts or datetime.now(UTC).isoformat(),
    }
    try:
        await redis_client.publish(FILE_EVENTS_CHANNEL, json.dumps(payload))
    except Exception:  # noqa: BLE001 - fail-open, 발행 실패는 파일 조작을 막지 않는다
        _log.warning("file_event_publish_failed", type=type, file_id=file_id)


async def user_can_receive_event(user: User, event: dict[str, Any]) -> bool:
    """구독자 권한 필터 — 이벤트 대상 파일에 user 가 read 이상 접근 가능한지 (Phase 8-1).

    소유자는 즉시 통과. 그 외에는 그룹 권한(조회 시 판정, 사용자 단위 Redis 캐시 재사용)이
    존재하면(read 이상) 통과한다. 파일 행이 없으면(영구 삭제 등) 전달하지 않는다. 소프트
    삭제는 행이 남으므로 정상 판정된다. 판정은 요청과 무관한 짧은 세션으로 수행한다.
    """
    file_id = event.get("file_id")
    if not isinstance(file_id, int):
        return False
    async with SessionFactory() as session:
        file = await session.get(File, file_id)
        if file is None:
            return False
        if file.user_id == user.id:
            return True
        level = await permissions_service.get_access_level(session, user, file)
        return level is not None


async def subscribe_file_events(user: User) -> AsyncGenerator[str, None]:
    """SSE 이벤트 스트림 생성기 — 구독자 권한 필터 + 30초 keepalive (Phase 8-1).

    Redis pub/sub 를 구독하고, 수신 이벤트마다 권한 필터를 통과한 것만 `data:` 프레임으로
    내보낸다. 메시지가 없으면 KEEPALIVE_SECONDS 마다 주석 프레임(`: ping`)을 보내 연결을
    유지한다. 클라이언트 종료/취소 시 finally 에서 pub/sub 연결을 정리한다.
    """
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(FILE_EVENTS_CHANNEL)
    try:
        # 연결 직후 초기 주석 — 프론트가 스트림 오픈을 즉시 확인하게 한다.
        yield ": connected\n\n"
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=KEEPALIVE_SECONDS
            )
            if message is None:
                # 타임아웃 = 유휴. keepalive 주석 전송.
                yield ": ping\n\n"
                continue
            data = message.get("data")
            if data is None:
                continue
            # decode_responses=True 이므로 str 로 온다(방어적으로 bytes 도 처리).
            if isinstance(data, bytes):
                data = data.decode("utf-8", "replace")
            try:
                event = json.loads(data)
            except (ValueError, TypeError):
                continue
            if await user_can_receive_event(user, event):
                yield f"data: {json.dumps(event)}\n\n"
    finally:
        try:
            await pubsub.unsubscribe(FILE_EVENTS_CHANNEL)
            await pubsub.aclose()  # type: ignore[no-untyped-call]
        except Exception:  # noqa: BLE001 - 정리 실패는 무시(연결은 어차피 종료)
            pass
