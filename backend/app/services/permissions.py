"""파일/폴더 그룹 권한 부여 + 상속 판정 서비스 (PRD 3.1.3, 5.7, 6.5/6.6).

설계 (PRD 5.7 — 물질화하지 않고 조회 시 판정):
  file_group_permissions 에는 **명시적 부여만** 저장한다. 판정 시 대상 파일에서 루트까지
  조상 경로를 recursive CTE 로 올라가며, 사용자 소속 그룹들의 권한 행 중 **가장 가까운
  조상의 것**을 적용한다(자기 자신 > 부모 > 조부…). 같은 거리에 여러 그룹 권한이 있으면
  최고 수준(manage > write > read)을 적용한다. 조상 행이라도 inherit_to_children=FALSE 면
  하위(자기 자신 제외)에 효력이 없고, expires_at 이 지난 행은 무효다.

판정 로직의 순수 부분(resolve_effective_permission)은 DB 없이 단위 테스트 가능하도록 분리한다.

캐시(Redis, PRD 1.4/2.2/5.7):
  판정 결과를 `perm:{ugen}:{user_id}:{file_id}` 키에 짧은 TTL 로 캐시한다. 무효화는
  **사용자별 세대 카운터**(`perm:ugen:{user_id}`)를 증가시키는 방식이다 — 카운터가 바뀌면
  그 사용자의 이전 캐시 키는 도달 불가가 되어 일괄 무효화된다. 권한 부여/수정/회수 시 해당
  그룹의 활성 멤버 전원의 세대를, 그룹 멤버 변경/그룹 삭제 시 관련 사용자의 세대를 올린다.
  정확성(즉시 반영)이 캐시 적중률보다 우선이므로, redis 오류 시 캐시를 건너뛰고 항상 DB 로
  판정한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import bindparam, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import redis_client
from app.models import (
    AuditLog,
    File,
    FileGroupPermission,
    Group,
    GroupMember,
    User,
)
from app.models.enums import GroupPermission
from app.services.groups import get_user_group_ids

AccessNeed = Literal["read", "write", "manage"]


class PermissionServiceError(Exception):
    """권한 조작 실패. HTTP 상태 코드를 함께 전달한다."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# 권한 수준 순위 — manage > write > read.
_RANK: dict[GroupPermission, int] = {
    GroupPermission.READ: 1,
    GroupPermission.WRITE: 2,
    GroupPermission.MANAGE: 3,
}


def permission_covers(effective: GroupPermission, need: AccessNeed) -> bool:
    """effective 권한이 need 를 충족하는지(같거나 높은 수준)."""
    return _RANK[effective] >= _RANK[GroupPermission(need)]


# --- 순수 판정 로직 (DB 무관, 단위 테스트 대상) ------------------------------


@dataclass(frozen=True)
class AncestorPermRow:
    """조상 경로에서 수집한 권한 행 하나. depth 0 = 대상 파일 자신, 1 = 부모, …."""

    depth: int
    file_id: int
    permission: str
    inherit_to_children: bool
    expires_at: datetime | None


def resolve_effective_permission(
    rows: Iterable[AncestorPermRow], now: datetime
) -> tuple[GroupPermission | None, int | None]:
    """조상 경로 권한 행들에서 유효 권한을 판정한다 (PRD 5.7).

    - 만료 행(expires_at <= now) 제외.
    - 조상 행(depth > 0)은 inherit_to_children=TRUE 만 유효(자기 자신 depth 0 은 항상 유효).
    - 남은 행 중 **가장 가까운 조상(min depth)** 만 적용하고, 동거리면 최고 수준을 택한다.

    반환: (유효 권한 수준, 그 권한이 부여된 파일 id). 없으면 (None, None).
    """
    applicable = [
        r
        for r in rows
        if (r.expires_at is None or r.expires_at > now)
        and (r.depth == 0 or r.inherit_to_children)
    ]
    if not applicable:
        return None, None

    min_depth = min(r.depth for r in applicable)
    best_level: GroupPermission | None = None
    best_file: int | None = None
    for r in applicable:
        if r.depth != min_depth:
            continue
        level = GroupPermission(r.permission)
        if best_level is None or _RANK[level] > _RANK[best_level]:
            best_level = level
            best_file = r.file_id
    return best_level, best_file


