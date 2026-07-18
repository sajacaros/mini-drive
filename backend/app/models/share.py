"""shares 테이블 (PRD 5.4)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import SharePermission


class Share(Base):
    """공유 링크. 게이트웨이 모델이라 비활성화 시 즉시 차단된다 (PRD 3.4, 5.4)."""

    __tablename__ = "shares"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id"), nullable=False
    )
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    share_url: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False
    )
    permission: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text(f"'{SharePermission.READ}'")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    max_downloads: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
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

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<Share id={self.id} url={self.share_url!r} active={self.is_active}>"
