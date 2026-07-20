"""Admin 사용자 관리 서비스 (PRD 3.6, 6.7).

승인/거절/변경의 상태 전이 규칙과 감사 로그 기록을 담당한다. 순수 전이 검증 함수는
DB 없이 단위 테스트 가능하도록 분리한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, File, Group, GroupMember, Share, User
from app.models.enums import ADMIN_ROLES, UserRole, UserStatus


class AdminActionError(Exception):
    """admin 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- 순수 상태 전이 검증 (DB 무관, 단위 테스트 대상) ---------------------------

# PATCH 로 직접 지정 가능한 status (가입 코드제에서는 active/inactive 만 존재).
_PATCHABLE_STATUSES = {UserStatus.ACTIVE, UserStatus.INACTIVE}


def check_status_update(new_status: UserStatus) -> None:
    if new_status not in _PATCHABLE_STATUSES:
        raise AdminActionError(
            422, "status 는 active/inactive 만 지정할 수 있습니다."
        )


def check_self_privilege_guard(
    actor_id: int,
    target_id: int,
    new_status: UserStatus | None,
    new_role: UserRole | None,
) -> None:
    """마지막 admin 잠금 방지 — 자기 자신의 관리자 권한 해제/비활성화 거부."""
    if actor_id != target_id:
        return
    if new_role is not None and new_role not in ADMIN_ROLES:
        raise AdminActionError(400, "자기 자신의 관리자 권한은 해제할 수 없습니다.")
    if new_status is not None and new_status != UserStatus.ACTIVE:
        raise AdminActionError(400, "자기 자신의 계정은 비활성화할 수 없습니다.")


def check_role_change_authorization(
    actor: User, target: User, new_role: UserRole | None
) -> None:
    """관리자 권한(role) 부여/회수 인가 — super_admin 만, super_admin 계정은 불변.

    - role 변경은 super_admin 만 수행할 수 있다(일반 admin 은 나머지 관리 기능만).
    - super_admin 권한은 API 로 부여할 수 없다(마이그레이션/CLI 전용) — 다중 super_admin 방지.
    - super_admin 계정의 role 은 아무도 변경할 수 없다(강등/잠금 방지).
    """
    if new_role is None or new_role == target.role:
        return
    if actor.role != UserRole.SUPER_ADMIN:
        raise AdminActionError(403, "관리자 권한 부여/회수는 최고 관리자만 할 수 있습니다.")
    if new_role == UserRole.SUPER_ADMIN:
        raise AdminActionError(400, "최고 관리자 권한은 부여할 수 없습니다.")
    if target.role == UserRole.SUPER_ADMIN:
        raise AdminActionError(400, "최고 관리자 계정의 역할은 변경할 수 없습니다.")


def check_super_admin_target_guard(actor: User, target: User) -> None:
    """super_admin 계정은 본인 외에는 수정(상태/할당량 포함) 불가 — 최상위 계정 보호."""
    if target.role == UserRole.SUPER_ADMIN and actor.id != target.id:
        raise AdminActionError(403, "최고 관리자 계정은 수정할 수 없습니다.")


def check_display_name_authorization(actor: User, new_display_name: str | None) -> None:
    """표시 이름 변경은 최고 관리자 전용 — 일반 admin 은 상태/할당량만 조정할 수 있다."""
    if new_display_name is not None and actor.role != UserRole.SUPER_ADMIN:
        raise AdminActionError(403, "사용자 이름 변경은 최고 관리자만 할 수 있습니다.")


# --- 감사 로그 --------------------------------------------------------------


def _record_audit(
    session: AsyncSession,
    actor_id: int,
    action: str,
    target_id: int,
    detail: dict[str, Any] | None = None,
    target_type: str = "user",
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail,
        )
    )


# --- DB 조작 ----------------------------------------------------------------


async def list_users(
    session: AsyncSession,
    status_filter: str | None,
    page: int,
    size: int,
) -> tuple[list[User], int]:
    """사용자 목록 + 총 개수. status 로 필터링(active/inactive)."""
    base = select(User)
    count_q = select(func.count()).select_from(User)
    if status_filter is not None:
        base = base.where(User.status == status_filter)
        count_q = count_q.where(User.status == status_filter)

    total = (await session.execute(count_q)).scalar_one()
    offset = (page - 1) * size
    rows = (
        await session.execute(
            base.order_by(User.created_at.desc()).offset(offset).limit(size)
        )
    ).scalars().all()
    return list(rows), total


