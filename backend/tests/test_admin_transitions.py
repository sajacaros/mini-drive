"""Admin 상태 전이 규칙 단위 테스트 — status 갱신/자기권한 잠금 (DB 불필요).

가입 코드제 전환(2026-07-19): 승인/거절 전이는 폐지됐고 status 는 active/inactive 만 존재한다.
"""

from __future__ import annotations

import pytest

from app.models.enums import UserRole, UserStatus
from app.services.admin import (
    AdminActionError,
    check_self_privilege_guard,
    check_status_update,
)


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
