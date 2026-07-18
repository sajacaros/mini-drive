"""Redis 기반 rate limiting (PRD 10장).

고정 윈도우(fixed window) 카운터: 키 `rl:{scope}:{identifier}` 에 INCR + 첫 증가 시
EXPIRE(window) 를 건다. 카운트가 한도를 넘으면 429 + Retry-After 로 차단한다.

설계 결정
---------
- **fail-open**: Redis 장애 시 요청을 허용한다. PRD 1.4 의 가용성(99.9%) 을 우선하며,
  rate limit 은 남용 완화 장치이지 인가 경계가 아니다 (인가는 JWT/require_admin 이 담당).
  차단(fail-closed)하면 Redis 순단이 곧 전체 서비스 장애가 되므로 허용 후 로깅만 한다.
- **고정 윈도우**: INCR+EXPIRE 두 명령이면 충분하고 원자성 걱정이 적다. 윈도우 경계에서
  최대 2배 버스트가 가능하지만, 분당 한 자리~수십 회 한도에서는 무시할 수준이다.
- **식별자**: IP(비인증 엔드포인트) 또는 user_id(인증 엔드포인트). IP 는 게이트웨이가
  설정하는 X-Real-IP 를 우선하고, 없으면 request.client.host 로 폴백한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger("app.ratelimit")


@dataclass(frozen=True)
class RateLimitResult:
    """rate limit 판정 결과."""

    allowed: bool
    remaining: int      # 이번 윈도우에 남은 허용 횟수 (허용 시)
    retry_after: int    # 차단 시 재시도까지 남은 초 (허용 시 0)


async def check_rate_limit(
    redis: Redis,
    *,
    scope: str,
    identifier: str,
    limit: int,
    window_seconds: int,
) -> RateLimitResult:
    """고정 윈도우 카운터로 한도를 판정한다. Redis 장애 시 fail-open(허용).

    윈도우 첫 요청에서 EXPIRE 를 걸고, 만약 EXPIRE 가 유실되어 TTL 이 없으면(-1) 방어적으로
    다시 건다 (그렇지 않으면 카운터가 영구히 남아 영구 차단된다).
    """
    key = f"rl:{scope}:{identifier}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, window_seconds)
            ttl = window_seconds
        else:
            ttl = await redis.ttl(key)
            if ttl < 0:  # -1: 만료 없음(EXPIRE 유실) / -2: 키 없음 — 방어적 재설정.
                await redis.expire(key, window_seconds)
                ttl = window_seconds
    except RedisError as exc:  # pragma: no cover - 장애 경로
        # fail-open: 가용성 우선. 차단하지 않고 로깅만 한다.
        logger.warning("rate limit fail-open (redis error) scope=%s: %s", scope, exc)
        return RateLimitResult(allowed=True, remaining=limit, retry_after=0)

    if count > limit:
        retry_after = ttl if ttl > 0 else window_seconds
        return RateLimitResult(allowed=False, remaining=0, retry_after=retry_after)
    return RateLimitResult(allowed=True, remaining=max(0, limit - count), retry_after=0)
