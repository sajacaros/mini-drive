"""파일/폴더 그룹 권한 부여 + 상속 판정 서비스 (PRD 3.1.3, 5.7, 6.5/6.6).

설계 (PRD 5.7 — 물질화하지 않고 조회 시 판정):
  file_group_permissions 에는 **명시적 부여만** 저장한다. 판정 시 대상 파일에서 루트까지
  조상 경로를 recursive CTE 로 올라가며, 사용자 소속 그룹들의 권한 행 **전체에서 최고
  수준**(manage > write > read)을 적용한다 — 누적(union). 거리는 수준 결정에 관여하지 않고,
  동수준일 때 출처 표기에만 쓴다. 조상 행이라도 inherit_to_children=FALSE 면 하위(자기 자신
  제외)에 효력이 없고, expires_at 이 지난 행은 무효다.

  누적으로 바꾼 이유 (2026-07-28) — 종전 '가장 가까운 조상이 이긴다'는 재정의로 권한을
  낮추기 위한 규칙이었으나, 권한 행이 사용자 소속 그룹 전체에 대해 수집되므로 재정의가
  그룹을 가로질러 작동했다. 그룹B 에 read 를 준 사람이 의도 없이 그룹A 관리자의 manage 를
  깎는 간섭이 생긴다. 누적에서는 부여된 적 없는 권한이 생기지 않으며(최고 수준도 누군가
  명시적으로 부여한 것이다), '상속받되 여기서만 낮추기'는 표현할 수 없어져 상위에서
  inherit_to_children=FALSE 로 상속을 끊고 하위에 개별 부여하는 방식으로 대체한다.

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
from typing import Any, Literal, cast

from sqlalchemy import CursorResult, bindparam, delete, func, select, text
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
from app.models.enums import GroupPermission, UserStatus
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
    - 남은 행 **전체에서 최고 수준**을 택한다(누적). 동수준이면 가까운 쪽을 출처로 삼는다.

    누적(union)인 이유 — 2026-07-28 변경. 종전 규칙은 '가장 가까운 조상이 이긴다'였고,
    상위 폴더의 manage 가 하위의 read 에 덮이는 재정의를 의도한 것이었다. 그런데 권한 행은
    **사용자가 속한 모든 그룹**에 대해 수집되므로(`_ANCESTOR_USER_GRANT_SQL`), 재정의가
    같은 그룹 안이 아니라 **그룹을 가로질러** 작동했다. 그룹B 에 read 를 준 사람이 의도 없이
    그룹A 관리자의 manage 를 깎는 간섭이 생긴다.

    누적으로 바꿔도 없던 권한이 생기지는 않는다 — 올라가 봐야 manage 권한자가 이미 명시적으로
    부여해 둔 수준까지다. 대신 '상속받되 여기서만 낮추기'는 표현할 수 없어지고, 좁히려면
    상위에서 inherit_to_children=FALSE 로 상속을 끊고 하위에 개별 부여한다.

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

    best = max(applicable, key=lambda r: (_RANK[GroupPermission(r.permission)], -r.depth))
    return GroupPermission(best.permission), best.file_id


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
    # 상속 목록 표시(select_inherited_grants)에만 쓴다 — 판정 경로는 출처 이름이 필요 없다.
    source_file_name: str = ""


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

    resolve_effective_permission 과 동일한 규칙(만료/inherit 필터 → 전체에서 최고 수준)을 쓰되,
    **접근을 부여한 그룹명 전부**를 함께 돌려준다(그룹 컬럼 표기용 — 수준과 무관하게, 이 파일에
    나를 접근하게 해주는 그룹들).
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
    best = _highest(GroupPermission(r.permission) for r in applicable)
    return best, _dedup(r.group_name for r in applicable)


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
         즉 그룹 판정과 무관한 하한선이다.
      2) **그룹 권한** — 누적 규칙(resolve_effective_permission).
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


async def can_access_descendants(
    session: AsyncSession, user: User, folder: File
) -> bool:
    """폴더 **하위 항목까지** 접근이 미치는지 (폴더 ZIP 다운로드용).

    폴더를 읽을 수 있다고 그 안까지 볼 수 있는 것은 아니다 — 그룹 부여가
    inherit_to_children=FALSE 면 그 폴더 한 칸에만 적용된다(PRD 5.7). 하위 판정은
    "상속되는 근거가 하나라도 있는가"면 충분하다:
      - 폴더 자신이나 조상을 내가 소유하면 하위 전체가 내 소유 경로 아래다(조상 소유 규칙).
      - 그룹 부여는 미만료 + inherit_to_children 인 행이 하나라도 있으면 하위로 이어진다.
        (하위에서는 그 행의 depth 가 하나 더 깊어질 뿐 판정 규칙은 같다.)
    반대로 하위에서 권한이 **낮아지는** 경우는 없다 — 누적 규칙에서는 하위의 부여가 상위를
    깎지 못하고, 어떤 수준이든 read 는 포함하므로 접근이 끊기지 않는다.
    """
    if folder.user_id == user.id:
        return True
    group_ids = await get_user_group_ids(session, user.id)
    rows = (
        await session.execute(
            _ANCESTOR_ACCESS_SQL,
            {"file_id": folder.id, "group_ids": group_ids or [_NO_GROUP]},
        )
    ).all()
    now = datetime.now(UTC)
    for r in rows:
        if r.depth > 0 and r.node_user_id == user.id:
            return True
        if r.file_id is None or not r.inherit_to_children:
            continue
        if r.expires_at is None or r.expires_at > now:
            return True
    return False


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
    """그룹의 활성 멤버 id 목록 (권한 캐시 무효화 대상).

    시스템 그룹(`@전사`)은 멤버십을 물질화하지 않으므로 group_members 에 행이 없다
    (get_user_group_ids 가 UNION 으로 붙인다). 그대로 두면 전사 공개를 켜고 꺼도 아무의
    캐시도 무효화되지 않아 최대 TTL 동안 반영이 지연된다 — 활성 사용자 전원을 대상으로 삼는다.
    """
    group = await session.get(Group, group_id)
    if group is not None and group.is_system:
        rows = (
            await session.execute(
                select(User.id).where(User.status == UserStatus.ACTIVE)
            )
        ).scalars().all()
        return list(rows)

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


# 주어진 폴더들의 조상 경로에서 (a) 각 노드의 소유자와 (b) 그 노드에 권한이 부여된 그룹을 모은다.
# 이동으로 조상 경로가 바뀌면 이 두 집합이 상속 판정 결과가 달라질 수 있는 사용자 전부다.
_PATH_STAKEHOLDERS_SQL = text(
    """
    WITH RECURSIVE ancestors AS (
        SELECT id, parent_folder_id, user_id FROM files WHERE id IN :file_ids
        UNION ALL
        SELECT f.id, f.parent_folder_id, f.user_id
        FROM files f JOIN ancestors a ON f.id = a.parent_folder_id
    )
    SELECT DISTINCT a.user_id AS user_id, p.group_id AS group_id
    FROM ancestors a
    LEFT JOIN file_group_permissions p ON p.file_id = a.id
    """
).bindparams(bindparam("file_ids", expanding=True))


async def invalidate_path_stakeholders(
    session: AsyncSession, folder_ids: Iterable[int | None]
) -> None:
    """주어진 폴더들의 조상 경로에 이해관계가 있는 사용자의 권한 캐시를 무효화한다.

    항목이 폴더 사이를 이동하면 상속 경로가 통째로 바뀐다. 영향을 받는 사람은 두 부류다 —
    경로상의 폴더 **소유자**(조상 소유로 manage 를 얻거나 잃는다)와 경로상의 폴더에 권한이
    부여된 **그룹의 멤버**(상속 권한을 얻거나 잃는다). 경로 깊이만큼만 훑으므로 비용이 작다.
    """
    ids = [fid for fid in folder_ids if fid is not None]
    if not ids:
        return
    rows = (await session.execute(_PATH_STAKEHOLDERS_SQL, {"file_ids": ids})).all()

    user_ids = {r.user_id for r in rows}
    for group_id in {r.group_id for r in rows if r.group_id is not None}:
        user_ids.update(await _active_member_ids(session, group_id))
    await invalidate_users(user_ids)


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
    상속받은 수준보다 낮은 부여는 409 로 거부한다(`narrowing_conflict`).
    """
    file = await _get_file(session, file_id)
    await _require_manage(session, actor, file)

    group = await session.get(Group, group_id)
    if group is None or not group.is_active:
        raise PermissionServiceError(404, "그룹을 찾을 수 없습니다.")

    await _reject_narrowing(session, file_id, group_id, permission)

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


