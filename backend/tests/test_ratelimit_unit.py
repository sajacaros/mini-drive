"""Rate limit 고정 윈도우 로직 단위 테스트 (PRD 10장).

실제 Redis 없이, INCR/EXPIRE/TTL 만 구현한 인메모리 페이크로 윈도우 동작을 검증한다:
한도 내 허용 → 한도 초과 429(retry_after) → 윈도우 만료 후 재허용 → fail-open.
"""

from __future__ import annotations

import pytest
from redis.exceptions import RedisError

from app.core.ratelimit import check_rate_limit


class FakeRedis:
    """INCR/EXPIRE/TTL 만 지원하는 최소 페이크. 만료는 수동으로 시뮬레이션한다."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        self.ttls[key] = seconds
        return True

    async def ttl(self, key: str) -> int:
        if key not in self.counts:
            return -2
        return self.ttls.get(key, -1)

    def expire_window(self, key: str) -> None:
        """윈도우 경과 시뮬레이션 — 카운터/TTL 제거."""
        self.counts.pop(key, None)
        self.ttls.pop(key, None)


class FailingRedis:
    async def incr(self, key: str) -> int:
        raise RedisError("boom")


@pytest.mark.asyncio
async def test_allows_up_to_limit() -> None:
    redis = FakeRedis()
    results = [
        await check_rate_limit(
            redis, scope="login", identifier="1.2.3.4", limit=5, window_seconds=60
        )
        for _ in range(5)
    ]
    assert all(r.allowed for r in results)
    # 마지막 허용 요청에서 remaining 은 0.
    assert results[-1].remaining == 0
    # remaining 은 5→4→3→2→1→0 이 아니라 4→3→2→1→0 (첫 요청 후 4 남음).
    assert [r.remaining for r in results] == [4, 3, 2, 1, 0]


@pytest.mark.asyncio
async def test_blocks_over_limit_with_retry_after() -> None:
    redis = FakeRedis()
    for _ in range(5):
        await check_rate_limit(
            redis, scope="login", identifier="ip", limit=5, window_seconds=60
        )
    sixth = await check_rate_limit(
        redis, scope="login", identifier="ip", limit=5, window_seconds=60
    )
    assert sixth.allowed is False
    assert sixth.retry_after == 60


@pytest.mark.asyncio
async def test_window_reset_reallows() -> None:
    redis = FakeRedis()
    for _ in range(5):
        await check_rate_limit(
            redis, scope="login", identifier="ip", limit=5, window_seconds=60
        )
    blocked = await check_rate_limit(
        redis, scope="login", identifier="ip", limit=5, window_seconds=60
    )
    assert blocked.allowed is False

    redis.expire_window("rl:login:ip")  # 윈도우 경과.
    after = await check_rate_limit(
        redis, scope="login", identifier="ip", limit=5, window_seconds=60
    )
    assert after.allowed is True


@pytest.mark.asyncio
async def test_distinct_identifiers_independent() -> None:
    redis = FakeRedis()
    for _ in range(5):
        await check_rate_limit(
            redis, scope="login", identifier="ip-a", limit=5, window_seconds=60
        )
    # ip-a 는 소진, ip-b 는 독립적으로 허용.
    a = await check_rate_limit(
        redis, scope="login", identifier="ip-a", limit=5, window_seconds=60
    )
    b = await check_rate_limit(
        redis, scope="login", identifier="ip-b", limit=5, window_seconds=60
    )
    assert a.allowed is False
    assert b.allowed is True


@pytest.mark.asyncio
async def test_expire_lost_is_reset_defensively() -> None:
    """EXPIRE 유실(TTL -1)이면 방어적으로 다시 걸어 영구 차단을 막는다."""
    redis = FakeRedis()
    await check_rate_limit(
        redis, scope="s", identifier="id", limit=3, window_seconds=30
    )
    del redis.ttls["rl:s:id"]  # EXPIRE 유실 시뮬레이션 (카운터는 남음).
    await check_rate_limit(
        redis, scope="s", identifier="id", limit=3, window_seconds=30
    )
    assert redis.ttls["rl:s:id"] == 30  # 재설정됨.


@pytest.mark.asyncio
async def test_fail_open_on_redis_error() -> None:
    result = await check_rate_limit(
        FailingRedis(), scope="s", identifier="id", limit=1, window_seconds=60
    )
    assert result.allowed is True