# --- 리스팅 메타 판정 (통합 드라이브 group_names/permission) ------------------
#
# 파일 목록의 Unix 유사 그룹/권한 컬럼을 채우기 위한 순수 판정 로직이다. 파일 자체의 직접
# 부여(batch 조회)로 끝나는 흔한 경로와, 조상 상속을 따라가야 하는 드문 경로를 나눈다.


@dataclass(frozen=True)
class AncestorGrantRow:
    """조상 경로에서 수집한 (그룹명 포함) 권한 행. depth 0 = 대상 파일 자신, 1 = 부모, …."""

    depth: int
    file_id: int
    group_id: int
    group_name: str
    permission: str
    inherit_to_children: bool
    expires_at: datetime | None


def _highest(levels: Iterable[GroupPermission]) -> GroupPermission:
    """권한 수준들 중 최고(manage > write > read)."""
    return max(levels, key=lambda p: _RANK[p])


def _dedup(names: Iterable[str]) -> list[str]:
    """순서를 보존하며 중복 제거."""
    out: list[str] = []
    for name in names:
        if name not in out:
            out.append(name)
    return out


def select_direct_grant(
    direct_grants: Iterable[tuple[int, str, str]],
    user_group_ids: set[int],
) -> tuple[GroupPermission | None, list[str]]:
    """파일 자체의 직접 부여(미만료) 중 사용자 소속 그룹 것만으로 유효 권한/부여 그룹명을 고른다.

    direct_grants 원소는 (group_id, permission, group_name). 파일 자신(depth 0)의 부여는
    inherit_to_children 과 무관하게 항상 적용되므로, 만료 필터만 통과하면 유효하다.
    반환: (유효 권한 수준, 부여 그룹명들). 내 그룹의 직접 부여가 없으면 (None, []) — 호출자가
    상속 판정으로 폴백한다.
    """
    mine = [(perm, name) for gid, perm, name in direct_grants if gid in user_group_ids]
    if not mine:
        return None, []
    best = _highest(GroupPermission(perm) for perm, _ in mine)
    return best, _dedup(name for _, name in mine)


def resolve_effective_grant(
    rows: Iterable[AncestorGrantRow], now: datetime
) -> tuple[GroupPermission | None, list[str]]:
    """조상 경로 권한 행들에서 유효 권한 수준 + 부여 그룹명을 판정한다 (PRD 5.7, 상속 포함).

    resolve_effective_permission 과 동일한 규칙(만료/inherit 필터 → 최근접 조상 → 동거리 최고
    수준)을 쓰되, 접근을 부여한 최근접 소스의 그룹명들을 함께 돌려준다(그룹 컬럼 표기용).
    반환: (유효 권한 수준, 부여 그룹명들). 없으면 (None, []).
    """
    applicable = [
        r
        for r in rows
        if (r.expires_at is None or r.expires_at > now)
        and (r.depth == 0 or r.inherit_to_children)
    ]
    if not applicable:
        return None, []
    min_depth = min(r.depth for r in applicable)
    at_source = [r for r in applicable if r.depth == min_depth]
    best = _highest(GroupPermission(r.permission) for r in at_source)
    return best, _dedup(r.group_name for r in at_source)


# 조상 경로에서 사용자 소속 그룹의 권한 행을 그룹명과 함께 모은다 (리스팅 상속 폴백용).
_ANCESTOR_USER_GRANT_SQL = text(
    """
    WITH RECURSIVE ancestors AS (
        SELECT id, parent_folder_id, 0 AS depth
        FROM files WHERE id = :file_id
        UNION ALL
        SELECT f.id, f.parent_folder_id, a.depth + 1
        FROM files f JOIN ancestors a ON f.id = a.parent_folder_id
    )
    SELECT a.depth AS depth, p.file_id AS file_id, p.group_id AS group_id,
           g.name AS group_name, p.permission AS permission,
           p.inherit_to_children AS inherit_to_children, p.expires_at AS expires_at
    FROM ancestors a
    JOIN file_group_permissions p ON p.file_id = a.id
    JOIN groups g ON g.id = p.group_id
    WHERE p.group_id IN :group_ids
    """
).bindparams(bindparam("group_ids", expanding=True))


