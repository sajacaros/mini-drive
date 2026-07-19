"""최근 이용 항목 서비스 (Phase 8-3, spec/drive-ux-phase8.md).

미리보기·다운로드 티켓 발급 성공 시 file_recents 를 upsert 하고, 사용자당 최신 100개만
유지한다(초과분 삭제). 기록은 fail-open — 실패해도 미리보기/다운로드 흐름을 막지 않는다.
공개 공유(무인증) 경로는 기록하지 않는다(라우트에서 호출하지 않음).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import File, FileRecent, User
from app.services import permissions as permissions_service

_log = get_logger("app.recents")

# 사용자당 최근 항목 보존 상한 (PRD Phase 8-3).
MAX_RECENTS_PER_USER = 100

# 최근 조회 기본/최대 개수.
DEFAULT_RECENT_LIMIT = 20
MAX_RECENT_LIMIT = 50

# 상한 초과분 삭제 — 최신 MAX_RECENTS_PER_USER 개를 제외한 오래된 행을 지운다.
_PRUNE_SQL = text(
    """
    DELETE FROM file_recents
    WHERE user_id = :uid AND file_id NOT IN (
        SELECT file_id FROM file_recents
        WHERE user_id = :uid
        ORDER BY last_accessed_at DESC
        LIMIT :keep
    )
    """
)


async def record_recent(session: AsyncSession, user_id: int, file_id: int) -> None:
    """최근 이용 upsert + 사용자당 100개 상한 유지 (fail-open).

    ON CONFLICT DO UPDATE 로 last_accessed_at 을 갱신하고, 초과분을 삭제한다. 실패 시
    롤백 후 경고만 남긴다 — 미리보기/다운로드는 이미 성공한 상태이므로 되돌리지 않는다.
    """
    now = datetime.now(UTC)
    try:
        stmt = (
            pg_insert(FileRecent)
            .values(user_id=user_id, file_id=file_id, last_accessed_at=now)
            .on_conflict_do_update(
                index_elements=["user_id", "file_id"],
                set_={"last_accessed_at": now},
            )
        )
        await session.execute(stmt)
        await session.execute(
            _PRUNE_SQL, {"uid": user_id, "keep": MAX_RECENTS_PER_USER}
        )
        await session.commit()
    except Exception:  # noqa: BLE001 - fail-open, 기록 실패는 흐름을 막지 않는다
        await session.rollback()
        _log.warning("recent_record_failed", user_id=user_id, file_id=file_id)


async def list_recent(
    session: AsyncSession, user: User, limit: int
) -> list[File]:
    """최근 이용 항목 (최신순). 삭제·접근 불가 파일은 제외한다.

    저장 상한이 100개라 활성 최근 파일 전체를 최신순으로 가져와 접근 필터 후 limit 개를 취한다
    (접근 필터를 SQL 뒤에 두면 페이지가 비므로). 접근 판정은 소유자 즉시 통과 + 그룹 권한 캐시.
    """
    limit = max(1, min(limit, MAX_RECENT_LIMIT))
    rows = (
        await session.execute(
            select(File, FileRecent.last_accessed_at)
            .join(FileRecent, FileRecent.file_id == File.id)
            .where(
                FileRecent.user_id == user.id,
                File.is_deleted.is_(False),
            )
            .order_by(FileRecent.last_accessed_at.desc())
        )
    ).all()

    visible: list[File] = []
    for file, _accessed in rows:
        if file.user_id == user.id:
            visible.append(file)
        else:
            level = await permissions_service.get_access_level(session, user, file)
            if level is not None:
                visible.append(file)
        if len(visible) >= limit:
            break
    return visible
