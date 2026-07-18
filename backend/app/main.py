import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from minio import Minio
from pydantic import BaseModel
from sqlalchemy import text

from app.api.router import api_router
from app.core.config import settings
from app.core.database import SessionFactory, engine
from app.core.logging import configure_logging, get_logger
from app.core.metrics import render_metrics
from app.core.middleware import RequestContextMiddleware
from app.core.redis import redis_client
from app.services.users import ensure_admin_bootstrap

# import 시점에 로깅을 구성해 uvicorn/테스트 어느 진입점에서도 일관 포맷을 보장한다.
configure_logging()
_log = get_logger("app.lifecycle")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # admin 부트스트랩 — ADMIN_EMAIL 계정이 없으면 active/admin + 루트 폴더 생성 (PRD 3.6.2).
    async with SessionFactory() as session:
        await ensure_admin_bootstrap(
            session, settings.admin_email, settings.admin_initial_password
        )
    _log.info("startup_complete", app=settings.app_name, environment=settings.environment)
    yield
    await engine.dispose()
    await redis_client.aclose()
    _log.info("shutdown_complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="사내 파일 공유/관리 서비스 (Mini Drive)",
        lifespan=lifespan,
    )

    # 요청 로깅/메트릭 미들웨어 — CORS 보다 먼저 add 하여 실행 순서상 바깥(가장 먼저)에 둔다.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    app.add_api_route("/health", health, methods=["GET"], tags=["health"])
    if settings.metrics_enabled:
        app.add_api_route("/metrics", metrics, methods=["GET"], include_in_schema=False)
    return app


async def metrics() -> Response:
    """Prometheus 스크레이프 엔드포인트 (PRD 11장).

    게이트웨이(nginx)에서 외부 접근을 차단하고 내부 네트워크 스크레이프만 허용한다.
    """
    payload, content_type = render_metrics()
    return Response(content=payload, media_type=content_type)


ComponentStatus = Literal["ok", "error"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: ComponentStatus
    minio: ComponentStatus
    redis: ComponentStatus


async def _check_database() -> ComponentStatus:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        return "error"


async def _check_redis() -> ComponentStatus:
    try:
        await redis_client.ping()
        return "ok"
    except Exception:
        return "error"


async def _check_minio() -> ComponentStatus:
    def _probe() -> bool:
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        # 버킷 존재 여부 확인만으로 연결/자격증명을 검증한다.
        client.bucket_exists(settings.minio_bucket)
        return True

    try:
        await asyncio.to_thread(_probe)
        return "ok"
    except Exception:
        return "error"


async def health() -> HealthResponse:
    """DB / MinIO / Redis 연결 상태를 확인한다 (PRD 11절)."""
    database, redis_state, minio_state = await asyncio.gather(
        _check_database(),
        _check_redis(),
        _check_minio(),
    )
    overall = "ok" if all(s == "ok" for s in (database, redis_state, minio_state)) else "degraded"
    return HealthResponse(
        status=overall,
        database=database,
        minio=minio_state,
        redis=redis_state,
    )


app = create_app()
