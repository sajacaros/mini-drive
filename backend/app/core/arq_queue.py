"""arq(Redis) 잡 큐잉 헬퍼 (PRD 3.7.2 자동 재인덱싱, Phase 7-1).

업로드/버전/삭제/제외 토글 훅에서 인덱싱 잡을 큐잉한다. **enqueue 실패는 경고 로그만 남기고
삼킨다** — 업로드/삭제 등 파일 서비스 경로를 절대 실패시키지 않는다(fail-open, rate limit 의
RATE_LIMIT_ENABLED 철학과 동일). 실제 실행은 app.worker 의 arq 워커가 담당한다.

잡 함수 이름은 app.worker 에 등록된 이름과 일치해야 한다: index_file / drop_file_index.
"""

from __future__ import annotations

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings
from app.core.logging import get_logger

_log = get_logger("app.arq_queue")

_pool: ArqRedis | None = None


async def _get_pool() -> ArqRedis:
    """지연 생성한 공용 arq 풀. 실행 중인 이벤트 루프에서 최초 큐잉 시 만든다."""
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def _enqueue(job: str, *args: object) -> None:
    """잡을 큐잉한다. indexing_enabled=False 면 조용히 건너뛰고, 실패는 경고 로그만."""
    if not settings.indexing_enabled:
        return
    try:
        pool = await _get_pool()
        await pool.enqueue_job(job, *args)
    except Exception as exc:  # noqa: BLE001 - fail-open: 파일 서비스 경로를 막지 않는다.
        _log.warning("enqueue_failed", job=job, args=args, error=str(exc))


async def enqueue_index_file(file_id: int) -> None:
    """파일(또는 폴더) 인덱싱 잡을 큐잉한다 (업로드/버전/복원/제외 해제)."""
    await _enqueue("index_file", file_id)


async def enqueue_drop_file_index(file_id: int) -> None:
    """파일(또는 폴더) 청크 삭제 잡을 큐잉한다 (휴지통 이동/인덱싱 제외 설정)."""
    await _enqueue("drop_file_index", file_id)
