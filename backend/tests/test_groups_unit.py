"""그룹 역할 가드 단위 테스트 — 초대/제거/역할 변경/소유권 이전 권한 매트릭스 (DB 불필요)."""

from __future__ import annotations

import pytest

from app.models.enums import GroupRole
from app.services.groups import (
    GroupServiceError,
    check_assignable_role,
    check_can_change_role,
    check_can_remove_member,
    check_can_transfer_ownership,
    check_manage_members,
)


class TestManageMembers:
    @pytest.mark.parametrize("role", [GroupRole.OWNER, GroupRole.ADMIN])
    def test_owner_admin_can_manage(self, role: GroupRole) -> None:
        check_manage_members(role)  # 예외 없음

    @pytest.mark.parametrize("role", [GroupRole.MEMBER, None])
    def test_member_and_nonmember_rejected(self, role: GroupRole | None) -> None:
        with pytest.raises(GroupServiceError) as exc:
            check_manage_members(role)
        assert exc.value.status_code == 403


class TestAssignableRole:
    @pytest.mark.parametrize("role", [GroupRole.ADMIN, GroupRole.MEMBER])
    def test_admin_member_assignable(self, role: GroupRole) -> None:
        check_assignable_role(role)

    def test_owner_direct_grant_rejected(self) -> None:
        with pytest.raises(GroupServiceError) as exc:
            check_assignable_role(GroupRole.OWNER)
        assert exc.value.status_code == 400


class TestRemoveMember:
    def test_owner_removes_admin(self) -> None:
        check_can_remove_member(GroupRole.OWNER, GroupRole.ADMIN, 1, 2)

    def test_owner_removes_member(self) -> None:
        check_can_remove_member(GroupRole.OWNER, GroupRole.MEMBER, 1, 2)

    def test_admin_removes_member(self) -> None:
        check_can_remove_member(GroupRole.ADMIN, GroupRole.MEMBER, 1, 2)

    def test_admin_cannot_remove_admin(self) -> None:
        with pytest.raises(GroupServiceError) as exc:
            check_can_remove_member(GroupRole.ADMIN, GroupRole.ADMIN, 1, 2)
        assert exc.value.status_code == 403

    def test_admin_cannot_remove_owner(self) -> None:
        with pytest.raises(GroupServiceError) as exc:
            check_can_remove_member(GroupRole.ADMIN, GroupRole.OWNER, 1, 2)
        assert exc.value.status_code == 400

    def test_owner_cannot_remove_self(self) -> None:
        # owner 자신 제거 = target_role OWNER → 400 (소유권 이전 먼저).
        with pytest.raises(GroupServiceError) as exc:
            check_can_remove_member(GroupRole.OWNER, GroupRole.OWNER, 1, 1)
        assert exc.value.status_code == 400

    @pytest.mark.parametrize("actor_role", [GroupRole.MEMBER, None])
    def test_member_and_nonmember_cannot_remove(
        self, actor_role: GroupRole | None
    ) -> None:
        with pytest.raises(GroupServiceError) as exc:
            check_can_remove_member(actor_role, GroupRole.MEMBER, 1, 2)
        assert exc.value.status_code == 403


class TestChangeRole:
    @pytest.mark.parametrize("new_role", [GroupRole.ADMIN, GroupRole.MEMBER])
    def test_owner_changes_member_role(self, new_role: GroupRole) -> None:
        check_can_change_role(GroupRole.OWNER, GroupRole.MEMBER, new_role)

    @pytest.mark.parametrize("actor_role", [GroupRole.ADMIN, GroupRole.MEMBER, None])
    def test_non_owner_cannot_change_role(
        self, actor_role: GroupRole | None
    ) -> None:
        with pytest.raises(GroupServiceError) as exc:
            check_can_change_role(actor_role, GroupRole.MEMBER, GroupRole.ADMIN)
        assert exc.value.status_code == 403

    def test_cannot_change_owner_role(self) -> None:
        with pytest.raises(GroupServiceError) as exc:
            check_can_change_role(GroupRole.OWNER, GroupRole.OWNER, GroupRole.ADMIN)
        assert exc.value.status_code == 400

    def test_cannot_promote_to_owner(self) -> None:
        with pytest.raises(GroupServiceError) as exc:
            check_can_change_role(GroupRole.OWNER, GroupRole.MEMBER, GroupRole.OWNER)
        assert exc.value.status_code == 400


class TestTransferOwnership:
    def test_owner_can_transfer(self) -> None:
        check_can_transfer_ownership(GroupRole.OWNER)

    @pytest.mark.parametrize("actor_role", [GroupRole.ADMIN, GroupRole.MEMBER, None])
    def test_non_owner_cannot_transfer(self, actor_role: GroupRole | None) -> None:
        with pytest.raises(GroupServiceError) as exc:
            check_can_transfer_ownership(actor_role)
        assert exc.value.status_code == 403