async def _get_target(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise AdminActionError(404, "사용자를 찾을 수 없습니다.")
    return user


async def update_user(
    session: AsyncSession,
    actor: User,
    user_id: int,
    new_status: UserStatus | None,
    new_role: UserRole | None,
    new_max_storage: int | None,
    new_display_name: str | None = None,
) -> User:
    """status/role/max_storage/display_name 변경 + 감사 로그 (PRD 6.7).

    인가: 자기 권한 해제/비활성화 거부, super_admin 계정 보호, role 부여/회수·이름 변경은
    super_admin 만.
    """
    if new_status is not None:
        check_status_update(new_status)
    check_self_privilege_guard(actor.id, user_id, new_status, new_role)
    check_display_name_authorization(actor, new_display_name)

    user = await _get_target(session, user_id)
    check_super_admin_target_guard(actor, user)
    check_role_change_authorization(actor, user, new_role)

    changes: dict[str, Any] = {}
    if new_status is not None and user.status != new_status:
        changes["status"] = {"from": user.status, "to": str(new_status)}
        user.status = new_status
    if new_role is not None and user.role != new_role:
        changes["role"] = {"from": user.role, "to": str(new_role)}
        user.role = new_role
    if new_max_storage is not None and user.max_storage != new_max_storage:
        changes["max_storage"] = {"from": user.max_storage, "to": new_max_storage}
        user.max_storage = new_max_storage
    if new_display_name is not None:
        stripped = new_display_name.strip()
        if stripped and user.display_name != stripped:
            changes["display_name"] = {"from": user.display_name, "to": stripped}
            user.display_name = stripped

    if changes:
        _record_audit(session, actor.id, "user.update", user.id, changes)
    await session.commit()
    await session.refresh(user)
    return user


# --- 전체 그룹 조회 (PRD 6.7) ------------------------------------------------


async def list_groups(
    session: AsyncSession,
    include_inactive: bool,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    """전체 그룹 + owner 이메일 + 활성 멤버 수 + 소유 파일 수. (dict 목록, 총 개수) 반환."""
    # 활성 멤버 수(removed_at IS NULL)와 그룹 소유 파일 수(미삭제)를 상관 서브쿼리로 집계한다.
    member_count = (
        select(func.count())
        .select_from(GroupMember)
        .where(GroupMember.group_id == Group.id, GroupMember.removed_at.is_(None))
        .correlate(Group)
        .scalar_subquery()
    )
    file_count = (
        select(func.count())
        .select_from(File)
        .where(File.group_id == Group.id, File.is_deleted.is_(False))
        .correlate(Group)
        .scalar_subquery()
    )

    base = select(
        Group, User.email, member_count.label("member_count"), file_count.label("file_count")
    ).join(User, Group.owner_user_id == User.id)
    count_q = select(func.count()).select_from(Group)
    if not include_inactive:
        base = base.where(Group.is_active.is_(True))
        count_q = count_q.where(Group.is_active.is_(True))

    total = (await session.execute(count_q)).scalar_one()
    offset = (page - 1) * size
    rows = (
        await session.execute(
            base.order_by(Group.created_at.desc()).offset(offset).limit(size)
        )
    ).all()

    items = [
        {
            "id": g.id,
            "name": g.name,
            "description": g.description,
            "owner_id": g.owner_user_id,
            "owner_email": owner_email,
            "is_active": g.is_active,
            "member_count": m_count,
            "file_count": f_count,
            "created_at": g.created_at,
        }
        for g, owner_email, m_count, f_count in rows
    ]
    return items, total


# --- 전체 공유 링크 조회 + 강제 비활성화 (PRD 6.7) ---------------------------


async def list_shares(
    session: AsyncSession,
    active: bool | None,
    user_id: int | None,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    """전체 공유 링크 + 파일명 + 생성자 이메일. active/userId 필터. (dict 목록, 총 개수)."""
    base = (
        select(Share, File.name, User.email)
        .join(File, Share.file_id == File.id)
        .join(User, Share.created_by == User.id)
    )
    count_q = select(func.count()).select_from(Share)
    if active is not None:
        base = base.where(Share.is_active.is_(active))
        count_q = count_q.where(Share.is_active.is_(active))
    if user_id is not None:
        base = base.where(Share.created_by == user_id)
        count_q = count_q.where(Share.created_by == user_id)

    total = (await session.execute(count_q)).scalar_one()
    offset = (page - 1) * size
    rows = (
        await session.execute(
            base.order_by(Share.created_at.desc()).offset(offset).limit(size)
        )
    ).all()

    items = [
        {
            "id": s.id,
            "file_id": s.file_id,
            "file_name": file_name,
            "created_by": s.created_by,
            "creator_email": creator_email,
            "permission": s.permission,
            "is_active": s.is_active,
            "download_count": s.download_count,
            "max_downloads": s.max_downloads,
            "expires_at": s.expires_at,
            "created_at": s.created_at,
        }
        for s, file_name, creator_email in rows
    ]
    return items, total


async def get_share_detail(session: AsyncSession, share_id: int) -> dict[str, Any] | None:
    """단일 공유 링크 + 파일명 + 생성자 이메일. 없으면 None."""
    row = (
        await session.execute(
            select(Share, File.name, User.email)
            .join(File, Share.file_id == File.id)
            .join(User, Share.created_by == User.id)
            .where(Share.id == share_id)
        )
    ).first()
    if row is None:
        return None
    s, file_name, creator_email = row
    return {
        "id": s.id,
        "file_id": s.file_id,
        "file_name": file_name,
        "created_by": s.created_by,
        "creator_email": creator_email,
        "permission": s.permission,
        "is_active": s.is_active,
        "download_count": s.download_count,
        "max_downloads": s.max_downloads,
        "expires_at": s.expires_at,
        "created_at": s.created_at,
    }


async def force_disable_share(session: AsyncSession, actor: User, share_id: int) -> Share:
    """공유 링크 강제 비활성화 + 감사 로그(share.force_disable). 멱등 (PRD 6.7, 3.6.4).

    게이트웨이 모델이라 다음 공개 요청부터 즉시 410 으로 차단된다. 이미 비활성이면 감사
    로그를 남기지 않고 그대로 반환한다.
    """
    share = await session.get(Share, share_id)
    if share is None:
        raise AdminActionError(404, "공유 링크를 찾을 수 없습니다.")
    if share.is_active:
        share.is_active = False
        _record_audit(
            session,
            actor.id,
            "share.force_disable",
            share.id,
            {"is_active": {"from": True, "to": False}},
            target_type="share",
        )
        await session.commit()
        await session.refresh(share)
    return share


# --- 대시보드 통계 (PRD 6.7) -------------------------------------------------


async def get_stats(session: AsyncSession) -> dict[str, Any]:
    """인스턴스 집계 통계 (파일 메타데이터만 — 내용 접근 없음, 3.6.4).

    상태별 사용자 수, 파일/폴더 수, 총 사용량, 공유 링크(활성/비활성), 그룹 수, 상위 5 사용자.
    """
    # 상태별 사용자 수.
    status_rows = (
        await session.execute(select(User.status, func.count()).group_by(User.status))
    ).all()
    users_by_status = {str(s): c for s, c in status_rows}
    total_users = sum(users_by_status.values())

    # 파일/폴더 수 (미삭제). 루트 폴더 행도 폴더로 집계된다.
    total_files = (
        await session.execute(
            select(func.count())
            .select_from(File)
            .where(File.is_folder.is_(False), File.is_deleted.is_(False))
        )
    ).scalar_one()
    total_folders = (
        await session.execute(
            select(func.count())
            .select_from(File)
            .where(File.is_folder.is_(True), File.is_deleted.is_(False))
        )
    ).scalar_one()

    # 총 사용량 — users.storage_used 가 할당량 검사의 진실 소스(PRD 5.10)이므로 이를 합산한다.
    total_storage_used = (
        await session.execute(select(func.coalesce(func.sum(User.storage_used), 0)))
    ).scalar_one()

    # 공유 링크 활성/비활성.
    share_rows = (
        await session.execute(select(Share.is_active, func.count()).group_by(Share.is_active))
    ).all()
    active_shares = sum(c for is_active, c in share_rows if is_active)
    inactive_shares = sum(c for is_active, c in share_rows if not is_active)

    total_groups = (
        await session.execute(select(func.count()).select_from(Group))
    ).scalar_one()

    # 상위 5 사용자 (사용량 기준).
    top_rows = (
        await session.execute(
            select(User.email, User.storage_used, User.max_storage)
            .order_by(User.storage_used.desc())
            .limit(5)
        )
    ).all()
    top_users = [
        {"email": email, "storage_used": used, "max_storage": maximum}
        for email, used, maximum in top_rows
    ]

    return {
        "total_users": total_users,
        "users_by_status": users_by_status,
        "total_files": total_files,
        "total_folders": total_folders,
        "total_storage_used": total_storage_used,
        "total_shares": {
            "active": active_shares,
            "inactive": inactive_shares,
            "total": active_shares + inactive_shares,
        },
        "total_groups": total_groups,
        "top_users": top_users,
    }


# --- 감사 로그 조회 (PRD 5.9, 6.7) -------------------------------------------


async def list_audit_logs(
    session: AsyncSession,
    actor_id: int | None,
    target_type: str | None,
    action: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    page: int,
    size: int,
) -> tuple[list[dict[str, Any]], int]:
    """감사 로그 + actor 이메일 join. actorId/targetType/action/from/to 필터, 최신순."""
    base = select(AuditLog, User.email).join(User, AuditLog.actor_id == User.id)
    count_q = select(func.count()).select_from(AuditLog)

    conditions = []
    if actor_id is not None:
        conditions.append(AuditLog.actor_id == actor_id)
    if target_type is not None:
        conditions.append(AuditLog.target_type == target_type)
    if action is not None:
        conditions.append(AuditLog.action == action)
    if date_from is not None:
        conditions.append(AuditLog.created_at >= date_from)
    if date_to is not None:
        conditions.append(AuditLog.created_at <= date_to)
    for cond in conditions:
        base = base.where(cond)
        count_q = count_q.where(cond)

    total = (await session.execute(count_q)).scalar_one()
    offset = (page - 1) * size
    rows = (
        await session.execute(
            base.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .offset(offset)
            .limit(size)
        )
    ).all()

    items = [
        {
            "id": log.id,
            "actor_id": log.actor_id,
            "actor_email": actor_email,
            "action": log.action,
            "target_type": log.target_type,
            "target_id": log.target_id,
            "detail": log.detail,
            "created_at": log.created_at,
        }
        for log, actor_email in rows
    ]
    return items, total
