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
# purge(영구 삭제)만 예외로 휴지통 화면 전용이며 별도 발행 함수를 쓴다(publish_purge_event).
EventType = str  # upload | version | folder | rename | move | delete | restore | permission | purge


async def _normalize_container(parent_folder_id: int | None) -> int | None:
    """이벤트의 parent_folder_id 를 프론트/리스트 API 규약(개인 루트 = null)에 맞춘다.

    개인 드라이브 루트는 실제로는 `parent_folder_id IS NULL` 인 폴더 행이지만, 프론트는 이를
    `null` 로 표현하고 리스트 API 도 `parent_id=null` 로 받는다. 발행부는 컨테이너의 실제 id
    (`get_root_folder().id`)를 실어 보내므로, 그대로면 프론트의 `parent_folder_id === 현재폴더`
    매칭이 `숫자 === null` 로 어긋나 **루트에서 실시간 반영이 되지 않는다**. 컨테이너가 개인
    루트(parent NULL & group NULL)면 null 로 정규화해 이 불일치를 없앤다.

    그룹/공유 루트는 프론트가 실제 폴더 id 를 rootId 로 쓰므로 `group_id is None` 가드로 제외한다
    (정규화하면 오히려 그쪽 매칭이 깨진다). 컨테이너 행은 항상 기존 커밋된 폴더라 별도 세션 조회로
    안전하다. 루트가 아닌 일반 폴더는 그대로 둔다.
    """
    if parent_folder_id is None:
        return None
    async with SessionFactory() as session:
        container = await session.get(File, parent_folder_id)
    if container is not None and container.parent_folder_id is None and container.group_id is None:
        return None
    return parent_folder_id


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
    await _publish(
        {
            "type": type,
            "file_id": file_id,
            "parent_folder_id": await _normalize_container(parent_folder_id),
            "actor_id": actor_id,
            "name": name,
            "ts": ts or datetime.now(UTC).isoformat(),
        }
    )


async def _publish(payload: dict[str, Any]) -> None:
    """페이로드 1건을 채널로 발행한다 (fail-open 공통부)."""
    try:
        await redis_client.publish(FILE_EVENTS_CHANNEL, json.dumps(payload))
    except Exception:  # noqa: BLE001 - fail-open, 발행 실패는 파일 조작을 막지 않는다
        _log.warning(
            "file_event_publish_failed",
            type=payload.get("type"),
            file_id=payload.get("file_id"),
        )


async def publish_purge_event(
    *,
    file_id: int,
    parent_folder_id: int | None,
    actor_id: int | None,
    name: str,
    owner_id: int,
) -> None:
    """영구 삭제 이벤트 (spec/trash-retention-purge.md).

    `publish_file_event` 와 분리한 이유는 **판정 방식이 다르기** 때문이다. 영구 삭제는 files
    행이 사라지므로 구독자 필터가 행을 조회해 권한을 판정할 수 없다(`user_can_receive_event`
    참조). 그래서 페이로드에 `owner_id` 를 실어 자기완결적으로 만들고, 필터는 소유자만
    통과시킨다 — 소프트 삭제 항목은 어떤 목록에도 노출되지 않으므로 화면이 바뀌는 사람은
    삭제 루트의 소유자뿐이다.

    `parent_folder_id` 는 `_normalize_container` 로 정규화하지 않는다. 이 이벤트의 소비자는
    휴지통 화면이라 컨테이너 매칭을 쓰지 않고, 배치 정리에서 항목마다 DB 를 한 번 더 잡는
    비용이 무의미하다. 파일 브라우저는 `type === "purge"` 를 무시한다.

    actor_id 는 사용자의 수동 영구 삭제면 그 사용자, 자동 정리면 None(행위자 없음)이다.
    """
    await _publish(
        {
            "type": "purge",
            "file_id": file_id,
            "parent_folder_id": parent_folder_id,
            "actor_id": actor_id,
            "name": name,
            "owner_id": owner_id,
            "ts": datetime.now(UTC).isoformat(),
        }
    )


async def publish_purge_summary(*, owner_id: int, purged: int) -> None:
    """소유자별 영구 삭제 요약 (이벤트 폭주 상한 초과분).

    개별 이벤트 대신 "N건이 정리됐다"만 알린다. `file_id` 가 없으므로 프론트는 행 제거 대신
    목록 전체를 재조회한다.
    """
    await _publish(
        {
            "type": "purge",
            "file_id": None,
            "parent_folder_id": None,
            "actor_id": None,
            "name": None,
            "owner_id": owner_id,
            "purged": purged,
            "ts": datetime.now(UTC).isoformat(),
        }
    )


async def user_can_receive_event(user: User, event: dict[str, Any]) -> bool:
    """구독자 권한 필터 — 이벤트 대상 파일에 user 가 read 이상 접근 가능한지 (Phase 8-1).

    소유자는 즉시 통과. 그 외에는 그룹 권한(조회 시 판정, 사용자 단위 Redis 캐시 재사용)이
    존재하면(read 이상) 통과한다. 파일 행이 없으면 전달하지 않는다. 소프트 삭제는 행이 남으므로
    정상 판정된다. 판정은 요청과 무관한 짧은 세션으로 수행한다.

    예외는 `purge`(영구 삭제)다 — 행이 이미 없어 조회로 판정할 수 없으므로 페이로드의
    `owner_id` 로만 판정한다(`publish_purge_event` 참조). 요약 이벤트는 `file_id` 가 없어
    이 분기가 아래 타입 가드보다 **먼저** 와야 한다.
    """
    if event.get("type") == "purge":
        owner_id = event.get("owner_id")
        return isinstance(owner_id, int) and owner_id == user.id
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