async def get_effective_grant(
    session: AsyncSession, user: User, file: File, group_ids: list[int] | None = None
) -> tuple[GroupPermission | None, list[str]]:
    """상속 포함 유효 그룹 권한 수준 + 부여 그룹명 (리스팅 상속 폴백 — 항목당 1 쿼리).

    group_ids 를 넘기면 재조회를 아낀다(리스팅 배치에서 한 번만 조회해 재사용).
    """
    if group_ids is None:
        group_ids = await get_user_group_ids(session, user.id)
    if not group_ids:
        return None, []
    result = await session.execute(
        _ANCESTOR_USER_GRANT_SQL, {"file_id": file.id, "group_ids": group_ids}
    )
    rows = [
        AncestorGrantRow(
            depth=r.depth,
            file_id=r.file_id,
            group_id=r.group_id,
            group_name=r.group_name,
            permission=r.permission,
            inherit_to_children=r.inherit_to_children,
            expires_at=r.expires_at,
        )
        for r in result
    ]
    return resolve_effective_grant(rows, datetime.now(UTC))


# --- CTE 판정 (조상 소유 + 사용자 소속 그룹 기준) -----------------------------

# 실제 그룹 id 는 양수이므로, 소속 그룹이 없을 때 "아무 행에도 매칭되지 않는" 자리표시자로 쓴다.
# (expanding bindparam 에 빈 리스트를 넘기지 않기 위한 것 — 조상 경로 자체는 여전히 필요하다.)
_NO_GROUP = -1

# 대상 파일에서 루트까지 조상 경로를 훑어 (a) 각 노드의 소유자와 (b) 사용자 소속 그룹의 권한 행을
# 함께 모은다. depth 0 = 대상 파일, 상위로 갈수록 +1. 권한 행이 하나도 없어도 조상 소유 판정을
# 하려면 경로 자체가 필요하므로 권한 테이블은 LEFT JOIN 이다.
# inherit/만료 필터는 순수 함수에서 처리한다.
_ANCESTOR_ACCESS_SQL = text(
    """
    WITH RECURSIVE ancestors AS (
        SELECT id, parent_folder_id, user_id, 0 AS depth
        FROM files WHERE id = :file_id
        UNION ALL
        SELECT f.id, f.parent_folder_id, f.user_id, a.depth + 1
        FROM files f JOIN ancestors a ON f.id = a.parent_folder_id
    )
    SELECT a.depth AS depth, a.id AS node_id, a.user_id AS node_user_id,
           p.file_id AS file_id, p.permission AS permission,
           p.inherit_to_children AS inherit_to_children, p.expires_at AS expires_at
    FROM ancestors a
    LEFT JOIN file_group_permissions p
           ON p.file_id = a.id AND p.group_id IN :group_ids
    """
).bindparams(bindparam("group_ids", expanding=True))


@dataclass(frozen=True)
class ResolvedAccess:
    """소유자 본인을 제외한 유효 접근 판정 결과.

    by_ancestor_owner=True 면 그룹 부여가 아니라 **내가 소유한 상위 폴더**를 통해 얻은 접근이다.
    """

    level: GroupPermission | None
    source_file_id: int | None
    by_ancestor_owner: bool


async def _determine_access(
    session: AsyncSession, user: User, file: File
) -> ResolvedAccess:
    """사용자의 유효 접근 수준을 DB 로 판정한다 (파일 자체의 소유 여부는 호출자가 먼저 처리).

    두 경로를 함께 본다:
      1) **조상 폴더 소유** — 내 폴더 안에 협업자가 만든 항목은 내 소유 경로 아래에 있으므로
         소유자에 준하는 전권(manage)을 갖는다. 폴더를 소유한다는 것이 그 안의 항목에 대한
         상위 권한이므로, 하위에 부여된(더 낮은) 그룹 권한이 이를 끌어내리지 않는다 —
         즉 최근접 조상 규칙의 예외이자 하한선이다.
      2) **그룹 권한** — 최근접 조상 규칙(resolve_effective_permission).
    조상 소유가 성립하면 manage 가 최고 수준이므로 그대로 채택하고, 아니면 그룹 판정을 쓴다.
    """
    group_ids = await get_user_group_ids(session, user.id)
    rows = (
        await session.execute(
            _ANCESTOR_ACCESS_SQL,
            {"file_id": file.id, "group_ids": group_ids or [_NO_GROUP]},
        )
    ).all()

    # LEFT JOIN 이라 한 노드가 권한 행 수만큼 반복된다 — 경로와 권한 행을 나눠 모은다.
    chain: dict[int, tuple[int, int]] = {}  # depth -> (node_id, node_user_id)
    perm_rows: list[AncestorPermRow] = []
    for r in rows:
        chain[r.depth] = (r.node_id, r.node_user_id)
        if r.file_id is not None:
            perm_rows.append(
                AncestorPermRow(
                    depth=r.depth,
                    file_id=r.file_id,
                    permission=r.permission,
                    inherit_to_children=r.inherit_to_children,
                    expires_at=r.expires_at,
                )
            )

    owner_depths = [d for d, (_, uid) in chain.items() if d > 0 and uid == user.id]
    if owner_depths:
        nearest = min(owner_depths)
        return ResolvedAccess(GroupPermission.MANAGE, chain[nearest][0], True)

    level, source = resolve_effective_permission(perm_rows, datetime.now(UTC))
    return ResolvedAccess(level, source, False)


