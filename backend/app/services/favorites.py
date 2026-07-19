"""즐겨찾기 서비스 (Phase 8-2, spec/drive-ux-phase8.md).

즐겨찾기는 (user_id, file_id) 멱등 토글이다. 등록은 read 이상 접근 가능한 파일만 가능하고,
해제는 항상 허용한다(접근권을 잃은 파일도 정리할 수 있어야 한다). 목록/단건 응답의
`is_favorite` 파생 필드는 EXISTS/IN 서브쿼리로 채워 N+1 을 피한다.
"""

from __future__ import annotations

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import File, FileFavorite, User
from app.services import files as files_service
from app.services import permissions as permissions_service


async def add_favorite(session: AsyncSession, user: User, file_id: int) -> None:
    """즐겨찾기 등록 (멱등). read 이상 접근 불가 시 404(존재 은닉 — ensure_file_access 관례)."""
    await files_service.ensure_file_access(
        session, user, await files_service.get_file(session, file_id)
    )
    stmt = (
        pg_insert(FileFavorite)
        .values(user_id=user.id, file_id=file_id)
        .on_conflict_do_nothing(index_elements=["user_id", "file_id"])
    )
    await session.execute(stmt)
    await session.commit()


async def remove_favorite(session: AsyncSession, user: User, file_id: int) -> None:
    """즐겨찾기 해제 (멱등). 접근권과 무관하게 항상 허용 — 없는 행이면 no-op."""
    await session.execute(
        delete(FileFavorite).where(
            FileFavorite.user_id == user.id, FileFavorite.file_id == file_id
        )
    )
    await session.commit()


async def is_favorite(session: AsyncSession, user_id: int, file_id: int) -> bool:
    """단건 즐겨찾기 여부 (EXISTS 서브쿼리). 단건 메타 응답의 파생 필드용."""
    return bool(
        await session.scalar(
            select(
                exists().where(
                    FileFavorite.user_id == user_id,
                    FileFavorite.file_id == file_id,
                )
            )
        )
    )


async def favorite_ids(
    session: AsyncSession, user_id: int, file_ids: list[int]
) -> set[int]:
    """주어진 file_ids 중 즐겨찾기된 id 집합 (단일 IN 쿼리, N+1 금지). 목록 응답용."""
    if not file_ids:
        return set()
    rows = (
        await session.execute(
            select(FileFavorite.file_id).where(
                FileFavorite.user_id == user_id,
                FileFavorite.file_id.in_(file_ids),
            )
        )
    ).scalars().all()
    return set(rows)


async def annotate_is_favorite(
    session: AsyncSession, user: User, files: list[File]
) -> None:
    """파일 목록에 is_favorite 를 in-place 로 부착한다(단일 IN 쿼리). model_validate 가 읽는다."""
    fav = await favorite_ids(session, user.id, [f.id for f in files])
    for f in files:
        f.is_favorite = f.id in fav  # type: ignore[attr-defined]


async def list_favorites(
    session: AsyncSession, user: User, page: int, size: int
) -> tuple[list[File], int]:
    """내 즐겨찾기 목록 (최신 등록순). 삭제·접근권 상실 파일은 숨긴다.

    숨김 판정은 조회 시점에 get_access_level 로 재검증한다(권한 복구 시 다시 보임). 접근 필터가
    페이지네이션보다 뒤에 오면 페이지가 비는 문제가 있어, 활성(미삭제) 즐겨찾기 전체를 최신순으로
    가져와 접근 필터 후 페이지를 슬라이스한다. 즐겨찾기는 사용자가 수동 등록하므로 규모가 작다.
    """
    rows = (
        await session.execute(
            select(File)
            .join(FileFavorite, FileFavorite.file_id == File.id)
            .where(
                FileFavorite.user_id == user.id,
                File.is_deleted.is_(False),
            )
            .order_by(FileFavorite.created_at.desc())
        )
    ).scalars().all()

    visible: list[File] = []
    for f in rows:
        if f.user_id == user.id:
            visible.append(f)
            continue
        level = await permissions_service.get_access_level(session, user, f)
        if level is not None:
            visible.append(f)

    total = len(visible)
    offset = (page - 1) * size
    page_items = visible[offset : offset + size]
    # 정의상 모든 항목이 즐겨찾기이므로 파생 필드를 True 로 부착한다.
    for f in page_items:
        f.is_favorite = True  # type: ignore[attr-defined]
    return page_items, total
