"""보안 유틸리티 단위 테스트 — 비밀번호 정책, argon2 해싱, JWT 발급/검증 (DB 불필요)."""

from __future__ import annotations

import pytest

from app.core.security import (
    PasswordPolicyError,
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    validate_password_policy,
    verify_password,
)


class TestPasswordPolicy:
    def test_valid_password_passes(self) -> None:
        validate_password_policy("Passw0rd!")  # 예외 없이 통과

    @pytest.mark.parametrize(
        "password",
        [
            "Pw1!",  # 8자 미만
            "password!",  # 숫자 없음
            "password1",  # 특수문자 없음
            "12345678!",  # 영문자 없음
        ],
    )
    def test_invalid_password_rejected(self, password: str) -> None:
        with pytest.raises(PasswordPolicyError):
            validate_password_policy(password)


class TestPasswordHashing:
    def test_hash_and_verify_roundtrip(self) -> None:
        h = hash_password("Passw0rd!")
        assert h != "Passw0rd!"  # 평문 저장 금지
        assert h.startswith("$argon2id$")  # argon2id 사용 (PRD 10장)
        assert verify_password("Passw0rd!", h) is True

    def test_wrong_password_fails(self) -> None:
        h = hash_password("Passw0rd!")
        assert verify_password("WrongPass1!", h) is False

    def test_invalid_hash_returns_false(self) -> None:
        assert verify_password("Passw0rd!", "not-a-hash") is False


class TestJwt:
    def test_access_token_roundtrip(self) -> None:
        issued = create_access_token(user_id=42)
        payload = decode_token(issued.token, expected_type="access")
        assert payload["sub"] == "42"
        assert payload["type"] == "access"
        assert payload["jti"] == issued.jti

    def test_refresh_token_roundtrip(self) -> None:
        issued = create_refresh_token(user_id=7)
        payload = decode_token(issued.token, expected_type="refresh")
        assert payload["sub"] == "7"
        assert payload["type"] == "refresh"

    def test_type_mismatch_rejected(self) -> None:
        issued = create_access_token(user_id=1)
        with pytest.raises(TokenError):
            decode_token(issued.token, expected_type="refresh")

    def test_tampered_token_rejected(self) -> None:
        issued = create_access_token(user_id=1)
        with pytest.raises(TokenError):
            decode_token(issued.token + "x", expected_type="access")

    def test_each_token_has_unique_jti(self) -> None:
        a = create_refresh_token(user_id=1)
        b = create_refresh_token(user_id=1)
        assert a.jti != b.jti
