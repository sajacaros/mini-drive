"""groups / group_members 테이블 (PRD 5.5, 5.6)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import GroupRole


class Group(Base):
    """부서·프로젝트·팀 단위 그룹 (PRD 1.3, 5.5)."""

    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
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

    __table_args__ = (UniqueConstraint("name", name="uq_groups_name"),)

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<Group id={self.id} name={self.name!r}>"


class GroupMember(Base):
    """그룹원. `removed_at` soft delete + UNIQUE(group_id, user_id) 로 재초대 upsert 지원 (PRD 5.6)."""

    __tablename__ = "group_members"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    group_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text(f"'{GroupRole.MEMBER}'")
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # NULL = 활성 멤버, 값 있으면 제거됨 (PRD 5.6).
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="uq_group_members_group_user"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<GroupMember group_id={self.group_id} user_id={self.user_id} role={self.role}>"
