"""signup_codes 테이블 (PRD 5.11 — 가입 코드제, 2026-07-19).

가입 시 필수 입력하는 관리자 발급 코드. 만료일·최대 사용 횟수 제한 가능하며,
검증(활성→만료→사용 횟수)과 use_count 증가는 조건부 UPDATE ... RETURNING 으로
원자적으로 처리한다 (PRD 5.10 과 동일 패턴, 동시 가입 레이스 방어).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SignupCode(Base):
    __tablename__ = "signup_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 추측 불가 랜덤 토큰 (secrets 기반, PRD 10장).
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    memo: Mapped[str] = mapped_column(
        String(200), nullable=False, server_default=text("''")
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return (
            f"<SignupCode id={self.id} memo={self.memo!r} "
            f"use_count={self.use_count} active={self.is_active}>"
        )
