"""Alembic 마이그레이션 환경 (async).

DB URL 은 app.core.config.settings.database_url(asyncpg) 을 사용하고,
autogenerate 대상 메타데이터는 app.models 를 전부 import 한 뒤의 Base.metadata 이다.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.core.config import settings

# app.models 를 import 해야 모든 테이블이 Base.metadata 에 등록된다.
from app.models import Base  # noqa: F401  (Base 는 전체 모델 import 의 부수효과로 채워짐)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# alembic.ini 대신 설정에서 URL 주입 (asyncpg 드라이버 유지).
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """오프라인(--sql) 모드: DB 연결 없이 SQL 스크립트를 생성한다."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """온라인 모드: async 엔진으로 실제 DB 에 마이그레이션을 적용한다."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
