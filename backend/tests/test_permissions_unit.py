"""권한 상속 판정 순수 로직 단위 테스트 (DB/Redis 불필요) — PRD 5.7.

resolve_effective_permission 은 조상 경로에서 모은 권한 행으로 유효 권한을 계산한다.
검증 축: 조상 전체에서 최고 수준(누적) / inherit_to_children=FALSE 조상 무시 /
만료 행 무시 / 자기 자신(depth 0)은 inherit 무관.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.enums import GroupPermission
from app.services.permissions import (
    AncestorGrantRow,
    AncestorPermRow,
    InheritedGrant,
    narrowing_conflict,
    permission_covers,
    resolve_effective_grant,
    resolve_effective_permission,
    select_direct_grant,
    select_inherited_grants,
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


class TestHighestLevelWins:
    """누적(union) — 거리와 무관하게 조상 경로 전체에서 최고 수준이 적용된다 (2026-07-28 변경).

    종전 규칙은 '가장 가까운 조상이 이긴다'였다. 권한 행이 사용자 소속 그룹 전체에 대해
    수집되므로, 그 규칙은 재정의를 같은 그룹 안이 아니라 그룹을 가로질러 적용해
    '그룹B 의 read 가 그룹A 의 manage 를 깎는' 간섭을 만들었다.
    """

    def test_no_rows_returns_none(self) -> None:
        level, src = resolve_effective_permission([], NOW)
        assert level is None and src is None

    def test_self_depth_zero_applies(self) -> None:
        level, src = resolve_effective_permission([_row(0, "write", file_id=5)], NOW)
        assert level is GroupPermission.WRITE and src == 5

    def test_farther_higher_beats_closer_lower(self) -> None:
        # 조부(depth2)=manage 가 부모(depth1)=read 를 이긴다 — 상위에서 준 권한이 살아있다.
        rows = [_row(2, "manage", file_id=20), _row(1, "read", file_id=10)]
        level, src = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.MANAGE and src == 20

    def test_ancestor_beats_lower_self_grant(self) -> None:
        # 자기 자신의 read 도 조상의 manage 를 취소하지 못한다 — 낮추는 재정의는 표현 불가.
        rows = [_row(0, "read", file_id=1), _row(1, "manage", file_id=10)]
        level, src = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.MANAGE and src == 10

    def test_closer_higher_still_wins(self) -> None:
        rows = [_row(2, "read", file_id=20), _row(0, "manage", file_id=1)]
        level, src = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.MANAGE and src == 1

    def test_tie_prefers_nearest_source(self) -> None:
        # 같은 수준이면 출처는 가까운 쪽으로 보고한다(UI 의 '어디서 상속' 표기용).
        rows = [_row(2, "write", file_id=20), _row(1, "write", file_id=10)]
        level, src = resolve_effective_permission(rows, NOW)
        assert level is GroupPermission.WRITE and src == 10


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
    source_file_name: str = "",
) -> AncestorGrantRow:
    return AncestorGrantRow(
        depth=depth,
        file_id=file_id or (100 + depth),
        group_id=group_id,
        group_name=group_name,
        permission=permission,
        inherit_to_children=inherit,
        expires_at=expires_at,
        source_file_name=source_file_name or f"폴더{depth}",
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

    def test_farther_higher_beats_closer_lower(self) -> None:
        # 누적 — 조부의 manage 가 부모의 read 를 이긴다. 그룹명은 접근을 준 쪽 전부.
        rows = [
            _grant(2, 1, "조부팀", "manage", file_id=20),
            _grant(1, 2, "부모팀", "read", file_id=10),
        ]
        level, names = resolve_effective_grant(rows, NOW)
        assert level is GroupPermission.MANAGE and names == ["조부팀", "부모팀"]

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


class TestSelectInheritedGrants:
    """권한 화면의 '상속된 권한' 목록 — 그룹별 최고 수준 한 건 (spec/permissions.md).

    이 목록은 표시용이 아니라 **경고의 근거**다. 프런트가 "상속보다 낮게는 못 낮춘다"를
    여기 실린 수준과 비교해 판단하므로, 유효 권한보다 낮게 실리면 경고가 새는 방향으로 틀린다.
    """

    def test_no_rows(self) -> None:
        assert select_inherited_grants([], NOW) == []

    def test_self_grant_excluded(self) -> None:
        # depth 0 은 직접 부여 목록의 몫이다 — 상속 목록에 섞이면 이중으로 보인다.
        assert select_inherited_grants([_grant(0, 1, "A팀", "manage")], NOW) == []

    def test_non_inheriting_ancestor_excluded(self) -> None:
        rows = [_grant(1, 1, "A팀", "manage", inherit=False)]
        assert select_inherited_grants(rows, NOW) == []

    def test_expired_ancestor_excluded(self) -> None:
        rows = [_grant(1, 1, "A팀", "manage", expires_at=NOW - timedelta(hours=1))]
        assert select_inherited_grants(rows, NOW) == []

    def test_farther_higher_beats_closer_lower(self) -> None:
        """조부 manage + 부모 read → manage. 최근접을 골랐다면 read 로 낮게 실렸다.

        유효 권한이 누적이라 실제 권한은 manage 이고, 화면이 read 를 보여주면 관리자가
        'read 니까 read 로 낮춰도 되겠지' 로 읽는다.
        """
        rows = [
            _grant(1, 1, "A팀", "read", file_id=10, source_file_name="부모"),
            _grant(2, 1, "A팀", "manage", file_id=20, source_file_name="조부"),
        ]
        (got,) = select_inherited_grants(rows, NOW)
        assert got.permission == "manage"
        assert got.source_file_id == 20 and got.source_file_name == "조부"

    def test_tie_prefers_nearest(self) -> None:
        # 동수준이면 가까운 쪽을 출처로 — 사람이 고치러 갈 폴더는 가까운 쪽이다.
        rows = [
            _grant(2, 1, "A팀", "read", file_id=20, source_file_name="조부"),
            _grant(1, 1, "A팀", "read", file_id=10, source_file_name="부모"),
        ]
        (got,) = select_inherited_grants(rows, NOW)
        assert got.source_file_id == 10 and got.depth == 1

    def test_groups_are_independent(self) -> None:
        rows = [
            _grant(1, 1, "A팀", "read", file_id=10),
            _grant(2, 2, "B팀", "manage", file_id=20),
        ]
        got = {g.group_id: g.permission for g in select_inherited_grants(rows, NOW)}
        assert got == {1: "read", 2: "manage"}

    def test_expired_higher_does_not_mask_live_lower(self) -> None:
        # 만료된 manage 가 살아있는 read 를 가리면 안 된다 — 걸러진 뒤에 최고 수준을 고른다.
        rows = [
            _grant(1, 1, "A팀", "manage", file_id=10, expires_at=NOW - timedelta(days=1)),
            _grant(2, 1, "A팀", "read", file_id=20),
        ]
        (got,) = select_inherited_grants(rows, NOW)
        assert got.permission == "read" and got.source_file_id == 20


class TestNarrowingConflict:
    """상속보다 낮은 부여는 거부한다 (spec/permissions.md 「상속보다 낮추기는 거부한다」).

    유효 권한이 누적이라 낮은 직접 부여는 저장돼도 유효 권한을 바꾸지 못한다. 허용하면
    관리자가 좁혔다고 믿는 조용한 무효가 남으므로, 저장을 막고 사유를 돌려준다.
    """

    @staticmethod
    def _inherited(permission: str) -> InheritedGrant:
        return InheritedGrant(
            group_id=1,
            group_name="A팀",
            permission=permission,
            source_file_id=10,
            source_file_name="공유폴더",
            depth=1,
            expires_at=None,
        )

    def test_no_inheritance_allows_anything(self) -> None:
        assert narrowing_conflict(None, GroupPermission.READ) is None

    def test_same_level_allowed(self) -> None:
        assert narrowing_conflict(self._inherited("read"), GroupPermission.READ) is None

    def test_promotion_allowed(self) -> None:
        # 넓히는 방향은 자유다 — read 상속 위에 manage 를 얹는 것은 유효 권한을 실제로 바꾼다.
        assert narrowing_conflict(self._inherited("read"), GroupPermission.MANAGE) is None

    def test_demotion_rejected(self) -> None:
        msg = narrowing_conflict(self._inherited("manage"), GroupPermission.READ)
        assert msg is not None

    def test_reason_names_source_and_escape_route(self) -> None:
        """사유가 '어디서 상속됐고 어떻게 풀어야 하는지'를 말해야 한다.

        좁히는 방법이 '상속 출처에서 하위 상속 끄기' 하나뿐이라, 출처 이름이 빠지면 거부만
        당하고 다음 행동을 알 수 없다.
        """
        msg = narrowing_conflict(self._inherited("manage"), GroupPermission.WRITE)
        assert msg is not None
        assert "공유폴더" in msg and "A팀" in msg
        assert "관리" in msg and "쓰기" in msg
        assert "하위 상속" in msg

    def test_write_to_read_rejected(self) -> None:
        assert narrowing_conflict(self._inherited("write"), GroupPermission.READ) is not None

    def test_manage_to_write_rejected(self) -> None:
        assert narrowing_conflict(self._inherited("manage"), GroupPermission.WRITE) is not None
