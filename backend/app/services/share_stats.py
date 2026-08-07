"""공유 링크 접근 통계 (PRD 3.4 — 조회 수, 마지막 접근 시각).

models 를 변경하지 않기 위해(shares 테이블에 컬럼을 추가하지 않고) 집계를 **Redis** 에 둔다:

  - `share:views:{share_id}`       조회(공개 메타/미리보기/다운로드) 누적 카운터 (INCR)
  - `share:last_access:{share_id}` 마지막 접근 시각 ISO-8601 문자열 (SET)

`download_count` 는 다운로드 횟수 제한과 직결되어 정확성이 필요하므로 그대로 DB(shares)에 남기고,
여기 view_count 는 "얼마나 열람됐나"의 근사 집계다.

주의(정확도): Redis 데이터가 소실되면(재시작/flush/eviction) view_count·last_access 통계는
리셋된다. 다운로드 횟수 제한(download_count)과 달리 이 통계는 유실을 허용하는 근사치다. 정확한
영구 통계가 필요해지면 Phase 6 에서 별도 테이블(예: share_access_logs)로 이전한다.

모든 기록/조회는 best-effort — Redis 오류 시 통계 갱신을 건너뛰고 요청 흐름을 막지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from redis.asyncio import Redis

_VIEWS_PREFIX = "share:views:"
_LAST_ACCESS_PREFIX = "share:last_access:"


def _views_key(share_id: int) -> str:
    return f"{_VIEWS_PREFIX}{share_id}"


def _last_access_key(share_id: int) -> str:
    return f"{_LAST_ACCESS_PREFIX}{share_id}"


@dataclass(frozen=True)
class ShareStats:
    """공유 링크 접근 통계 스냅샷. download_count 는 호출자가 DB 값을 채운다."""

    view_count: int
    last_access_at: datetime | None


async def record_access(redis: Redis, share_id: int) -> None:
    """공개 접근 1건을 기록한다 — 조회 수 +1, 마지막 접근 시각 갱신 (best-effort)."""
    try:
        now = datetime.now(UTC).isoformat()
        pipe = redis.pipeline(transaction=False)
        pipe.incr(_views_key(share_id))
        pipe.set(_last_access_key(share_id), now)
        await pipe.execute()
    except Exception:  # noqa: BLE001 - 통계는 best-effort, 요청을 막지 않는다.
        pass


def _as_text(raw: object) -> str | None:
    """Redis 원문을 문자열로 되돌린다. decode_responses 설정에 따라 str/bytes 가 섞여 온다."""
    if isinstance(raw, bytes):
        return raw.decode(errors="replace")
    return raw if isinstance(raw, str) else None


def _parse_views(raw: object) -> int:
    text = _as_text(raw)
    if text is None:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def _parse_last_access(raw: object) -> datetime | None:
    text = _as_text(raw)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


async def get_stats(redis: Redis, share_id: int) -> ShareStats:
    """단일 공유의 조회 수/마지막 접근 시각을 읽는다 (Redis 오류 시 0/None)."""
    try:
        views_raw, last_raw = await redis.mget(
            _views_key(share_id), _last_access_key(share_id)
        )
    except Exception:  # noqa: BLE001
        return ShareStats(view_count=0, last_access_at=None)
    return ShareStats(
        view_count=_parse_views(views_raw),
        last_access_at=_parse_last_access(last_raw),
    )


async def get_stats_bulk(
    redis: Redis, share_ids: list[int]
) -> dict[int, ShareStats]:
    """여러 공유의 통계를 한 번의 MGET 으로 읽는다(목록 응답용). Redis 오류 시 전부 0/None."""
    if not share_ids:
        return {}
    keys: list[str] = []
    for sid in share_ids:
        keys.append(_views_key(sid))
        keys.append(_last_access_key(sid))
    try:
        values = await redis.mget(*keys)
    except Exception:  # noqa: BLE001
        return {sid: ShareStats(view_count=0, last_access_at=None) for sid in share_ids}

    result: dict[int, ShareStats] = {}
    for idx, sid in enumerate(share_ids):
        views_raw = values[idx * 2]
        last_raw = values[idx * 2 + 1]
        result[sid] = ShareStats(
            view_count=_parse_views(views_raw),
            last_access_at=_parse_last_access(last_raw),
        )
    return result
