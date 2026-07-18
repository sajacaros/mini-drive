"""audit_logs 테이블 (PRD 5.9).

관리자 행위와 권한 변경을 기록한다 — "누가 이 링크를 껐나"에 답하기 위한 테이블.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    # 예: user.update, share.force_disable, permission.grant,
    #     signup_code.create, signup_code.update (PRD 5.9).
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str] = mapped_column(String(30), nullable=False)
    target_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 변경 전/후 값 등 (PRD 5.9).
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_audit_logs_target", "target_type", "target_id"),
        Index("idx_audit_logs_actor", "actor_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return (
            f"<AuditLog id={self.id} actor={self.actor_id} action={self.action!r} "
            f"target={self.target_type}:{self.target_id}>"
        )
