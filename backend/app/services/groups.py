"""그룹 CRUD + 멤버 관리 서비스 (PRD 1.3, 3.1.1, 3.1.2, 6.4).

역할 가드(초대/제거/역할 변경/소유권 이전 권한 매트릭스)는 DB 없이 단위 테스트 가능하도록
순수 함수로 분리한다. 그룹/멤버 변경은 audit_logs 에 기록한다(PRD 5.9).

`get_member_role` / `require_group_role` / `get_user_group_ids` 는 이후 스테이지(파일 그룹 권한
판정)가 재사용하는 공개 헬퍼다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AuditLog,
    File,
    FileGroupPermission,
    Group,
    GroupMember,
    User,
)
from app.models.enums import GroupRole, UserStatus


class GroupServiceError(Exception):
    """그룹 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# 멤버 관리(초대/제거)가 가능한 역할.
_MEMBER_MANAGERS = frozenset({GroupRole.OWNER, GroupRole.ADMIN})
# 초대/역할 변경으로 직접 지정 가능한 역할 (owner 는 소유권 이전 경유).
_ASSIGNABLE_ROLES = frozenset({GroupRole.ADMIN, GroupRole.MEMBER})


# --- 순수 역할 가드 (DB 무관, 단위 테스트 대상) -------------------------------


def check_manage_members(actor_role: GroupRole | None) -> None:
    """멤버 초대/제거 권한 — owner/admin 만."""
    if actor_role not in _MEMBER_MANAGERS:
        raise GroupServiceError(403, "그룹원 관리 권한이 없습니다 (owner/admin 만 가능).")


def check_assignable_role(role: GroupRole) -> None:
    """초대/역할 변경으로 부여 가능한 역할인지 — owner 직접 부여 금지."""
    if role == GroupRole.OWNER:
        raise GroupServiceError(
            400, "owner 역할은 직접 부여할 수 없습니다 (소유권 이전을 사용하세요)."
        )
    if role not in _ASSIGNABLE_ROLES:
        raise GroupServiceError(422, "role 은 admin/member 만 지정할 수 있습니다.")


def check_can_remove_member(
    actor_role: GroupRole | None,
    target_role: GroupRole,
    actor_id: int,
    target_id: int,
) -> None:
    """멤버 제거 권한 매트릭스.

    - owner/admin 만 제거 가능.
    - owner 는 자신을 제거할 수 없다 (소유권 이전 먼저).
    - admin 은 admin/owner 를 제거할 수 없다 (member 만).
    """
    check_manage_members(actor_role)
    if target_role == GroupRole.OWNER:
        # owner 자신 제거 포함 — 소유자는 제거 대상이 될 수 없다.
        raise GroupServiceError(
            400, "그룹 소유자는 제거할 수 없습니다 (소유권 이전을 먼저 수행하세요)."
        )
    if actor_role == GroupRole.ADMIN and target_role == GroupRole.ADMIN:
        raise GroupServiceError(403, "admin 은 다른 admin 을 제거할 수 없습니다.")


def check_can_change_role(
    actor_role: GroupRole | None,
    target_role: GroupRole,
    new_role: GroupRole,
) -> None:
    """역할 변경 권한 — owner 만, 대상은 owner 가 아니어야 하고 새 역할은 admin/member."""
    if actor_role != GroupRole.OWNER:
        raise GroupServiceError(403, "역할 변경은 그룹 소유자만 가능합니다.")
    if target_role == GroupRole.OWNER:
        raise GroupServiceError(
            400, "소유자의 역할은 변경할 수 없습니다 (소유권 이전을 사용하세요)."
        )
    check_assignable_role(new_role)


def check_can_transfer_ownership(actor_role: GroupRole | None) -> None:
    """소유권 이전 권한 — owner 만."""
    if actor_role != GroupRole.OWNER:
        raise GroupServiceError(403, "소유권 이전은 그룹 소유자만 가능합니다.")


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
            target_type="group",
            target_id=target_id,
            detail=detail,
        )
    )


# --- 공개 헬퍼 (이후 스테이지의 파일 권한 판정에서 재사용) ---------------------


