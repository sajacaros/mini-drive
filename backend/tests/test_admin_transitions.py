"""Admin 상태 전이 규칙 단위 테스트 — 승인/거절/자기권한 잠금 (DB 불필요)."""

from __future__ import annotations

import pytest

from app.models.enums import UserRole, UserStatus
from app.services.admin import (
    AdminActionError,
    check_can_approve,
    check_can_reject,
    check_self_privilege_guard,
    check_status_update,
)


class TestApproveReject:
    def test_approve_allowed_only_from_pending(self) -> None:
        check_can_approve(UserStatus.PENDING)  # 예외 없음

    @pytest.mark.parametrize(
        "status", [UserStatus.ACTIVE, UserStatus.INACTIVE, UserStatus.REJECTED]
    )
    def test_approve_rejected_from_non_pending(self, status: UserStatus) -> None:
        with pytest.raises(AdminActionError) as exc:
            check_can_approve(status)
        assert exc.value.status_code == 409

    def test_reject_allowed_only_from_pending(self) -> None:
        check_can_reject(UserStatus.PENDING)

    @pytest.mark.parametrize("status", [UserStatus.ACTIVE, UserStatus.REJECTED])
    def test_reject_rejected_from_non_pending(self, status: UserStatus) -> None:
        with pytest.raises(AdminActionError):
            check_can_reject(status)


class TestStatusUpdate:
    @pytest.mark.parametrize("status", [UserStatus.ACTIVE, UserStatus.INACTIVE])
    def test_patchable_statuses_allowed(self, status: UserStatus) -> None:
        check_status_update(status)

    @pytest.mark.parametrize("status", [UserStatus.PENDING, UserStatus.REJECTED])
    def test_non_patchable_statuses_rejected(self, status: UserStatus) -> None:
        with pytest.raises(AdminActionError) as exc:
            check_status_update(status)
        assert exc.value.status_code == 422


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
