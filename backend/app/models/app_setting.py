"""app_settings 테이블 (PRD 5.12 — 셋업 위저드, 2026-07-19).

셋업 위저드가 기록하는 애플리케이션 설정 저장소(key/value). 인프라 시크릿은 저장하지 않는다.
`setup_completed` 는 셋업 완료 잠금이자 동시 셋업 레이스 방어의 원자적 뮤텍스로도 쓰인다
(PK(key) 에 ON CONFLICT DO NOTHING — setup 서비스 참조). `default_max_storage` 는 신규
가입자 users.max_storage 의 기본값이다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<AppSetting key={self.key!r} value={self.value!r}>"
