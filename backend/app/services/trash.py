"""휴지통 보존 기간 자동 정리 (spec/trash-retention-purge.md).

`soft_delete` 가 남긴 `deleted_at` 이 보존 기간을 넘긴 항목을 영구 삭제한다. 실제 삭제는
`files.purge_tree` 에 위임해 사용자의 수동 영구 삭제와 같은 코드를 타게 한다 — 소유자별
할당량 회수·공유 링크 선삭제·썸네일 제거가 두 경로에서 어긋나지 않아야 한다.

설계 원칙:
  - **삭제 루트만 고른다.** 부모가 살아 있거나 없는 삭제 항목이 삭제 루트이고(list_trash 와
    같은 판정), 하위는 purge_tree 의 subtree CTE 가 함께 지운다.
  - **배치 상한은 트랜잭션 크기 제한이지 하루 처리량 제한이 아니다.** 한 회차 안에서 배치가
    상한보다 적게 돌아올 때까지 반복한다 — 그렇지 않으면 대량 유입 시 회수가 며칠씩 밀린다.
  - **한 항목의 실패가 회차를 멈추지 않고, 무한 재선택도 되지 않는다.** 실패한 id 를 모아
    선택에서 제외해 진행을 보장한다.
  - **중복 실행 방지는 Redis 리스**로 한다. 같은 항목을 두 프로세스가 집으면 양쪽이
    `_release_quota` 를 호출해 할당량이 이중 차감된다(파일은 안전하지만 사용량이 어긋난다).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import observe_trash_purged
from app.core.redis import redis_client
from app.models import File
from app.services import file_events as file_events_service
from app.services.files import purge_tree, subtree_stats
from app.services.storage import StorageService

_log = get_logger("app.trash")

# 실행 시각 기준 시간대 — 서비스 전체 규약(todos/archives 와 동일).
_KST = ZoneInfo("Asia/Seoul")

_LOCK_KEY = "trash:purge:lock"
# 리스 TTL. 대량 정리가 이보다 오래 걸릴 수 있어 배치마다 갱신한다.
_LOCK_TTL_SECONDS = 900


def next_run_at(now: datetime, hour: int | None = None) -> datetime:
    """다음 실행 시각(KST). `now` 가 정각이면 하루 뒤로 넘긴다.

    간격 기반 sleep 이 아니라 벽시계 목표를 쓰는 이유는, 컨테이너 재시작 시각에 실행 시각이
    끌려다니면 되돌릴 수 없는 삭제가 업무 시간에 돌 수 있기 때문이다.
    """
    h = settings.trash_purge_hour if hour is None else hour
    local = now.astimezone(_KST)
    target = local.replace(hour=h, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=1)
    return target


@dataclass
class PurgeResult:
    """정리 1회차 결과. dry_run 이면 아무것도 지우지 않고 규모만 센다."""

    roots: int = 0
    rows: int = 0
    bytes_reclaimed: int = 0
    failed: int = 0
    dry_run: bool = False
    locked_out: bool = False
    disabled: bool = False


def _cutoff(retention_days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=retention_days)


async def _expired_roots(
    session: AsyncSession,
    cutoff: datetime,
    limit: int | None,
    exclude: set[int],
) -> list[File]:
    """기한을 넘긴 삭제 루트를 오래된 것부터 고른다 (list_trash 와 같은 루트 판정)."""
    parent = File.__table__.alias("p")
    stmt = (
        select(File)
        .outerjoin(parent, File.parent_folder_id == parent.c.id)
        .where(
            File.is_deleted.is_(True),
            File.deleted_at.is_not(None),
            File.deleted_at < cutoff,
            (parent.c.id.is_(None)) | (parent.c.is_deleted.is_(False)),
        )
        .order_by(File.deleted_at)
    )
    if exclude:
        stmt = stmt.where(File.id.not_in(exclude))
    if limit is not None:
        stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def _acquire_lock(token: str) -> bool:
    """리스를 잡는다. 못 잡으면 다른 프로세스가 정리 중이므로 이번 회차를 건너뛴다."""
    try:
        return bool(await redis_client.set(_LOCK_KEY, token, nx=True, ex=_LOCK_TTL_SECONDS))
    except Exception:  # noqa: BLE001 - Redis 장애 시 정리를 강행하지 않는다(안전측 실패).
        _log.warning("trash_purge_lock_unavailable")
        return False


async def _extend_lock(token: str) -> None:
    """내가 쥔 리스만 연장한다(다른 소유자의 리스를 밀지 않는다)."""
    try:
        if await redis_client.get(_LOCK_KEY) == token:
            await redis_client.expire(_LOCK_KEY, _LOCK_TTL_SECONDS)
    except Exception:  # noqa: BLE001 - 연장 실패는 치명적이지 않다(TTL 만료 후 다음 회차).
        _log.warning("trash_purge_lock_extend_failed")


async def _release_lock(token: str) -> None:
    try:
        if await redis_client.get(_LOCK_KEY) == token:
            await redis_client.delete(_LOCK_KEY)
    except Exception:  # noqa: BLE001 - 놓쳐도 TTL 로 풀린다.
        _log.warning("trash_purge_lock_release_failed")


async def _dry_run(session: AsyncSession, cutoff: datetime) -> PurgeResult:
    result = PurgeResult(dry_run=True)
    for root in await _expired_roots(session, cutoff, limit=None, exclude=set()):
        rows, size = await subtree_stats(session, root.id)
        result.roots += 1
        result.rows += rows
        result.bytes_reclaimed += size
        _log.info(
            "trash_purge_candidate",
            file_id=root.id,
            name=root.name,
            owner_id=root.user_id,
            is_folder=root.is_folder,
            rows=rows,
            bytes=size,
            deleted_at=root.deleted_at.isoformat() if root.deleted_at else None,
        )
    return result


async def purge_expired(
    session: AsyncSession, storage: StorageService, *, dry_run: bool = False
) -> PurgeResult:
    """보존 기간을 넘긴 휴지통 항목을 정리한다. 반환값으로 회차 결과를 보고한다."""
    retention = settings.trash_retention_days
    if retention <= 0:
        return PurgeResult(disabled=True, dry_run=dry_run)

    cutoff = _cutoff(retention)
    if dry_run:
        return await _dry_run(session, cutoff)

    token = uuid.uuid4().hex
    if not await _acquire_lock(token):
        _log.info("trash_purge_skipped_locked")
        return PurgeResult(locked_out=True)

    result = PurgeResult()
    failed: set[int] = set()
    published = 0
    summary: dict[int, int] = {}
    try:
        while True:
            roots = await _expired_roots(session, cutoff, settings.trash_purge_batch, failed)
            if not roots:
                break
            for root in roots:
                owner_id, file_id, name = root.user_id, root.id, root.name
                deleted_at = root.deleted_at
                publish = published < settings.trash_purge_event_cap
                try:
                    rows, size = await purge_tree(
                        session, storage, root, actor_id=None, publish=publish
                    )
                except Exception:  # noqa: BLE001 - 한 항목의 실패가 회차를 멈추지 않게 한다.
                    await session.rollback()
                    failed.add(file_id)
                    result.failed += 1
                    _log.exception("trash_purge_item_failed", file_id=file_id, name=name)
                    continue
                if publish:
                    published += 1
                else:
                    summary[owner_id] = summary.get(owner_id, 0) + 1
                result.roots += 1
                result.rows += rows
                result.bytes_reclaimed += size
                _log.info(
                    "trash_purged_item",
                    file_id=file_id,
                    name=name,
                    owner_id=owner_id,
                    rows=rows,
                    bytes=size,
                    deleted_at=deleted_at.isoformat() if deleted_at else None,
                )
            await _extend_lock(token)
            # 상한보다 적게 돌아왔다 = 남은 대상이 없다. 상한만큼 왔으면 이어서 비운다.
            if len(roots) < settings.trash_purge_batch:
                break
        # 폭주 상한을 넘긴 몫은 소유자별 요약 1건으로 접는다.
        for owner_id, n in summary.items():
            await file_events_service.publish_purge_summary(owner_id=owner_id, purged=n)
    finally:
        await _release_lock(token)

    observe_trash_purged(result.rows, result.bytes_reclaimed)
    return result
