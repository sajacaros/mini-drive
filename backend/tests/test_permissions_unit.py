"""권한 상속 판정 순수 로직 단위 테스트 (DB/Redis 불필요) — PRD 5.7.

resolve_effective_permission 은 조상 경로에서 모은 권한 행으로 유효 권한을 계산한다.
검증 축: 최근접 조상 우선 / 동거리 최고 수준 / inherit_to_children=FALSE 조상 무시 /
만료 행 무시 / 자기 자신(depth 0)은 inherit 무관.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.enums import GroupPermission
from app.services.permissions import (
    AncestorGrantRow,
    AncestorPermRow,
    permission_covers,
    resolve_effective_grant,
    resolve_effective_permission,
    select_direct_grant,
)

NOW = datetime(2026, 7, 18, tzinfo=UTC)


def _row(
    depth: int,
    permission: str,
    *,
    file_id: int = 0,
    inherit: bool = True,
    expires_at: datetime | None = None,
) -> AncestorPermRow:
    return AncestorPermRow(
        depth=depth,
        file_id=file_id or (100 + depth),
        permission=permission,
        inherit_to_children=inherit,
        expires_at=expires_at,
    )


class TestClosestAncestorWins:
    def test_no_rows_returns_none(self) -> None:
        level, src = resolve_effective_permission([], NOW)
        assert level is None and src is None

    def test_self_depth_zero_applies(self) -> None:
        level, src = resolve_effective_permission([_row(0, "write", file_id=5)], NOW)
        assert level is GroupPermission.WRITE and src == 5

    def test_closer_ancestor_overrides_farther(self) -> None:
        # 부모(depth1)=read 가 조부(depth2)=manage 를 덮어쓴다 — 거리 우선(재정의).
        rows = [_row(2, "manage", file_id=20), _row(1, "read", file_id=10)]
        level, src = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.READ and src == 10

    def test_self_overrides_ancestor(self) -> None:
        rows = [_row(0, "read", file_id=1), _row(1, "manage", file_id=10)]
        level, src = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.READ and src == 1


class TestSameDistanceHighestLevel:
    def test_same_depth_takes_highest(self) -> None:
        # 같은 파일(같은 거리)에 여러 그룹 권한 — 최고 수준(manage) 적용.
        rows = [_row(1, "read", file_id=10), _row(1, "manage", file_id=10),
                _row(1, "write", file_id=10)]
        level, _ = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.MANAGE


class TestInheritFlag:
    def test_non_inheriting_ancestor_ignored(self) -> None:
        # 조상(depth1)의 inherit_to_children=FALSE → 하위에 효력 없음 → None.
        rows = [_row(1, "manage", file_id=10, inherit=False)]
        level, src = resolve_effective_permission(rows, NOW)
        assert level is None and src is None

    def test_non_inheriting_self_still_applies(self) -> None:
        # 자기 자신(depth0)은 inherit 플래그와 무관하게 적용된다.
        rows = [_row(0, "write", file_id=5, inherit=False)]
        level, _ = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.WRITE

    def test_non_inheriting_closer_falls_through_to_inheriting_farther(self) -> None:
        # 가까운 조상이 non-inherit 이면 무시되고, 더 먼 inherit 조상이 적용된다.
        rows = [_row(1, "manage", file_id=10, inherit=False),
                _row(2, "read", file_id=20, inherit=True)]
        level, src = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.READ and src == 20


class TestExpiry:
    def test_expired_row_ignored(self) -> None:
        past = NOW - timedelta(hours=1)
        rows = [_row(1, "manage", file_id=10, expires_at=past)]
        level, src = resolve_effective_permission(rows, NOW)
        assert level is None and src is None

    def test_future_expiry_valid(self) -> None:
        future = NOW + timedelta(hours=1)
        rows = [_row(1, "write", file_id=10, expires_at=future)]
        level, _ = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.WRITE

    def test_expired_closer_falls_through_to_valid_farther(self) -> None:
        past = NOW - timedelta(hours=1)
        rows = [_row(1, "manage", file_id=10, expires_at=past),
                _row(2, "read", file_id=20)]
        level, src = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.READ and src == 20


def _grant(
    depth: int,
    group_id: int,
    group_name: str,
    permission: str,
    *,
    file_id: int = 0,
    inherit: bool = True,
    expires_at: datetime | None = None,
) -> AncestorGrantRow:
    return AncestorGrantRow(
        depth=depth,
        file_id=file_id or (100 + depth),
        group_id=group_id,
        group_name=group_name,
        permission=permission,
        inherit_to_children=inherit,
        expires_at=expires_at,
    )


class TestSelectDirectGrant:
    """리스팅 배치 경로 — 파일 자체의 직접 부여 중 내 그룹 것만으로 권한/그룹명 선택."""

    def test_no_matching_group_returns_none(self) -> None:
        # 직접 부여가 있어도 내 그룹이 아니면 (None, []) — 호출자가 상속 폴백.
        level, names = select_direct_grant([(9, "write", "기획팀")], {1, 2})
        assert level is None and names == []

    def test_single_matching_grant(self) -> None:
        level, names = select_direct_grant([(2, "read", "디자인팀")], {1, 2})
        assert level is GroupPermission.READ and names == ["디자인팀"]

    def test_highest_level_among_my_groups(self) -> None:
        # 여러 내 그룹의 직접 부여 — 최고 수준(write) 적용, 그룹명은 매칭된 모두.
        grants = [(1, "read", "A팀"), (2, "write", "B팀")]
        level, names = select_direct_grant(grants, {1, 2})
        assert level is GroupPermission.WRITE
        assert names == ["A팀", "B팀"]

    def test_ignores_non_member_groups_in_names(self) -> None:
        # 소유자가 다른 그룹에도 공유했더라도 내 그룹명만 노출된다.
        grants = [(1, "read", "내팀"), (9, "manage", "남의팀")]
        level, names = select_direct_grant(grants, {1})
        assert level is GroupPermission.READ and names == ["내팀"]


class TestResolveEffectiveGrant:
    """리스팅 상속 폴백 — 조상 경로에서 유효 권한 수준 + 부여 그룹명."""

    def test_no_rows_returns_none(self) -> None:
        level, names = resolve_effective_grant([], NOW)
        assert level is None and names == []

    def test_self_direct_grant(self) -> None:
        level, names = resolve_effective_grant(
            [_grant(0, 1, "내팀", "manage", file_id=5)], NOW
        )
        assert level is GroupPermission.MANAGE and names == ["내팀"]

    def test_inherited_from_ancestor(self) -> None:
        # 부모(depth1)의 상속 부여로 접근 — 그 그룹명을 돌려준다.
        level, names = resolve_effective_grant(
            [_grant(1, 3, "상위팀", "read", file_id=10)], NOW
        )
        assert level is GroupPermission.READ and names == ["상위팀"]

    def test_closest_ancestor_wins_over_farther(self) -> None:
        rows = [
            _grant(2, 1, "조부팀", "manage", file_id=20),
            _grant(1, 2, "부모팀", "read", file_id=10),
        ]
        level, names = resolve_effective_grant(rows, NOW)
        assert level is GroupPermission.READ and names == ["부모팀"]

    def test_same_depth_highest_level_all_names(self) -> None:
        # 같은 소스(동거리)의 여러 그룹 — 최고 수준 + 그룹명 모두(그룹명순 정렬 입력).
        rows = [
            _grant(1, 1, "A팀", "read", file_id=10),
            _grant(1, 2, "B팀", "manage", file_id=10),
        ]
        level, names = resolve_effective_grant(rows, NOW)
        assert level is GroupPermission.MANAGE and names == ["A팀", "B팀"]

    def test_non_inheriting_ancestor_ignored(self) -> None:
        rows = [_grant(1, 1, "부모팀", "manage", file_id=10, inherit=False)]
        level, names = resolve_effective_grant(rows, NOW)
        assert level is None and names == []

    def test_expired_ancestor_ignored(self) -> None:
        past = NOW - timedelta(hours=1)
        rows = [_grant(1, 1, "부모팀", "manage", file_id=10, expires_at=past)]
        level, names = resolve_effective_grant(rows, NOW)
        assert level is None and names == []


class TestPermissionCovers:
    def test_manage_covers_all(self) -> None:
        assert permission_covers(GroupPermission.MANAGE, "read")
        assert permission_covers(GroupPermission.MANAGE, "write")
        assert permission_covers(GroupPermission.MANAGE, "manage")

    def test_read_covers_only_read(self) -> None:
        assert permission_covers(GroupPermission.READ, "read")
        assert not permission_covers(GroupPermission.READ, "write")
        assert not permission_covers(GroupPermission.READ, "manage")

    def test_write_covers_read_write_not_manage(self) -> None:
        assert permission_covers(GroupPermission.WRITE, "read")
        assert permission_covers(GroupPermission.WRITE, "write")
        assert not permission_covers(GroupPermission.WRITE, "manage")
