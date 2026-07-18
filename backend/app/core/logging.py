"""구조화 로깅 설정 (structlog) — PRD 11장.

운영에서는 JSON 한 줄 로그(`LOG_FORMAT=json`), 개발에서는 컬러 콘솔(`LOG_FORMAT=console`)로
출력한다. stdlib `logging` 도 structlog 파이프라인으로 흐르게 하여 앱/서드파티 로그의 포맷을
일원화한다.

요청 로깅은 미들웨어(app.core.middleware)가 담당하고, uvicorn access 로그는 중복이므로
끈다(app.main 에서 `--no-access-log` + 여기서 로거 레벨 억제).
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import structlog

from app.core.config import settings

_configured = False


def _shared_processors() -> list[structlog.types.Processor]:
    """structlog 와 stdlib 로그가 공유하는 프로세서 체인."""
    return [
        structlog.contextvars.merge_contextvars,  # 미들웨어가 바인딩한 request_id 등 병합
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]


def configure_logging() -> None:
    """앱/테스트 기동 시 1회 호출. 재호출은 무해(idempotent)."""
    global _configured
    if _configured:
        return

    use_json = settings.log_format.lower() == "json"
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if use_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    # structlog 로거: 앱 코드가 structlog.get_logger() 로 쓰는 경로.
    structlog.configure(
        processors=[
            *_shared_processors(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # stdlib 로그(uvicorn, sqlalchemy 등)를 동일 렌더러로 통과시키는 핸들러.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_shared_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn access 로그는 미들웨어 로그와 중복 — 억제한다(에러만 유지).
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True

    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """structlog 바운드 로거를 반환한다."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(name))
