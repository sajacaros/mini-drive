"""사용자 프로비저닝 로직 — 루트 폴더 생성, 이메일 조회.

가입(가입 코드 검증 통과) 및 admin 생성 시점에 사용자별 루트 폴더 행을 생성한다
(files: is_folder=TRUE, parent_folder_id=NULL, name='root') — PRD 5.2 루트 폴더 규약.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import File, User
from app.models.enums import UserStatus

# 루트 폴더 행 규약 (PRD 5.2). parent_folder_id=NULL 은 루트 행에만 허용된다.
ROOT_FOLDER_NAME = "root"


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_active_user_by_email(session: AsyncSession, email: str) -> User | None:
    """정확히 일치하는 active 사용자 1명 조회 (그룹 초대 UX용 이메일 조회).

    부분 검색/목록은 열거 방지를 위해 지원하지 않는다 — 정확 일치 + active 만 반환.
    """
    result = await session.execute(
        select(User).where(User.email == email, User.status == UserStatus.ACTIVE)
    )
    return result.scalar_one_or_none()


async def search_active_users(
    session: AsyncSession,
    query: str,
    *,
    limit: int = 20,
    exclude_user_id: int | None = None,
) -> list[User]:
    """이름(display_name) 또는 이메일 부분 일치 active 사용자 검색 (그룹 초대 UX용).

    최소 검색어 길이는 라우트에서 강제한다. 이메일 열거를 제한하기 위해 결과에 상한(limit)을
    두고, 표시명 오름차순으로 정렬한다. LIKE 와일드카드(%, _)는 이스케이프해 리터럴로 취급한다.
    """
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{escaped}%"
    stmt = (
        select(User)
        .where(
            User.status == UserStatus.ACTIVE,
            or_(
                User.display_name.ilike(like, escape="\\"),
                User.email.ilike(like, escape="\\"),
            ),
        )
        .order_by(User.display_name.asc())
        .limit(limit)
    )
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def update_display_name(
    session: AsyncSession, user: User, display_name: str
) -> User:
    """현재 사용자의 표시 이름을 변경한다(프로필 편집). 호출자 트랜잭션에서 커밋한다."""
    user.display_name = display_name.strip()
    await session.commit()
    await session.refresh(user)
    return user


async def has_root_folder(session: AsyncSession, user_id: int) -> bool:
    result = await session.execute(
        select(File.id).where(
            File.user_id == user_id,
            File.parent_folder_id.is_(None),
            File.is_folder.is_(True),
        )
    )
    return result.first() is not None


async def create_root_folder(session: AsyncSession, user: User) -> File:
    """사용자 루트 폴더 행을 생성한다. 이미 있으면 기존 행을 반환한다.

    호출자가 commit 한다 (승인/부트스트랩 트랜잭션에 포함).
    """
    result = await session.execute(
        select(File).where(
            File.user_id == user.id,
            File.parent_folder_id.is_(None),
            File.is_folder.is_(True),
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    root = File(
        user_id=user.id,
        group_id=None,
        parent_folder_id=None,
        name=ROOT_FOLDER_NAME,
        file_key="",  # 폴더는 오브젝트 스토리지 키가 없다.
        mime_type=None,
        size=0,
        is_folder=True,
    )
    session.add(root)
    await session.flush()
    return root