# 여러 파일에 대해 "내가 소유한 폴더의 하위인가"를 한 번에 판정한다 (목록 배치용).
# 각 대상 파일(origin)마다 조상 경로를 따라 올라가며 내 소유 노드를 만나는지 본다.
_ANCESTOR_OWNED_SQL = text(
    """
    WITH RECURSIVE chain AS (
        SELECT id AS origin, parent_folder_id, user_id, 0 AS depth
        FROM files WHERE id IN :file_ids
        UNION ALL
        SELECT c.origin, f.parent_folder_id, f.user_id, c.depth + 1
        FROM files f JOIN chain c ON f.id = c.parent_folder_id
    )
    SELECT DISTINCT origin FROM chain WHERE depth > 0 AND user_id = :user_id
    """
).bindparams(bindparam("file_ids", expanding=True))


async def ancestor_owned_file_ids(
    session: AsyncSession, user_id: int, file_ids: Iterable[int]
) -> set[int]:
    """주어진 파일들 중 **내가 소유한 폴더의 하위**인 것들의 id (쿼리 1회).

    목록 응답의 권한 컬럼을 채울 때 항목당 판정(N+1) 없이 조상 소유를 반영하기 위한 배치 헬퍼다.
    """
    ids = list(file_ids)
    if not ids:
        return set()
    rows = await session.execute(
        _ANCESTOR_OWNED_SQL, {"file_ids": ids, "user_id": user_id}
    )
    return {r.origin for r in rows}


# --- Redis 캐시 (사용자별 세대 카운터 기반 무효화) ----------------------------

_CACHE_TTL_SECONDS = 300


def _ugen_key(user_id: int) -> str:
    return f"perm:ugen:{user_id}"


def _cache_key(generation: int, user_id: int, file_id: int) -> str:
    return f"perm:{generation}:{user_id}:{file_id}"


async def _user_generation(user_id: int) -> int:
    try:
        value = await redis_client.get(_ugen_key(user_id))
        return int(value) if value is not None else 0
    except Exception:  # noqa: BLE001 - 캐시 장애 시 세대 0 으로 취급(항상 DB 판정)
        return 0


async def get_access_level(
    session: AsyncSession, user: User, file: File
) -> GroupPermission | None:
    """파일 자체의 소유를 제외한 유효 접근 수준(조상 폴더 소유 + 그룹 권한, 캐시 적용).

    ensure_file_access 의 핫패스가 사용한다. 캐시에는 수준 문자열(read/write/manage) 또는
    'none' 을 저장한다.
    """
    generation = await _user_generation(user.id)
    key = _cache_key(generation, user.id, file.id)
    try:
        cached = await redis_client.get(key)
    except Exception:  # noqa: BLE001
        cached = None
    if cached is not None:
        cached = cached.decode() if isinstance(cached, bytes) else cached
        return None if cached == "none" else GroupPermission(cached)

    level = (await _determine_access(session, user, file)).level
    try:
        await redis_client.set(
            key, level.value if level is not None else "none", ex=_CACHE_TTL_SECONDS
        )
    except Exception:  # noqa: BLE001
        pass
    return level


async def _active_member_ids(session: AsyncSession, group_id: int) -> list[int]:
    rows = (
        await session.execute(
            select(GroupMember.user_id).where(
                GroupMember.group_id == group_id,
                GroupMember.removed_at.is_(None),
            )
        )
    ).scalars().all()
    return list(rows)


async def invalidate_users(user_ids: Iterable[int]) -> None:
    """해당 사용자들의 권한 캐시 세대를 올려 일괄 무효화한다."""
    try:
        for uid in set(user_ids):
            await redis_client.incr(_ugen_key(uid))
    except Exception:  # noqa: BLE001 - 무효화 실패는 짧은 TTL 로 자연 만료됨
        pass


