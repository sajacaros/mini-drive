import asyncio
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from minio import Minio
from pydantic import BaseModel
from sqlalchemy import text

from app.api.router import api_router
from app.core.config import settings
from app.core.database import engine
from app.core.redis import redis_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 이후 스테이지: admin 부트스트랩 시드, 스토리지 버킷 확인 등을 여기서 수행한다.
    yield
    await engine.dispose()
    await redis_client.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="사내 파일 공유/관리 서비스 (Mini Drive)",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)
    app.add_api_route("/health", health, methods=["GET"], tags=["health"])
    return app


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