async def grant_permission_bulk(
    session: AsyncSession,
    actor: User,
    file_ids: list[int],
    group_id: int,
    permission: GroupPermission,
    *,
    audit_detail: dict[str, Any] | None = None,
) -> int:
    """여러 파일에 같은 그룹 권한을 한 번에 부여한다. 반환: 부여한 개수.

    위키 폴더 발행 전용이다(`services/wiki.py`). `grant_permission` 을 파일마다 부르면
    커밋·캐시 무효화·SSE 발행이 **파일 수만큼** 일어난다. 폴더 하나에 수백 건이 걸리므로
    왕복을 한 번으로 접는다 — 감사 로그도 건별이 아니라 묶음 한 줄이다.

    **권한 판정은 호출자 책임이다.** `_require_manage` 를 부르지 않는다. 유일한 호출자인
    위키가 `folder_scope` 에서 **파일마다** 발행 권한을 이미 판정하고(그 판정이 폴더가 아니라
    파일 단위여야 하는 이유는 spec/wiki-index.md 「폴더 발행 범위」), 통과한 것만 여기 넘긴다.
    다른 곳에서 쓰려면 그 판정을 먼저 하거나 `grant_permission` 을 써라.

    `inherit_to_children` 은 항상 FALSE 다. 위키는 대상 파일에만 공개를 걸고 폴더에는 걸지
    않는다 — 폴더에 상속 부여를 하면 인덱싱 대상이 아닌 PDF·이미지까지 열리고, 소유자가
    파일에서 위키를 꺼도 공개가 상속으로 남는다(spec 「공개는 폴더가 아니라 대상 파일에 건다」).
    """
    if not file_ids:
        return 0

    group = await session.get(Group, group_id)
    if group is None or not group.is_active:
        raise PermissionServiceError(404, "그룹을 찾을 수 없습니다.")

    stmt = pg_insert(FileGroupPermission).values(
        [
            {
                "file_id": fid,
                "group_id": group_id,
                "permission": permission.value,
                "inherit_to_children": False,
                "expires_at": None,
                "granted_by": actor.id,
            }
            for fid in file_ids
        ]
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
        "permission.grant_bulk",
        file_ids[0],
        {
            "group_id": group_id,
            "permission": permission.value,
            "file_count": len(file_ids),
            **(audit_detail or {}),
        },
    )
    await invalidate_group_members(session, group_id)
    await session.commit()
    return len(file_ids)


