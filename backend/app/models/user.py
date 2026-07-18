"""users 테이블 (PRD 5.1)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import UserRole, UserStatus

if TYPE_CHECKING:
    from app.models.file import File


# 기본 할당량 10GB (PRD 5.1: 10737418240 = 10 * 1024**3)
DEFAULT_MAX_STORAGE = 10_737_418_240


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("''")
    )
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # DB 는 PRD DDL 대로 VARCHAR. 값 집합만 StrEnum 으로 강제 (native enum 미사용).
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text(f"'{UserRole.USER}'")
    )
    # 가입 코드제 — 코드 검증 통과 시 즉시 active (PRD 3.1, 5.1).
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text(f"'{UserStatus.ACTIVE}'")
    )

    storage_used: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    max_storage: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text(str(DEFAULT_MAX_STORAGE))
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # 생성자(업로더)로서 소유한 파일. files.user_id → users.id (PRD 5.2/5.8).
    files: Mapped[list[File]] = relationship(
        "File",
        back_populates="owner",
        foreign_keys="File.user_id",
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<User id={self.id} email={self.email!r} role={self.role} status={self.status}>"