async def invalidate_group_members(session: AsyncSession, group_id: int) -> None:
    """그룹 활성 멤버 전원의 권한 캐시를 무효화한다 (권한 부여/수정/회수/그룹 삭제 시)."""
    await invalidate_users(await _active_member_ids(session, group_id))


# --- 권한 관리 인가 헬퍼 -----------------------------------------------------


async def _require_manage(session: AsyncSession, actor: User, file: File) -> None:
    """권한 관리(부여/수정/회수)는 소유자(상위 폴더 소유 포함) 또는 manage 권한자만.

    아예 접근 불가면 404(존재 은닉), 조회는 되지만 manage 부족이면 403.
    """
    if file.user_id == actor.id:
        return
    level = (await _determine_access(session, actor, file)).level
    if level is None:
        raise PermissionServiceError(404, "파일을 찾을 수 없습니다.")
    if _RANK[level] < _RANK[GroupPermission.MANAGE]:
        raise PermissionServiceError(403, "권한을 관리할 수 있는 수준이 아닙니다.")


async def _get_file(session: AsyncSession, file_id: int) -> File:
    file = await session.get(File, file_id)
    if file is None or file.is_deleted:
        raise PermissionServiceError(404, "파일을 찾을 수 없습니다.")
    return file


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
            target_type="file",
            target_id=target_id,
            detail=detail,
        )
    )


# --- 권한 부여 / 수정 / 회수 (PRD 6.5) ---------------------------------------