async def revoke_permission_bulk(
    session: AsyncSession,
    actor: User,
    file_ids: list[int],
    group_id: int,
    *,
    audit_detail: dict[str, Any] | None = None,
) -> int:
    """여러 파일에서 같은 그룹 권한을 한 번에 회수한다. 반환: 실제로 지운 개수.

    `grant_permission_bulk` 의 짝이다. 없는 부여가 섞여 있어도 오류가 아니다 — 위키를 끄는
    경로는 "공개였는지" 를 따지지 않고 부르므로, 없으면 지울 것이 없는 것으로 본다.
    권한 판정은 같은 이유로 호출자 책임이다.
    """
    if not file_ids:
        return 0

    result = cast(
        "CursorResult[Any]",
        await session.execute(
            delete(FileGroupPermission).where(
                FileGroupPermission.file_id.in_(file_ids),
                FileGroupPermission.group_id == group_id,
            )
        ),
    )
    deleted = result.rowcount or 0
    if deleted:
        _record_audit(
            session,
            actor.id,
            "permission.revoke_bulk",
            file_ids[0],
            {
                "group_id": group_id,
                "file_count": deleted,
                **(audit_detail or {}),
            },
        )
    await invalidate_group_members(session, group_id)
    await session.commit()
    return deleted


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
    """직접 부여된 그룹 권한을 수정한다 (permission/inherit_to_children/expires_at).

    부여와 같은 정책 — 상속받은 수준보다 낮추려 하면 409 로 거부한다(`narrowing_conflict`).
    """
    file = await _get_file(session, file_id)
    await _require_manage(session, actor, file)

    row = await _get_direct_permission(session, file_id, group_id, required=True)
    if permission is not None:
        await _reject_narrowing(session, file_id, group_id, permission)

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


