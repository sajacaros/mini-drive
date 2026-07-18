"""Redis 기반 refresh 토큰 저장·회전 (PRD 10장, 2.1).

refresh 토큰을 jti 단위로 Redis 에 저장한다. 회전(rotation) 시 기존 jti 를 폐기하고
새 jti 를 저장한다. 저장되지 않은(=이미 회전되었거나 폐기된) 유효 JWT 가 다시 제시되면
재사용으로 간주하고 해당 사용자의 전체 세션을 폐기한다.
"""

from __future__ import annotations

from redis.asyncio import Redis

# refresh:{user_id}:{jti} -> "1" (TTL = refresh 만료). 사용자별 전체 폐기는 패턴 스캔.
_KEY_PREFIX = "refresh"


def _key(user_id: int, jti: str) -> str:
    return f"{_KEY_PREFIX}:{user_id}:{jti}"


def _user_pattern(user_id: int) -> str:
    return f"{_KEY_PREFIX}:{user_id}:*"


async def store_refresh_token(redis: Redis, user_id: int, jti: str, ttl_seconds: int) -> None:
    """새 refresh jti 를 TTL 과 함께 저장한다."""
    await redis.set(_key(user_id, jti), "1", ex=ttl_seconds)


async def is_refresh_token_active(redis: Redis, user_id: int, jti: str) -> bool:
    """해당 jti 가 아직 유효(회전/폐기되지 않음)한지 확인한다."""
    return bool(await redis.exists(_key(user_id, jti)))


async def revoke_refresh_token(redis: Redis, user_id: int, jti: str) -> None:
    """단일 refresh jti 를 폐기한다 (로그아웃/회전)."""
    await redis.delete(_key(user_id, jti))


async def revoke_all_user_tokens(redis: Redis, user_id: int) -> int:
    """사용자의 모든 refresh 토큰을 폐기한다 (재사용 감지·강제 로그아웃).

    폐기한 토큰 수를 반환한다.
    """
    deleted = 0
    async for key in redis.scan_iter(match=_user_pattern(user_id)):
        deleted += await redis.delete(key)
    return deleted
