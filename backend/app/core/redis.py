from collections.abc import AsyncGenerator

from redis.asyncio import Redis, from_url

from app.core.config import settings

# redis-py 의 from_url 은 아직 반환 타입 주석이 없어 strict 모드에서 untyped call 로 잡힌다.
# 좌변 주석으로 타입은 확정되므로 호출만 무시한다.
redis_client: Redis = from_url(  # type: ignore[no-untyped-call]
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)


async def get_redis() -> AsyncGenerator[Redis, None]:
    """FastAPI 의존성: 공유 async Redis 클라이언트를 제공한다."""
    yield redis_client