def select_inherited_grants(
    rows: Iterable[AncestorGrantRow], now: datetime
) -> list[InheritedGrant]:
    """조상 권한 행들에서 그룹별 상속 권한을 한 건씩 고른다 (화면 표시용, PRD 6.5/6.6).

    - 자기 자신(depth 0)·비상속(inherit_to_children=FALSE)·만료 행은 제외.
    - 그룹별로 **가장 높은 수준**을 남긴다. 동수준이면 가까운 조상(depth 작은 쪽).

    '가장 가까운 조상' 이 아니라 '가장 높은 수준' 인 이유는 판정 규칙과 맞추기 위해서다 —
    유효 권한은 누적(resolve_effective_permission)이라 조부모의 manage 가 부모의 read 에
    덮이지 않는다. 최근접을 고르면 그 경우 화면이 read 를 표시해 **유효 권한보다 낮게** 읽힌다.
    프런트가 "상속보다 낮게는 못 낮춘다"를 경고하는 근거로 이 값을 쓰므로(PermissionModal),
    낮게 표시되면 경고가 새는 방향으로 틀린다.
    """
    best: dict[int, AncestorGrantRow] = {}
    for r in rows:
        if r.depth == 0 or not r.inherit_to_children:
            continue
        if r.expires_at is not None and r.expires_at <= now:
            continue
        kept = best.get(r.group_id)
        if kept is None or (_RANK[GroupPermission(r.permission)], -r.depth) > (
            _RANK[GroupPermission(kept.permission)],
            -kept.depth,
        ):
            best[r.group_id] = r
    return [
        InheritedGrant(
            group_id=r.group_id,
            group_name=r.group_name,
            permission=r.permission,
            source_file_id=r.file_id,
            source_file_name=r.source_file_name,
            depth=r.depth,
            expires_at=r.expires_at,
        )
        for r in best.values()
    ]


# 거부 사유 문구에 쓰는 한국어 수준명 (프런트 permissionLabel 과 같은 말).
_LEVEL_LABEL: dict[GroupPermission, str] = {
    GroupPermission.READ: "읽기",
    GroupPermission.WRITE: "쓰기",
    GroupPermission.MANAGE: "관리",
}


def narrowing_conflict(
    inherited: InheritedGrant | None, new_level: GroupPermission
) -> str | None:
    """상속보다 낮은 수준을 부여하려는 것인가 — 맞으면 사용자에게 보일 사유를 돌려준다.

    유효 권한은 누적이라(resolve_effective_permission) 상속보다 낮은 직접 부여는 유효 권한을
    **바꾸지 못한다.** 저장은 되고 목록에는 그 낮은 값이 보이므로, 허용하면 관리자는 좁혔다고
    믿는다 — 실패보다 나쁜 조용한 무효다. 그래서 저장 자체를 거부하고 이유를 말한다.

    좁히는 방법은 하나뿐이므로(상속 출처에서 '하위 상속' 끄기) 사유에 그 경로를 함께 적는다.
    반환: 거부 사유 문구. 문제 없으면 None.
    """
    if inherited is None:
        return None
    level = GroupPermission(inherited.permission)
    if _RANK[level] <= _RANK[new_level]:
        return None
    return (
        f"'{inherited.group_name}' 그룹은 '{inherited.source_file_name}'에서 "
        f"{_LEVEL_LABEL[level]} 권한을 상속받고 있어 이 항목만 "
        f"{_LEVEL_LABEL[new_level]}(으)로 낮출 수 없습니다. "
        f"좁히려면 '{inherited.source_file_name}'에서 하위 상속을 끄고 "
        f"필요한 항목에만 개별 부여하세요."
    )