async def grant_permission(
    session: AsyncSession,
    actor: User,
    file_id: int,
    group_id: int,
    permission: GroupPermission,
    inherit_to_children: bool,
    expires_at: datetime | None,
) -> FileGroupPermission:
    """파일/폴더에 그룹 권한을 부여한다. (file, group) 중복은 upsert(수정)로 처리.

    소유자 또는 manage 권한자만 가능. 대상 그룹은 활성이어야 한다.
    """
    file = await _get_file(session, file_id)
    await _require_manage(session, actor, file)

    group = await session.get(Group, group_id)
    if group is None or not group.is_active:
        raise PermissionServiceError(404, "그룹을 찾을 수 없습니다.")

    stmt = pg_insert(FileGroupPermission).values(
        file_id=file_id,
        group_id=group_id,
        permission=permission.value,
        inherit_to_children=inherit_to_children,
        expires_at=expires_at,
        granted_by=actor.id,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_file_group_permissions_file_group",
        set_={
            "permission": stmt.excluded.permission,
            "inherit_to_children": stmt.excluded.inherit_to_children,
            "expires_at": stmt.excluded.expires_at,
            "granted_by": stmt.excluded.granted_by,
            "granted_at": func.now(),
        },
    )
    await session.execute(stmt)
    _record_audit(
        session,
        actor.id,
        "permission.grant",
        file_id,
        {
            "group_id": group_id,
            "permission": permission.value,
            "inherit_to_children": inherit_to_children,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    await invalidate_group_members(session, group_id)
    await session.commit()
    await _publish_permission_event(file, actor.id)
    return await _get_direct_permission(session, file_id, group_id)


async def update_permission(
    session: AsyncSession,
    actor: User,
    file_id: int,
    group_id: int,
    permission: GroupPermission | None,
    inherit_to_children: bool | None,
    expires_at: datetime | None,
    expires_at_set: bool,
) -> FileGroupPermission:
    """직접 부여된 그룹 권한을 수정한다 (permission/inherit_to_children/expires_at)."""
    file = await _get_file(session, file_id)
    await _require_manage(session, actor, file)

    row = await _get_direct_permission(session, file_id, group_id, required=True)

    changes: dict[str, Any] = {}
    if permission is not None and row.permission != permission.value:
        changes["permission"] = {"from": row.permission, "to": permission.value}
        row.permission = permission.value
    if inherit_to_children is not None and row.inherit_to_children != inherit_to_children:
        changes["inherit_to_children"] = {
            "from": row.inherit_to_children,
            "to": inherit_to_children,
        }
        row.inherit_to_children = inherit_to_children
    if expires_at_set and row.expires_at != expires_at:
        changes["expires_at"] = {
            "from": row.expires_at.isoformat() if row.expires_at else None,
            "to": expires_at.isoformat() if expires_at else None,
        }
        row.expires_at = expires_at

    if changes:
        _record_audit(session, actor.id, "permission.update", file_id,
                      {"group_id": group_id, **changes})
        await invalidate_group_members(session, group_id)
    await session.commit()
    await session.refresh(row)
    return row


async def revoke_permission(
    session: AsyncSession, actor: User, file_id: int, group_id: int
) -> None:
    """직접 부여된 그룹 권한을 회수한다. 없는 권한이면 404."""
    file = await _get_file(session, file_id)
    await _require_manage(session, actor, file)

    row = await _get_direct_permission(session, file_id, group_id, required=True)
    await session.delete(row)
    _record_audit(
        session, actor.id, "permission.revoke", file_id, {"group_id": group_id}
    )
    await invalidate_group_members(session, group_id)
    await session.commit()
    await _publish_permission_event(file, actor.id)


async def _publish_permission_event(file: File, actor_id: int) -> None:
    """권한 부여/회수 실시간 이벤트 발행 (Phase 8-1, fail-open).

    file_events 가 이 모듈(permissions)을 임포트하므로 순환을 피하려 지연 임포트한다.
    회수 시 접근권을 잃은 구독자는 구독 필터에서 걸러져 이벤트를 못 받는데, 이는 설계상
    수용된 한계다(재조회/이동 시점에 404 로 자연 정리된다).
    """
    from app.services import file_events as file_events_service

    await file_events_service.publish_file_event(
        type="permission",
        file_id=file.id,
        parent_folder_id=file.parent_folder_id,
        actor_id=actor_id,
        name=file.name,
    )


async def _get_direct_permission(
    session: AsyncSession, file_id: int, group_id: int, required: bool = False
) -> FileGroupPermission:
    row = (
        await session.execute(
            select(FileGroupPermission).where(
                FileGroupPermission.file_id == file_id,
                FileGroupPermission.group_id == group_id,
            )
        )
    ).scalar_one_or_none()
    if row is None and required:
        raise PermissionServiceError(404, "부여된 그룹 권한이 없습니다.")
    return row  # type: ignore[return-value]


# --- 권한 목록 / 상속 조회 (PRD 6.5/6.6) -------------------------------------


@dataclass(frozen=True)
class DirectGrant:
    group_id: int
    group_name: str
    permission: str
    inherit_to_children: bool
    granted_at: datetime
    expires_at: datetime | None
    granted_by: int


@dataclass(frozen=True)
class InheritedGrant:
    group_id: int
    group_name: str
    permission: str
    source_file_id: int
    source_file_name: str
    depth: int
    expires_at: datetime | None


# 조상(depth>0)의 상속 가능한(inherit_to_children) 권한 행을 그룹/파일명과 함께 모은다.
_ANCESTOR_ALL_PERM_SQL = text(
    """
    WITH RECURSIVE ancestors AS (
        SELECT id, parent_folder_id, 0 AS depth
        FROM files WHERE id = :file_id
        UNION ALL
        SELECT f.id, f.parent_folder_id, a.depth + 1
        FROM files f JOIN ancestors a ON f.id = a.parent_folder_id
    )
    SELECT a.depth AS depth, p.file_id AS source_file_id, sf.name AS source_file_name,
           p.group_id AS group_id, g.name AS group_name, p.permission AS permission,
           p.inherit_to_children AS inherit_to_children, p.expires_at AS expires_at
    FROM ancestors a
    JOIN file_group_permissions p ON p.file_id = a.id
    JOIN groups g ON g.id = p.group_id
    JOIN files sf ON sf.id = p.file_id
    WHERE a.depth > 0
    ORDER BY p.group_id ASC, a.depth ASC
    """
)


async def list_permissions(
    session: AsyncSession, actor: User, file_id: int
) -> tuple[list[DirectGrant], list[InheritedGrant]]:
    """직접 부여 목록 + 유효 상속 권한 목록을 반환한다 (PRD 6.5/6.6 통합).

    상속 목록은 그룹별로 가장 가까운 상속 조상(inherit_to_children=TRUE, 미만료) 한 건을 담되,
    이 파일에 같은 그룹의 직접 부여가 있으면(재정의) 제외한다.
    """
    file = await _get_file(session, file_id)
    await _require_manage(session, actor, file)

    direct_rows = (
        await session.execute(
            select(FileGroupPermission, Group.name)
            .join(Group, Group.id == FileGroupPermission.group_id)
            .where(FileGroupPermission.file_id == file_id)
            .order_by(FileGroupPermission.group_id.asc())
        )
    ).all()
    direct = [
        DirectGrant(
            group_id=p.group_id,
            group_name=name,
            permission=p.permission,
            inherit_to_children=p.inherit_to_children,
            granted_at=p.granted_at,
            expires_at=p.expires_at,
            granted_by=p.granted_by,
        )
        for p, name in direct_rows
    ]
    direct_group_ids = {d.group_id for d in direct}

    now = datetime.now(UTC)
    ancestor_rows = (
        await session.execute(_ANCESTOR_ALL_PERM_SQL, {"file_id": file_id})
    ).all()
    # 그룹별 최근접 상속 권한 한 건 (미만료 + inherit_to_children). ORDER BY depth ASC 이므로 첫 건.
    inherited_by_group: dict[int, InheritedGrant] = {}
    for r in ancestor_rows:
        if r.group_id in direct_group_ids or r.group_id in inherited_by_group:
            continue
        if not r.inherit_to_children:
            continue
        if r.expires_at is not None and r.expires_at <= now:
            continue
        inherited_by_group[r.group_id] = InheritedGrant(
            group_id=r.group_id,
            group_name=r.group_name,
            permission=r.permission,
            source_file_id=r.source_file_id,
            source_file_name=r.source_file_name,
            depth=r.depth,
            expires_at=r.expires_at,
        )
    return direct, list(inherited_by_group.values())


async def list_inherited(
    session: AsyncSession, actor: User, file_id: int
) -> list[InheritedGrant]:
    """상속된 유효 권한만 조회 (PRD 6.6 /inherited). manage 권한자용 디버깅/관리 뷰."""
    _, inherited = await list_permissions(session, actor, file_id)
    return inherited


# --- 내 유효 권한 조회 (PRD 6.6 check) ---------------------------------------


@dataclass(frozen=True)
class EffectivePermission:
    permission: str  # read / write / manage / none
    via: str  # owner / group / none
    source_file_id: int | None


async def check_permission(
    session: AsyncSession, user: User, file_id: int
) -> EffectivePermission:
    """현재 사용자의 파일 유효 권한 (PRD 6.6). 존재하지 않으면 404.

    소유자(생성자)는 manage(전권). 내가 소유한 폴더의 하위 항목도 소유 경로로 manage 이며,
    이 경우 via="owner" 에 source_file_id 로 그 폴더 id 가 실린다(파일 자체 소유는 None).
    그 외에는 그룹 권한으로 판정한다. 시스템 admin 은 파일 내용 접근 권한이 없으므로
    (PRD 3.6.4) via=admin 은 발생하지 않는다.
    """
    file = await _get_file(session, file_id)
    if file.user_id == user.id:
        return EffectivePermission(permission="manage", via="owner", source_file_id=None)

    access = await _determine_access(session, user, file)
    if access.level is None:
        return EffectivePermission(permission="none", via="none", source_file_id=None)
    return EffectivePermission(
        permission=access.level.value,
        via="owner" if access.by_ancestor_owner else "group",
        source_file_id=access.source_file_id,
    )


# --- 공유된 항목 목록 (shared-with-me) ---------------------------------------


@dataclass(frozen=True)
class SharedItem:
    file: File
    group_id: int
    group_name: str
    permission: str


async def list_shared_with_me(session: AsyncSession, user: User) -> list[SharedItem]:
    """내 소속 그룹에 부여된 권한의 대상 파일/폴더(부여 지점) 목록 (PRD 3.1.3 진입점).

    만료 행/삭제 파일은 제외하고, 본인 소유 파일은 제외한다(타인이 공유한 항목만).
    """
    group_ids = await get_user_group_ids(session, user.id)
    if not group_ids:
        return []

    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(File, FileGroupPermission, Group.name)
            .join(FileGroupPermission, FileGroupPermission.file_id == File.id)
            .join(Group, Group.id == FileGroupPermission.group_id)
            .where(
                FileGroupPermission.group_id.in_(group_ids),
                File.is_deleted.is_(False),
                File.user_id != user.id,
                (FileGroupPermission.expires_at.is_(None))
                | (FileGroupPermission.expires_at > now),
            )
            .order_by(File.is_folder.desc(), File.name.asc())
        )
    ).all()
    return [
        SharedItem(
            file=file,
            group_id=perm.group_id,
            group_name=name,
            permission=perm.permission,
        )
        for file, perm, name in rows
    ]
