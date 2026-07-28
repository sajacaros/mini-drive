"""인덱싱 작업 큐 (spec/wiki-index.md).

Redis 정렬 집합(ZSET) 하나로 큐·디바운스·합치기를 동시에 얻는다.

  member = file_id, score = 처리 가능 시각(epoch)

- **합치기(coalesce)**: 같은 파일을 다시 넣으면 ZADD 가 점수만 갱신한다. 큐에 중복이 쌓이지
  않으므로 큰 파일을 짧은 간격으로 여러 번 올려도 인덱싱은 한 번만 돈다.
- **디바운스**: 점수를 `now + debounce` 로 두면 워커가 그 시각 전에는 집지 않는다. 껐다 켜는
  조작이나 연속 버전업이 흡수된다.
- 작업 내용은 file_id 뿐이다. 워커가 처리 시점에 현재 상태를 다시 읽으므로, 큐에 담긴 정보가
  낡아서 생기는 문제가 없다.
"""

from __future__ import annotations

import time

from app.core.logging import get_logger
from app.core.redis import redis_client

log = get_logger(__name__)

QUEUE_KEY = "wiki:index:queue"
# 토글·버전업 직후 바로 돌리지 않고 잠시 기다린다. 연속 조작을 한 번으로 합치기 위한 값이라
# 길 필요는 없다.
DEBOUNCE_SECONDS = 10


async def enqueue(file_ids: list[int] | int, *, delay: int | None = None) -> int:
    """인덱싱 대기열에 넣는다(이미 있으면 시각만 갱신). 반환: 요청한 개수.

    Redis 장애가 인덱싱을 못 하게 만들 뿐 파일 조작 자체를 막아서는 안 되므로 fail-open 이다 —
    실패는 로그로만 남기고, 놓친 항목은 다음 토글·버전업에서 다시 들어온다.
    """
    ids = [file_ids] if isinstance(file_ids, int) else list(file_ids)
    if not ids:
        return 0
    due = time.time() + (DEBOUNCE_SECONDS if delay is None else delay)
    try:
        await redis_client.zadd(QUEUE_KEY, {str(fid): due for fid in ids})
    except Exception:  # noqa: BLE001 - fail-open
        log.warning("wiki_enqueue_failed", count=len(ids))
    return len(ids)


async def claim(limit: int = 5) -> list[int]:
    """처리 시각이 된 작업을 가져오고 큐에서 뺀다.

    가져온 뒤 지우므로, 처리 중 크래시하면 그 항목은 유실된다. 대신 워커가 상태를 DB 에
    남기므로(status=indexing) 재기동 시 복구 대상을 찾을 수 있고, 최악의 경우에도 다음
    버전업·토글에서 다시 들어온다. 인덱싱은 멱등이라 중복 실행도 안전하다.
    """
    now = time.time()
    try:
        members = await redis_client.zrangebyscore(
            QUEUE_KEY, min="-inf", max=now, start=0, num=limit
        )
        if not members:
            return []
        await redis_client.zrem(QUEUE_KEY, *members)
    except Exception:  # noqa: BLE001 - fail-open
        log.warning("wiki_claim_failed")
        return []

    out: list[int] = []
    for m in members:
        try:
            out.append(int(m))
        except (TypeError, ValueError):
            continue
    return out


async def pending_count() -> int:
    try:
        return int(await redis_client.zcard(QUEUE_KEY))
    except Exception:  # noqa: BLE001
        return 0


async def drop(file_ids: list[int] | int) -> None:
    """대기 중인 작업을 취소한다 (위키를 끄거나 파일이 삭제될 때)."""
    ids = [file_ids] if isinstance(file_ids, int) else list(file_ids)
    if not ids:
        return
    try:
        await redis_client.zrem(QUEUE_KEY, *[str(fid) for fid in ids])
    except Exception:  # noqa: BLE001
        log.warning("wiki_drop_failed", count=len(ids))


__all__ = ["DEBOUNCE_SECONDS", "QUEUE_KEY", "claim", "drop", "enqueue", "pending_count"]
