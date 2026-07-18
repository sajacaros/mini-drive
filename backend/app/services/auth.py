"""인증 오케스트레이션 — 토큰 쌍 발급 및 refresh 회전 (PRD 6.1, 10장)."""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import settings
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.schemas.auth import TokenResponse
from app.services.tokens import (
    is_refresh_token_active,
    revoke_all_user_tokens,
    revoke_refresh_token,
    store_refresh_token,
)

_REFRESH_TTL_SECONDS = settings.refresh_token_expire_days * 24 * 60 * 60
_ACCESS_EXPIRES_IN = settings.access_token_expire_minutes * 60


async def issue_token_pair(redis: Redis, user_id: int) -> TokenResponse:
    """새 access/refresh 쌍을 발급하고 refresh jti 를 Redis 에 저장한다."""
    access = create_access_token(user_id)
    refresh = create_refresh_token(user_id)
    await store_refresh_token(redis, user_id, refresh.jti, _REFRESH_TTL_SECONDS)
    return TokenResponse(
        access_token=access.token,
        refresh_token=refresh.token,
        expires_in=_ACCESS_EXPIRES_IN,
    )


class RefreshError(Exception):
    """refresh 실패. reuse_detected=True 면 전체 세션이 이미 폐기됨."""

    def __init__(self, message: str, *, reuse_detected: bool = False) -> None:
        super().__init__(message)
        self.reuse_detected = reuse_detected


async def rotate_refresh_token(redis: Redis, refresh_token: str) -> tuple[int, TokenResponse]:
    """refresh 토큰을 회전한다. (user_id, 새 토큰 쌍) 을 반환한다.

    - 유효 JWT 이나 Redis 에 없으면(이미 회전/폐기) 재사용으로 간주,
      해당 사용자 전체 세션을 폐기하고 RefreshError(reuse_detected=True) 를 발생.
    - 정상 시 기존 jti 폐기 후 새 쌍 발급.
    """
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
        user_id = int(payload["sub"])
        jti = payload["jti"]
    except (TokenError, KeyError, ValueError) as exc:
        raise RefreshError("유효하지 않은 refresh 토큰입니다.") from exc

    if not await is_refresh_token_active(redis, user_id, jti):
        # 재사용 감지 — 방어적으로 전체 세션 폐기 (PRD 10장).
        await revoke_all_user_tokens(redis, user_id)
        raise RefreshError("refresh 토큰 재사용이 감지되었습니다.", reuse_detected=True)

    await revoke_refresh_token(redis, user_id, jti)
    tokens = await issue_token_pair(redis, user_id)
    return user_id, tokens
