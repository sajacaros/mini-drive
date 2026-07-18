"""file_group_permissions 테이블 (PRD 5.7).

명시적으로 부여한 권한만 저장한다. 상속은 물질화하지 않고 조회 시 recursive CTE 로
가장 가까운 `inherit_to_children = TRUE` 권한을 판정한다 (PRD 2.2, 5.7).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import GroupPermission


class FileGroupPermission(Base):
    __tablename__ = "file_group_permissions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    permission: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text(f"'{GroupPermission.READ}'")
    )
    # 하위 폴더/파일로 상속 여부 (PRD 5.7).
    inherit_to_children: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # NULL = 영구.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    granted_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "file_id", "group_id", name="uq_file_group_permissions_file_group"
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return (
            f"<FileGroupPermission file_id={self.file_id} group_id={self.group_id} "
            f"permission={self.permission} inherit={self.inherit_to_children}>"
        )
