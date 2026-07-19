"""arq 워커 — RAG 인덱싱(7-1) + 위키 Ingest/Lint 컴파일(7-3) (PRD 3.7.4).

업로드/버전/삭제 훅이 큐잉한 인덱싱 잡과, 위키 소스 등록/버전 갱신이 큐잉한 컴파일 잡을 처리하는
별도 프로세스다. 기존 Redis 를 큐로 재사용하며(compose 의 worker 서비스), 재시도는 arq 기본
(최대 3회)을 활용한다.

기동:  arq app.worker.WorkerSettings

잡 함수:
  - index_file(file_id)      : 파일이면 인덱싱, 폴더면 하위 파일로 팬아웃.
  - drop_file_index(file_id) : 파일/폴더 하위의 청크 삭제(휴지통 이동·인덱싱 제외).
  - wiki_ingest(file_id)     : 위키 소스를 컴파일(2단계 LLM → 페이지 드라이브 파일).
  - wiki_lint()              : 전사 위키 Lint(index 정합성 자동 수정 + 휴리스틱 리포트).
크론:
  - wiki_lint_cron           : 1일 1회 전사 위키 Lint(wiki-v2 D6/D9).

임베딩/챗 프로바이더는 기동 시 1회 생성해 컨텍스트에 둔다. 키가 없으면 None 이 되어 해당 잡이
스킵/실패로 처리된다(파일 서비스 본연 기능 불영향, PRD 3.7.4).
"""

from __future__ import annotations

from typing import Any

from arq import cron
from arq.connections import RedisSettings

from app.core.config import settings
from app.core.database import SessionFactory
from app.core.logging import configure_logging, get_logger
from app.models.wiki import JOB_FAILED, JOB_INGEST
from app.services import indexing as indexing_service
from app.services import wiki as wiki_service
from app.services.chat_llm import get_chat_provider
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


async def wiki_ingest(ctx: dict[str, Any], file_id: int) -> str:
    """위키 Ingest 잡 — 소스를 컴파일해 위키 페이지(드라이브 파일)를 생성/갱신한다(wiki-v2 4.4)."""
    chat_provider = ctx.get("chat_provider")
    if chat_provider is None:
        # 챗 프로바이더 미구성이면 컴파일 불가 — 잡을 failed 로 기록하고 재시도하지 않는다.
        async with SessionFactory() as session:
            await wiki_service.record_job(
                session,
                JOB_INGEST,
                file_id=file_id,
                status=JOB_FAILED,
                error="chat provider 미구성(LLM 컴파일 불가)",
            )
            await session.commit()
        _log.warning("wiki_ingest_no_provider", file_id=file_id)
        return "skipped:no_chat_provider"
    async with SessionFactory() as session:
        summary = await wiki_service.ingest_source(
            session, get_storage(), chat_provider, settings, file_id
        )
    _log.info(
        "worker_wiki_ingest",
        file_id=file_id,
        compiled=summary.compiled,
        targets=summary.targets,
    )
    return f"ingest:{summary.compiled}/{summary.targets}"


async def wiki_lint(ctx: dict[str, Any]) -> str:
    """위키 Lint 잡 — 전사 위키 정합성 자동 수정 + 휴리스틱 리포트(wiki-v2 D6/D9)."""
    async with SessionFactory() as session:
        root_id, owner = await wiki_service._ensure_wiki(session, get_storage())
        report = await wiki_service.run_lint(session, get_storage(), root_id, owner)
    _log.info(
        "worker_wiki_lint",
        auto_fixed=len(report.auto_fixed),
        reports=len(report.reports),
    )
    return f"lint:{len(report.auto_fixed)}/{len(report.reports)}"


async def wiki_lint_cron(ctx: dict[str, Any]) -> str:
    """1일 1회 전사 위키 Lint(wiki-v2 D6/D9). 부트스트랩 전이면 조용히 건너뛴다."""
    async with SessionFactory() as session:
        already = await wiki_service._get_setting_int(
            session, wiki_service.WIKI_ROOT_FOLDER_KEY
        )
        if already is None:
            return "skipped:no_wiki"
    async with SessionFactory() as session:
        root_id, owner = await wiki_service._ensure_wiki(session, get_storage())
        report = await wiki_service.run_lint(session, get_storage(), root_id, owner)
    _log.info(
        "worker_wiki_lint_cron",
        auto_fixed=len(report.auto_fixed),
        reports=len(report.reports),
    )
    return f"lint_cron:{len(report.auto_fixed)}/{len(report.reports)}"


async def startup(ctx: dict[str, Any]) -> None:
    """워커 기동 — 로깅 설정 + 임베딩/챗 프로바이더 1회 생성."""
    configure_logging()
    provider = get_embedding_provider(settings)
    ctx["provider"] = provider
    ctx["chat_provider"] = get_chat_provider(settings)
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
    if ctx["chat_provider"] is None:
        _log.warning(
            "worker_no_chat_provider",
            provider=settings.chat_provider,
            hint="챗 프로바이더 미구성 시 위키 Ingest 컴파일이 실패로 기록됩니다.",
        )


async def shutdown(ctx: dict[str, Any]) -> None:
    """워커 종료 훅(현재 정리할 리소스 없음)."""


class WorkerSettings:
    """arq 워커 설정. `arq app.worker.WorkerSettings` 로 기동한다."""

    functions = [index_file, drop_file_index, wiki_ingest, wiki_lint]
    # 매일 03:00 전체 스페이스 Lint(PRD 3.7.1 정기 스케줄).
    cron_jobs = [cron(wiki_lint_cron, hour=3, minute=0)]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    on_startup = startup
    on_shutdown = shutdown
    max_tries = 3  # 재시도 최대 3회(PRD Phase 7).
