"""본인 비밀번호 변경 검증 단위 테스트 — 현재 비번 불일치/정책 위반/성공 (DB 불필요).

check_password_change 는 현재 비밀번호 일치와 새 비밀번호 정책만 순수 검증한다.
실제 해시 갱신·세션 폐기(refresh 전체 폐기)는 라우트가 수행하므로 여기서는 검사 대상이 아니다.
"""

from __future__ import annotations

import pytest

from app.core.security import hash_password, verify_password
from app.services.users import PasswordChangeError, check_password_change

# 정책 통과 평문(영문+숫자+특수문자, 8자 이상)과 그 해시.
_CURRENT_PLAIN = "Passw0rd!"
_CURRENT_HASH = hash_password(_CURRENT_PLAIN)


class TestCheckPasswordChange:
    def test_valid_change_passes(self) -> None:
        # 현재 비번 일치 + 새 비번 정책 통과 → 예외 없음.
        check_password_change(_CURRENT_HASH, _CURRENT_PLAIN, "NewPass1!")

    def test_wrong_current_password_rejected(self) -> None:
        with pytest.raises(PasswordChangeError) as exc:
            check_password_change(_CURRENT_HASH, "WrongPass1!", "NewPass1!")
        assert exc.value.status_code == 400
        assert exc.value.detail == "현재 비밀번호가 올바르지 않습니다."

    @pytest.mark.parametrize(
        "new_password",
        [
            "Pw1!",  # 8자 미만
            "password!",  # 숫자 없음
            "password1",  # 특수문자 없음
            "12345678!",  # 영문자 없음
        ],
    )
    def test_policy_violation_rejected(self, new_password: str) -> None:
        # 현재 비번이 맞아도 새 비번이 정책을 위반하면 422.
        with pytest.raises(PasswordChangeError) as exc:
            check_password_change(_CURRENT_HASH, _CURRENT_PLAIN, new_password)
        assert exc.value.status_code == 422

    def test_current_check_precedes_policy_check(self) -> None:
        # 현재 비번이 틀리면 새 비번 정책 위반 여부와 무관하게 400(불일치)이 우선한다.
        with pytest.raises(PasswordChangeError) as exc:
            check_password_change(_CURRENT_HASH, "WrongPass1!", "weak")
        assert exc.value.status_code == 400

    def test_route_rehash_produces_verifiable_hash(self) -> None:
        # 검증 통과 후 라우트가 수행하는 재해싱 규약 확인(새 해시로 새 비번 검증 가능).
        check_password_change(_CURRENT_HASH, _CURRENT_PLAIN, "NewPass1!")
        new_hash = hash_password("NewPass1!")
        assert verify_password("NewPass1!", new_hash) is True
        assert verify_password(_CURRENT_PLAIN, new_hash) is False
