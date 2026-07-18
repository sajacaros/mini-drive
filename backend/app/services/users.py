"""사용자 프로비저닝 로직 — 루트 폴더 생성, admin 부트스트랩, 상태 전이 규칙.

가입 승인 시점(및 admin 부트스트랩)에 사용자별 루트 폴더 행을 생성한다
(files: is_folder=TRUE, parent_folder_id=NULL, name='root') — PRD 5.2 루트 폴더 규약.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import File, User
from app.models.enums import UserRole, UserStatus

# 루트 폴더 행 규약 (PRD 5.2). parent_folder_id=NULL 은 루트 행에만 허용된다.
ROOT_FOLDER_NAME = "root"


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


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


async def ensure_admin_bootstrap(
    session: AsyncSession, email: str, password: str
) -> User | None:
    """ADMIN_EMAIL 계정이 없으면 active/admin 으로 생성하고 루트 폴더를 만든다.

    생성한 경우 User 를, 이미 존재하면 None 을 반환한다 (PRD 3.6.2).
    """
    existing = await get_user_by_email(session, email)
    if existing is not None:
        return None

    admin = User(
        email=email,
        password_hash=hash_password(password),
        display_name="Administrator",
        role=UserRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    session.add(admin)
    await session.flush()
    await create_root_folder(session, admin)
    await session.commit()
    return admin
