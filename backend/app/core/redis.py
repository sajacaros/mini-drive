from collections.abc import AsyncGenerator

from redis.asyncio import Redis, from_url

from app.core.config import settings

redis_client: Redis = from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)


async def get_redis() -> AsyncGenerator[Redis, None]:
    """FastAPI 의존성: 공유 async Redis 클라이언트를 제공한다."""
    yield redis_client