async def get_member_role(
    session: AsyncSession, group_id: int, user_id: int
) -> GroupRole | None:
    """활성 멤버십(removed_at IS NULL)의 역할. 비멤버면 None."""
    row = (
        await session.execute(
            select(GroupMember.role).where(
                GroupMember.group_id == group_id,
                GroupMember.user_id == user_id,
                GroupMember.removed_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    return GroupRole(row) if row is not None else None


async def require_group_role(
    session: AsyncSession,
    group_id: int,
    user_id: int,
    allowed: frozenset[GroupRole] | set[GroupRole],
) -> GroupRole:
    """활성 멤버십 역할이 allowed 에 속하는지 확인하고 역할을 반환. 아니면 403."""
    role = await get_member_role(session, group_id, user_id)
    if role is None or role not in allowed:
        raise GroupServiceError(403, "이 작업을 수행할 권한이 없습니다.")
    return role


async def get_user_group_ids(session: AsyncSession, user_id: int) -> list[int]:
    """사용자가 활성 멤버인 그룹 id 목록 (파일 권한 판정용)."""
    rows = (
        await session.execute(
            select(GroupMember.group_id).where(
                GroupMember.user_id == user_id,
                GroupMember.removed_at.is_(None),
            )
        )
    ).scalars().all()
    return list(rows)


# --- 내부 조회 --------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


async def _get_active_group(session: AsyncSession, group_id: int) -> Group:
    group = await session.get(Group, group_id)
    if group is None or not group.is_active:
        raise GroupServiceError(404, "그룹을 찾을 수 없습니다.")
    return group


async def fetch_members(
    session: AsyncSession, group_id: int
) -> list[tuple[GroupMember, str, str]]:
    """활성 멤버 목록 + (이메일, 표시명). 가입 순."""
    rows = (
        await session.execute(
            select(GroupMember, User.email, User.display_name)
            .join(User, User.id == GroupMember.user_id)
            .where(
                GroupMember.group_id == group_id,
                GroupMember.removed_at.is_(None),
            )
            .order_by(GroupMember.joined_at.asc(), GroupMember.id.asc())
        )
    ).all()
    return [(m, email, name) for m, email, name in rows]


async def _active_member_count(session: AsyncSession, group_id: int) -> int:
    return (
        await session.execute(
            select(func.count())
            .select_from(GroupMember)
            .where(
                GroupMember.group_id == group_id,
                GroupMember.removed_at.is_(None),
            )
        )
    ).scalar_one()


# --- 그룹 CRUD --------------------------------------------------------------


async def create_group(
    session: AsyncSession, actor: User, name: str, description: str | None
) -> Group:
    """그룹 생성 — 생성자가 owner_user_id + group_members(role=owner)로 자동 등록.

    이름 전역 중복 시 409 (PRD 5.5 UNIQUE(name)).
    """
    group = Group(name=name, description=description, owner_user_id=actor.id)
    session.add(group)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise GroupServiceError(409, "이미 사용 중인 그룹 이름입니다.") from exc

    session.add(
        GroupMember(group_id=group.id, user_id=actor.id, role=GroupRole.OWNER)
    )
    _record_audit(session, actor.id, "group.create", group.id, {"name": name})
    await session.commit()
    await session.refresh(group)
    return group


async def list_groups(
    session: AsyncSession, user_id: int, page: int, size: int
) -> tuple[list[tuple[Group, int, str | None]], int]:
    """활성 그룹 목록 + (멤버 수, 요청자 역할). 페이지네이션."""
    member_count_sq = (
        select(
            GroupMember.group_id.label("group_id"),
            func.count().label("member_count"),
        )
        .where(GroupMember.removed_at.is_(None))
        .group_by(GroupMember.group_id)
        .subquery()
    )
    my_sq = (
        select(
            GroupMember.group_id.label("group_id"),
            GroupMember.role.label("my_role"),
        )
        .where(
            GroupMember.user_id == user_id,
            GroupMember.removed_at.is_(None),
        )
        .subquery()
    )

    total = (
        await session.execute(
            select(func.count()).select_from(Group).where(Group.is_active.is_(True))
        )
    ).scalar_one()

    offset = (page - 1) * size
    rows = (
        await session.execute(
            select(
                Group,
                func.coalesce(member_count_sq.c.member_count, 0),
                my_sq.c.my_role,
            )
            .outerjoin(member_count_sq, member_count_sq.c.group_id == Group.id)
            .outerjoin(my_sq, my_sq.c.group_id == Group.id)
            .where(Group.is_active.is_(True))
            .order_by(Group.created_at.desc(), Group.id.desc())
            .offset(offset)
            .limit(size)
        )
    ).all()
    return [(g, count, role) for g, count, role in rows], total


async def get_group_detail(
    session: AsyncSession, group_id: int, user_id: int
) -> tuple[Group, int, str | None, list[tuple[GroupMember, str, str]]]:
    """그룹 상세 — 그룹 + 멤버 수 + 요청자 역할 + 활성 멤버 목록. 비멤버도 조회 가능."""
    group = await _get_active_group(session, group_id)
    members = await fetch_members(session, group_id)
    my_role = next(
        (m.role for m, _, _ in members if m.user_id == user_id), None
    )
    return group, len(members), my_role, members


async def get_group_members(
    session: AsyncSession, group_id: int
) -> list[tuple[GroupMember, str, str]]:
    """활성 멤버 목록 (PRD 6.4 GET /members). 비멤버도 조회 가능."""
    await _get_active_group(session, group_id)
    return await fetch_members(session, group_id)


async def update_group(
    session: AsyncSession,
    actor: User,
    group_id: int,
    name: str | None,
    description: str | None,
    description_set: bool,
) -> Group:
    """이름/설명 수정 (owner/admin 만). 이름 중복 시 409.

    description_set 은 description 필드가 요청 본문에 포함됐는지 여부(None 으로의 명시적 갱신 구분).
    """
    group = await _get_active_group(session, group_id)
    await require_group_role(session, group_id, actor.id, _MEMBER_MANAGERS)

    changes: dict[str, Any] = {}
    if name is not None and name != group.name:
        changes["name"] = {"from": group.name, "to": name}
        group.name = name
    if description_set and description != group.description:
        changes["description"] = {"from": group.description, "to": description}
        group.description = description

    if not changes:
        return group

    _record_audit(session, actor.id, "group.update", group.id, changes)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise GroupServiceError(409, "이미 사용 중인 그룹 이름입니다.") from exc
    await session.refresh(group)
    return group


async def delete_group(session: AsyncSession, actor: User, group_id: int) -> None:
    """그룹 소프트 삭제 (owner 만) — is_active=FALSE + 소속 파일 권한 일괄 제거.

    그룹 소유 파일(files.group_id)은 group_id=NULL 로 되돌려 개인 소유로 전환한다
    (files.user_id 생성자가 개인 소유자, PRD 5.8). file_group_permissions 는 삭제한다(PRD 3.1.1).
    """
    group = await _get_active_group(session, group_id)
    await require_group_role(session, group_id, actor.id, {GroupRole.OWNER})

    # 삭제로 모든 파일 권한이 사라지므로 활성 멤버 전원의 권한 캐시를 무효화한다 (PRD 1.4).
    from app.services.permissions import invalidate_users

    member_ids = (
        await session.execute(
            select(GroupMember.user_id).where(
                GroupMember.group_id == group_id,
                GroupMember.removed_at.is_(None),
            )
        )
    ).scalars().all()

    perms_result = cast(
        "CursorResult[Any]",
        await session.execute(
            delete(FileGroupPermission).where(
                FileGroupPermission.group_id == group_id
            )
        ),
    )
    files_result = cast(
        "CursorResult[Any]",
        await session.execute(
            update(File).where(File.group_id == group_id).values(group_id=None)
        ),
    )
    perms_removed = perms_result.rowcount
    files_detached = files_result.rowcount

    group.is_active = False
    _record_audit(
        session,
        actor.id,
        "group.delete",
        group.id,
        {
            "name": group.name,
            "permissions_removed": perms_removed,
            "files_detached": files_detached,
        },
    )
    await session.commit()
    await invalidate_users(member_ids)


# --- 멤버 관리 --------------------------------------------------------------


async def add_member(
    session: AsyncSession,
    actor: User,
    group_id: int,
    user_id: int,
    role: GroupRole,
) -> tuple[GroupMember, str, str]:
    """그룹원 초대 (owner/admin 만). 재초대는 upsert 로 재활성화.

    owner 직접 부여 금지. 대상 사용자 status='active' 확인 (PRD 6.4).
    """
    await _get_active_group(session, group_id)
    await require_group_role(session, group_id, actor.id, _MEMBER_MANAGERS)
    check_assignable_role(role)

    target = await session.get(User, user_id)
    if target is None:
        raise GroupServiceError(404, "대상 사용자를 찾을 수 없습니다.")
    if target.status != UserStatus.ACTIVE:
        raise GroupServiceError(400, "활성 상태의 사용자만 초대할 수 있습니다.")

    # 재초대 upsert — soft delete 된 멤버는 새 행 INSERT 대신 재활성화 (PRD 5.6).
    stmt = pg_insert(GroupMember).values(
        group_id=group_id, user_id=user_id, role=role.value
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_group_members_group_user",
        set_={
            "removed_at": None,
            "role": stmt.excluded.role,
            "joined_at": func.now(),
        },
    )
    await session.execute(stmt)
    _record_audit(
        session,
        actor.id,
        "group.member_add",
        group_id,
        {"user_id": user_id, "role": role.value},
    )
    await session.commit()

    # 멤버가 그룹에 합류하면 그 그룹의 파일 권한을 즉시 갖는다 — 권한 캐시 무효화 (PRD 1.4).
    from app.services.permissions import invalidate_users

    await invalidate_users([user_id])

    row = (
        await session.execute(
            select(GroupMember, User.email, User.display_name)
            .join(User, User.id == GroupMember.user_id)
            .where(
                GroupMember.group_id == group_id,
                GroupMember.user_id == user_id,
            )
        )
    ).one()
    member, email, name = row
    return member, email, name


async def remove_member(
    session: AsyncSession, actor: User, group_id: int, user_id: int
) -> None:
    """그룹원 제거 (owner/admin 만). removed_at 설정 (soft delete).

    admin 은 admin/owner 제거 불가, owner 는 자신 제거 불가 (소유권 이전 먼저).
    """
    await _get_active_group(session, group_id)
    actor_role = await get_member_role(session, group_id, actor.id)
    target_role = await get_member_role(session, group_id, user_id)
    if target_role is None:
        raise GroupServiceError(404, "그룹원을 찾을 수 없습니다.")

    check_can_remove_member(actor_role, target_role, actor.id, user_id)

    await session.execute(
        update(GroupMember)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
            GroupMember.removed_at.is_(None),
        )
        .values(removed_at=_now())
    )
    _record_audit(
        session,
        actor.id,
        "group.member_remove",
        group_id,
        {"user_id": user_id, "role": target_role.value},
    )
    await session.commit()

    # 멤버 제거 시 그룹 권한을 잃으므로 그 사용자의 권한 캐시를 즉시 무효화 (PRD 1.4).
    from app.services.permissions import invalidate_users

    await invalidate_users([user_id])


async def change_member_role(
    session: AsyncSession,
    actor: User,
    group_id: int,
    user_id: int,
    new_role: GroupRole,
) -> tuple[GroupMember, str, str]:
    """그룹원 역할 변경 (owner 만, admin|member). 소유자 역할은 변경 불가."""
    await _get_active_group(session, group_id)
    actor_role = await get_member_role(session, group_id, actor.id)
    target_role = await get_member_role(session, group_id, user_id)
    if target_role is None:
        raise GroupServiceError(404, "그룹원을 찾을 수 없습니다.")

    check_can_change_role(actor_role, target_role, new_role)

    await session.execute(
        update(GroupMember)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
            GroupMember.removed_at.is_(None),
        )
        .values(role=new_role.value)
    )
    _record_audit(
        session,
        actor.id,
        "group.member_role",
        group_id,
        {"user_id": user_id, "from": target_role.value, "to": new_role.value},
    )
    await session.commit()

    row = (
        await session.execute(
            select(GroupMember, User.email, User.display_name)
            .join(User, User.id == GroupMember.user_id)
            .where(
                GroupMember.group_id == group_id,
                GroupMember.user_id == user_id,
            )
        )
    ).one()
    member, email, name = row
    return member, email, name


async def transfer_ownership(
    session: AsyncSession, actor: User, group_id: int, user_id: int
) -> Group:
    """소유권 이전 (owner 만) — owner_user_id 교체 + 역할 스왑(기존 owner→admin, 대상→owner).

    대상은 활성 멤버여야 한다 (PRD 3.1.1 그룹 관리자 위임).
    """
    group = await _get_active_group(session, group_id)
    actor_role = await get_member_role(session, group_id, actor.id)
    check_can_transfer_ownership(actor_role)

    if user_id == actor.id:
        raise GroupServiceError(400, "이미 소유자입니다.")

    target_role = await get_member_role(session, group_id, user_id)
    if target_role is None:
        raise GroupServiceError(400, "대상은 그룹의 활성 멤버여야 합니다.")

    # 역할 스왑 + 소유자 교체.
    await session.execute(
        update(GroupMember)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == actor.id,
            GroupMember.removed_at.is_(None),
        )
        .values(role=GroupRole.ADMIN.value)
    )
    await session.execute(
        update(GroupMember)
        .where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
            GroupMember.removed_at.is_(None),
        )
        .values(role=GroupRole.OWNER.value)
    )
    group.owner_user_id = user_id
    _record_audit(
        session,
        actor.id,
        "group.transfer_ownership",
        group_id,
        {"from": actor.id, "to": user_id},
    )
    await session.commit()
    await session.refresh(group)
    return group
