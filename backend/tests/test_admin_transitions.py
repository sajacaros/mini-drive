"""Admin 상태 전이 규칙 단위 테스트 — status 갱신/자기권한 잠금 (DB 불필요).

가입 코드제 전환(2026-07-19): 승인/거절 전이는 폐지됐고 status 는 active/inactive 만 존재한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models.enums import UserRole, UserStatus
from app.services.admin import (
    AdminActionError,
    check_display_name_authorization,
    check_role_change_authorization,
    check_self_privilege_guard,
    check_status_update,
    check_super_admin_target_guard,
)


def _u(user_id: int, role: UserRole) -> SimpleNamespace:
    """가드가 읽는 최소 속성(id/role)만 가진 User 스텁."""
    return SimpleNamespace(id=user_id, role=role)


class TestStatusUpdate:
    @pytest.mark.parametrize("status", [UserStatus.ACTIVE, UserStatus.INACTIVE])
    def test_patchable_statuses_allowed(self, status: UserStatus) -> None:
        check_status_update(status)


class TestSelfPrivilegeGuard:
    def test_other_user_unrestricted(self) -> None:
        # 다른 사용자면 admin 해제/비활성화 허용.
        check_self_privilege_guard(1, 2, UserStatus.INACTIVE, UserRole.USER)

    def test_self_role_demotion_rejected(self) -> None:
        with pytest.raises(AdminActionError) as exc:
            check_self_privilege_guard(1, 1, None, UserRole.USER)
        assert exc.value.status_code == 400

    def test_self_deactivation_rejected(self) -> None:
        with pytest.raises(AdminActionError):
            check_self_privilege_guard(1, 1, UserStatus.INACTIVE, None)

    def test_self_keeping_admin_active_allowed(self) -> None:
        # 자기 자신이라도 admin/active 유지 변경은 허용 (예: max_storage 만 변경).
        check_self_privilege_guard(1, 1, UserStatus.ACTIVE, UserRole.ADMIN)
        check_self_privilege_guard(1, 1, None, None)

    def test_self_super_admin_kept_allowed(self) -> None:
        # super_admin 도 admin 계열 유지면 허용 (강등 아님).
        check_self_privilege_guard(1, 1, None, UserRole.SUPER_ADMIN)


class TestRoleChangeAuthorization:
    def test_super_admin_can_grant_admin(self) -> None:
        check_role_change_authorization(
            _u(1, UserRole.SUPER_ADMIN), _u(2, UserRole.USER), UserRole.ADMIN
        )

    def test_super_admin_can_revoke_admin(self) -> None:
        check_role_change_authorization(
            _u(1, UserRole.SUPER_ADMIN), _u(2, UserRole.ADMIN), UserRole.USER
        )

    def test_regular_admin_cannot_change_role(self) -> None:
        with pytest.raises(AdminActionError) as exc:
            check_role_change_authorization(
                _u(1, UserRole.ADMIN), _u(2, UserRole.USER), UserRole.ADMIN
            )
        assert exc.value.status_code == 403

    def test_cannot_assign_super_admin_via_api(self) -> None:
        with pytest.raises(AdminActionError) as exc:
            check_role_change_authorization(
                _u(1, UserRole.SUPER_ADMIN), _u(2, UserRole.ADMIN), UserRole.SUPER_ADMIN
            )
        assert exc.value.status_code == 400

    def test_cannot_demote_super_admin(self) -> None:
        with pytest.raises(AdminActionError) as exc:
            check_role_change_authorization(
                _u(1, UserRole.SUPER_ADMIN), _u(2, UserRole.SUPER_ADMIN), UserRole.ADMIN
            )
        assert exc.value.status_code == 400

    def test_noop_when_role_unchanged_or_none(self) -> None:
        # 같은 역할이거나 None 이면 인가 검사를 건너뛴다(권한 없어도 통과).
        check_role_change_authorization(
            _u(1, UserRole.ADMIN), _u(2, UserRole.ADMIN), UserRole.ADMIN
        )
        check_role_change_authorization(
            _u(1, UserRole.ADMIN), _u(2, UserRole.USER), None
        )


class TestSuperAdminTargetGuard:
    def test_other_actor_cannot_modify_super_admin(self) -> None:
        with pytest.raises(AdminActionError) as exc:
            check_super_admin_target_guard(
                _u(1, UserRole.ADMIN), _u(2, UserRole.SUPER_ADMIN)
            )
        assert exc.value.status_code == 403

    def test_super_admin_can_modify_self(self) -> None:
        check_super_admin_target_guard(
            _u(2, UserRole.SUPER_ADMIN), _u(2, UserRole.SUPER_ADMIN)
        )

    def test_non_super_admin_target_unrestricted(self) -> None:
        check_super_admin_target_guard(_u(1, UserRole.ADMIN), _u(2, UserRole.USER))


class TestDisplayNameAuthorization:
    def test_super_admin_can_change_name(self) -> None:
        check_display_name_authorization(_u(1, UserRole.SUPER_ADMIN), "새 이름")

    def test_regular_admin_cannot_change_name(self) -> None:
        with pytest.raises(AdminActionError) as exc:
            check_display_name_authorization(_u(1, UserRole.ADMIN), "새 이름")
        assert exc.value.status_code == 403

    def test_none_skips_authorization(self) -> None:
        # 이름을 지정하지 않은 요청(상태/할당량만)은 admin 도 통과.
        check_display_name_authorization(_u(1, UserRole.ADMIN), None)
