"""Admin 사용자 관리 서비스 (PRD 3.6, 6.7).

승인/거절/변경의 상태 전이 규칙과 감사 로그 기록을 담당한다. 순수 전이 검증 함수는
DB 없이 단위 테스트 가능하도록 분리한다.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, User
from app.models.enums import UserRole, UserStatus
from app.services.users import create_root_folder


class AdminActionError(Exception):
    """admin 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- 순수 상태 전이 검증 (DB 무관, 단위 테스트 대상) ---------------------------

# PATCH 로 직접 지정 가능한 status (approve/reject 전용 전이는 제외).
_PATCHABLE_STATUSES = {UserStatus.ACTIVE, UserStatus.INACTIVE}


def check_can_approve(current_status: str) -> None:
    if current_status != UserStatus.PENDING:
        raise AdminActionError(409, "가입 신청(pending) 상태의 사용자만 승인할 수 있습니다.")


def check_can_reject(current_status: str) -> None:
    if current_status != UserStatus.PENDING:
        raise AdminActionError(409, "가입 신청(pending) 상태의 사용자만 거절할 수 있습니다.")


def check_status_update(new_status: UserStatus) -> None:
    if new_status not in _PATCHABLE_STATUSES:
        raise AdminActionError(
            422,
            "status 는 active/inactive 만 지정할 수 있습니다 "
            "(승인은 approve, 거절은 reject 사용).",
        )


def check_self_privilege_guard(
    actor_id: int,
    target_id: int,
    new_status: UserStatus | None,
    new_role: UserRole | None,
) -> None:
    """마지막 admin 잠금 방지 — 자기 자신의 admin 권한 해제/비활성화 거부."""
    if actor_id != target_id:
        return
    if new_role is not None and new_role != UserRole.ADMIN:
        raise AdminActionError(400, "자기 자신의 관리자 권한은 해제할 수 없습니다.")
    if new_status is not None and new_status != UserStatus.ACTIVE:
        raise AdminActionError(400, "자기 자신의 계정은 비활성화할 수 없습니다.")


# --- 감사 로그 --------------------------------------------------------------


def _record_audit(
    session: AsyncSession,
    actor_id: int,
    action: str,
    target_id: int,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_type="user",
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
    """사용자 목록 + 총 개수. status 로 필터링(예: pending)."""
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


async def approve_user(session: AsyncSession, actor: User, user_id: int) -> User:
    """pending → active + 루트 폴더 생성 + 감사 로그 (PRD 6.7)."""
    user = await _get_target(session, user_id)
    check_can_approve(user.status)

    user.status = UserStatus.ACTIVE
    await create_root_folder(session, user)
    _record_audit(session, actor.id, "user.approve", user.id, {"status": "active"})
    await session.commit()
    await session.refresh(user)
    return user


async def reject_user(session: AsyncSession, actor: User, user_id: int) -> User:
    """pending → rejected + 감사 로그 (PRD 6.7)."""
    user = await _get_target(session, user_id)
    check_can_reject(user.status)

    user.status = UserStatus.REJECTED
    _record_audit(session, actor.id, "user.reject", user.id, {"status": "rejected"})
    await session.commit()
    await session.refresh(user)
    return user


async def update_user(
    session: AsyncSession,
    actor: User,
    user_id: int,
    new_status: UserStatus | None,
    new_role: UserRole | None,
    new_max_storage: int | None,
) -> User:
    """status/role/max_storage 변경 + 감사 로그. 자기 권한 해제/비활성화 거부 (PRD 6.7)."""
    if new_status is not None:
        check_status_update(new_status)
    check_self_privilege_guard(actor.id, user_id, new_status, new_role)

    user = await _get_target(session, user_id)

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

    if changes:
        _record_audit(session, actor.id, "user.update", user.id, changes)
    await session.commit()
    await session.refresh(user)
    return user
