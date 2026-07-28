"""셋업 위저드 + 애플리케이션 설정 서비스 (PRD 3.6.2, 5.12, 6.1).

첫 부팅 시 admin 이 0명이면 셋업 위저드로 첫 admin 계정 + 초기 가입 코드 + 기본 할당량을
한 번에 설정하고 셋업을 영구 잠근다. 동시 요청 레이스는 app_settings PK(`setup_completed`)에
대한 ON CONFLICT DO NOTHING 으로 원자적으로 방어한다(먼저 잠근 요청만 성공).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.models import AppSetting, SignupCode, User
from app.models.enums import ADMIN_ROLES, UserRole, UserStatus
from app.models.user import DEFAULT_MAX_STORAGE
from app.services.groups import ensure_all_users_group
from app.services.signup_codes import create_signup_code
from app.services.users import create_root_folder

# app_settings 키.
SETUP_COMPLETED_KEY = "setup_completed"
DEFAULT_MAX_STORAGE_KEY = "default_max_storage"


class SetupError(Exception):
    """셋업 실패(이미 완료 등). HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


async def admin_exists(session: AsyncSession) -> bool:
    """활성 여부와 무관하게 admin/super_admin 사용자가 1명이라도 있으면 True.

    셋업이 super_admin 만 만들 수도 있으므로 두 역할을 모두 센다 — 안 그러면 super_admin 만
    있는 인스턴스에서 셋업이 재개방된다.
    """
    result = await session.execute(
        select(func.count()).select_from(User).where(User.role.in_(ADMIN_ROLES))
    )
    return result.scalar_one() > 0


async def get_setting(session: AsyncSession, key: str) -> Any | None:
    row = await session.get(AppSetting, key)
    return row.value if row is not None else None


async def set_setting(session: AsyncSession, key: str, value: Any) -> None:
    """app_settings 를 upsert 한다. 호출자가 commit 한다."""
    await session.execute(
        pg_insert(AppSetting)
        .values(key=key, value=value)
        .on_conflict_do_update(
            index_elements=["key"], set_={"value": value, "updated_at": func.now()}
        )
    )


async def is_setup_completed(session: AsyncSession) -> bool:
    """셋업 완료 여부. setup_completed 플래그 또는 admin 존재 중 하나라도 참이면 완료."""
    if await get_setting(session, SETUP_COMPLETED_KEY):
        return True
    return await admin_exists(session)


async def get_default_max_storage(session: AsyncSession) -> int:
    """신규 가입자의 기본 할당량. 셋업 전(미설정)에는 기존 10GB 기본값을 유지한다."""
    value = await get_setting(session, DEFAULT_MAX_STORAGE_KEY)
    if value is None:
        return DEFAULT_MAX_STORAGE
    return int(value)


async def create_admin_account(
    session: AsyncSession,
    email: str,
    password: str,
    *,
    display_name: str = "Administrator",
    role: UserRole = UserRole.ADMIN,
) -> User:
    """active 관리자 계정 + 루트 폴더를 생성한다. flush 만 하고 커밋은 호출자가 한다.

    셋업 위저드와 CLI `create-admin`, 통합 테스트 부트스트랩이 공유하는 유일한 관리자 생성 경로.
    셋업 위저드는 최초 계정을 super_admin 으로, CLI 는 기본 admin 으로 만든다.
    """
    admin = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name,
        role=role,
        status=UserStatus.ACTIVE,
    )
    session.add(admin)
    await session.flush()
    await create_root_folder(session, admin)
    # @전사 시스템 그룹은 소유자(관리자)가 있어야 만들 수 있다. 관리자 생성 경로가 여기 하나뿐이라
    # 신규 설치·CLI·통합 테스트가 모두 이 훅으로 그룹을 갖게 된다 (spec/wiki-index.md).
    await ensure_all_users_group(session, admin)
    return admin


async def perform_setup(
    session: AsyncSession,
    *,
    admin_email: str,
    admin_password: str,
    admin_display_name: str = "Administrator",
    signup_code: str | None,
    default_max_storage: int,
) -> tuple[User, SignupCode]:
    """첫 admin + 초기 가입 코드 + 기본 할당량을 원자적으로 설정한다 (PRD 3.6.2).

    admin 이 이미 있거나 셋업이 완료됐으면 SetupError(403). 동시 셋업 레이스는 setup_completed
    sentinel 의 ON CONFLICT DO NOTHING 으로 방어한다(먼저 잠근 요청만 진행). (admin, code) 반환.
    """
    # 친절한 사전 검사(비레이스 경로). 진짜 방어는 아래 원자적 sentinel 삽입.
    if await admin_exists(session):
        raise SetupError(403, "이미 셋업이 완료되었습니다.")

    claimed = await session.execute(
        pg_insert(AppSetting)
        .values(key=SETUP_COMPLETED_KEY, value=True)
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(AppSetting.key)
    )
    if claimed.first() is None:
        # 다른 요청이 먼저 셋업을 잠갔다.
        raise SetupError(403, "이미 셋업이 완료되었습니다.")

    # 최초 계정은 super_admin — 이후 다른 사용자에게 admin 권한을 부여/회수할 수 있다.
    admin = await create_admin_account(
        session,
        admin_email,
        admin_password,
        display_name=admin_display_name or "Administrator",
        role=UserRole.SUPER_ADMIN,
    )
    await set_setting(session, DEFAULT_MAX_STORAGE_KEY, default_max_storage)
    code = await create_signup_code(
        session,
        admin.id,
        memo="초기 셋업 가입 코드",
        code=signup_code,
    )
    await session.commit()
    await session.refresh(admin)
    await session.refresh(code)
    return admin, code