async def _inherited_for_group(
    session: AsyncSession, file_id: int, group_id: int
) -> InheritedGrant | None:
    """그 파일이 특정 그룹에 대해 상속받고 있는 권한 (없으면 None). 낮춤 거부 판정용."""
    rows = (await session.execute(_ANCESTOR_ALL_PERM_SQL, {"file_id": file_id})).all()
    for grant in select_inherited_grants(
        [
            AncestorGrantRow(
                depth=r.depth,
                file_id=r.source_file_id,
                group_id=r.group_id,
                group_name=r.group_name,
                permission=r.permission,
                inherit_to_children=r.inherit_to_children,
                expires_at=r.expires_at,
                source_file_name=r.source_file_name,
            )
            for r in rows
        ],
        datetime.now(UTC),
    ):
        if grant.group_id == group_id:
            return grant
    return None


async def _reject_narrowing(
    session: AsyncSession, file_id: int, group_id: int, new_level: GroupPermission
) -> None:
    """상속보다 낮은 수준을 부여/수정하려 하면 409 로 막는다 (spec/permissions.md)."""
    conflict = narrowing_conflict(
        await _inherited_for_group(session, file_id, group_id), new_level
    )
    if conflict is not None:
        raise PermissionServiceError(409, conflict)


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

    상속 목록은 그룹별로 **가장 높은 수준**의 상속 조상(inherit_to_children=TRUE, 미만료)
    한 건을 담는다. 동수준이면 가까운 쪽이다.

    '가장 가까운 조상' 이 아니라 '가장 높은 수준' 인 이유는 판정 규칙과 맞추기 위해서다 —
    유효 권한은 누적(resolve_effective_permission)이라 조부모의 manage 가 부모의 read 에
    덮이지 않는다. 최근접을 고르면 그 경우 화면이 read 를 표시해 **유효 권한보다 낮게**
    읽힌다. 화면이 낮춤 불가를 경고하는 근거로 이 값을 쓰므로(프런트 PermissionModal),
    낮게 표시되면 경고 자체가 새는 방향으로 틀린다.

    같은 그룹의 직접 부여가 있어도 상속 항목을 **숨기지 않는다** — 판정이 누적으로 바뀌면서
    (resolve_effective_permission, 2026-07-28) 직접 부여가 상속을 취소하지 못하기 때문이다.
    직접 read + 상속 manage 면 유효 권한은 manage 인데, 상속 행을 감추면 관리자가 화면에서
    read 로 읽는다. 두 행을 모두 보여주고 실제 유효 수준은 둘 중 높은 쪽이다.
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

    ancestor_rows = (
        await session.execute(_ANCESTOR_ALL_PERM_SQL, {"file_id": file_id})
    ).all()
    inherited = select_inherited_grants(
        [
            AncestorGrantRow(
                depth=r.depth,
                file_id=r.source_file_id,
                group_id=r.group_id,
                group_name=r.group_name,
                permission=r.permission,
                inherit_to_children=r.inherit_to_children,
                expires_at=r.expires_at,
                source_file_name=r.source_file_name,
            )
            for r in ancestor_rows
        ],
        datetime.now(UTC),
    )
    return direct, inherited


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

    **시스템 그룹(`@전사`) 유래 부여는 제외한다** (spec/wiki-index.md 「프런트」). 위키를 켜면
    그 파일에 `@전사 read` 가 걸리고 `get_user_group_ids` 가 전 활성 사용자에게 그 그룹을
    붙이므로, 걸러내지 않으면 **전사 위키 문서 전체가 모든 사람의 "공유" 폴더에 쏟아진다**
    (실측 467건). 누가 나에게 공유한 항목을 찾으러 온 화면이 사내 문서 목록이 되어버린다.

    이건 **보안 필터가 아니라 UI 필터다** — 권한은 실제로 있고 파일도 열린다. 위키 문서를
    찾는 자리는 문서 카탈로그(`/wiki/catalog`)다. 그래서 '부여 지점'만 가린다.

    같은 파일이 `@전사` 와 실제 그룹으로 **함께** 공유됐다면 그 파일은 남는다 — 거르는 것은
    파일이 아니라 부여 행이고, 실제 그룹의 부여 행이 그대로 살아 있기 때문이다.
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
                # 이름이 아니라 `is_system` 으로 가른다 — 앞으로 자동 멤버십 그룹이 더 생기면
                # 같은 이유로 같은 처리가 필요하고, 이름 비교는 그때 조용히 새는 쪽이다.
                Group.is_system.is_(False),
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
