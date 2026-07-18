"""권한 상속 판정 순수 로직 단위 테스트 (DB/Redis 불필요) — PRD 5.7.

resolve_effective_permission 은 조상 경로에서 모은 권한 행으로 유효 권한을 계산한다.
검증 축: 최근접 조상 우선 / 동거리 최고 수준 / inherit_to_children=FALSE 조상 무시 /
만료 행 무시 / 자기 자신(depth 0)은 inherit 무관.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.enums import GroupPermission
from app.services.permissions import (
    AncestorPermRow,
    permission_covers,
    resolve_effective_permission,
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
