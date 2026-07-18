"""인증 보안 유틸리티 (PRD 10장).

- 비밀번호: argon2id 해싱 + 정책 검증(최소 8자, 영문+숫자+특수문자).
- JWT: access(15분)/refresh(7일) 토큰 발급·검증. refresh 는 jti 로 Redis 회전에 사용.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import settings

# argon2id (기본 type=ID) 로 비밀번호를 해싱한다 (PRD 10장).
_password_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]

# 비밀번호 정책: 최소 8자, 영문/숫자/특수문자 각 1개 이상 (PRD 10장).
_MIN_PASSWORD_LENGTH = 8
_LETTER_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"\d")
_SPECIAL_RE = re.compile(r"[^A-Za-z0-9]")


class PasswordPolicyError(ValueError):
    """비밀번호가 정책을 만족하지 않을 때 발생."""


def validate_password_policy(password: str) -> None:
    """정책 위반 시 PasswordPolicyError 를 발생시킨다."""
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise PasswordPolicyError("비밀번호는 최소 8자 이상이어야 합니다.")
    if not _LETTER_RE.search(password):
        raise PasswordPolicyError("비밀번호에 영문자를 최소 1자 포함해야 합니다.")
    if not _DIGIT_RE.search(password):
        raise PasswordPolicyError("비밀번호에 숫자를 최소 1자 포함해야 합니다.")
    if not _SPECIAL_RE.search(password):
        raise PasswordPolicyError("비밀번호에 특수문자를 최소 1자 포함해야 합니다.")


def hash_password(password: str) -> str:
    """argon2id 해시 문자열을 반환한다."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """평문 비밀번호가 해시와 일치하면 True."""
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


@dataclass(frozen=True)
class IssuedToken:
    """발급된 JWT 와 그 메타데이터."""

    token: str
    jti: str
    expires_at: datetime


def _create_token(user_id: int, token_type: TokenType, expires_delta: timedelta) -> IssuedToken:
    now = datetime.now(UTC)
    expires_at = now + expires_delta
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": token_type,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return IssuedToken(token=token, jti=jti, expires_at=expires_at)


def create_access_token(user_id: int) -> IssuedToken:
    return _create_token(
        user_id,
        "access",
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: int) -> IssuedToken:
    return _create_token(
        user_id,
        "refresh",
        timedelta(days=settings.refresh_token_expire_days),
    )


class TokenError(ValueError):
    """JWT 검증 실패(만료·서명 오류·타입 불일치)."""


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """JWT 를 검증하고 payload 를 반환한다. 실패 시 TokenError."""
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc

    if payload.get("type") != expected_type:
        raise TokenError("토큰 타입이 올바르지 않습니다.")
    if "sub" not in payload or "jti" not in payload:
        raise TokenError("토큰에 필수 클레임이 없습니다.")
    return payload
