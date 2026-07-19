"""arq 인덱싱 워커 (PRD 3.7.4, Phase 7-1).

업로드/버전/삭제 훅이 큐잉한 인덱싱 잡을 처리하는 별도 프로세스다. 기존 Redis 를 큐로 재사용하며
(compose 의 worker 서비스), 재시도는 arq 기본(최대 3회)을 활용한다.

기동:  arq app.worker.WorkerSettings

잡 함수:
  - index_file(file_id)       : 파일이면 인덱싱, 폴더면 하위 파일로 팬아웃.
  - drop_file_index(file_id)  : 파일/폴더 하위의 청크 삭제(휴지통 이동·인덱싱 제외).

임베딩 프로바이더는 기동 시 1회 생성해 컨텍스트에 둔다. upstage 키가 없으면 None 이 되어
잡이 fail-open 으로 스킵된다(PRD 3.7.4 — 파일 서비스 본연 기능 불영향).
"""

from __future__ import annotations

from typing import Any

from arq.connections import RedisSettings

from app.core.config import settings
from app.core.database import SessionFactory
from app.core.logging import configure_logging, get_logger
from app.services import indexing as indexing_service
from app.services.embeddings import get_embedding_provider
from app.services.storage import get_storage

_log = get_logger("app.worker")


async def index_file(ctx: dict[str, Any], file_id: int) -> str:
    """인덱싱 잡 — 파일이면 인덱싱, 폴더면 하위 파일로 팬아웃한다."""
    async with SessionFactory() as session:
        count = await indexing_service.index_target(
            session, get_storage(), ctx.get("provider"), file_id, settings
        )
    _log.info("worker_index_file", file_id=file_id, targets=count)
    return f"indexed:{count}"


async def drop_file_index(ctx: dict[str, Any], file_id: int) -> str:
    """청크 삭제 잡 — 파일/폴더 하위의 file_chunks 를 제거한다."""
    async with SessionFactory() as session:
        await indexing_service.drop_index(session, file_id)
    _log.info("worker_drop_file_index", file_id=file_id)
    return "dropped"


async def startup(ctx: dict[str, Any]) -> None:
    """워커 기동 — 로깅 설정 + 임베딩 프로바이더 1회 생성."""
    configure_logging()
    provider = get_embedding_provider(settings)
    ctx["provider"] = provider
    if provider is None:
        _log.warning(
            "worker_no_embedding_provider",
            provider=settings.embedding_provider,
            hint="upstage 키가 없으면 인덱싱 잡은 스킵됩니다(fail-open).",
        )
    else:
        _log.info(
            "worker_started",
            provider=settings.embedding_provider,
            dim=provider.dim,
        )


async def shutdown(ctx: dict[str, Any]) -> None:
    """워커 종료 훅(현재 정리할 리소스 없음)."""


class WorkerSettings:
    """arq 워커 설정. `arq app.worker.WorkerSettings` 로 기동한다."""

    functions = [index_file, drop_file_index]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown
    max_tries = 3  # 재시도 최대 3회(PRD Phase 7-1).
