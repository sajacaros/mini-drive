"""files / file_versions 테이블 (PRD 5.2, 5.3)."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.user import User


class File(Base):
    """파일 및 폴더. `is_folder` 로 구분하며 자기참조로 트리를 구성한다.

    - `user_id`: 생성자(업로더). `group_id`: 그룹 소유 여부(NULL = 개인 소유) (PRD 5.8).
    - 루트 폴더 행만 `parent_folder_id IS NULL`, 일반 파일/폴더는 항상 부모를 가진다 (PRD 5.2 주석).
    """

    __tablename__ = "files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    group_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("groups.id"), nullable=True
    )
    parent_folder_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("files.id"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    thumbnail_key: Mapped[str | None] = mapped_column(String(500), nullable=True)

    is_folder: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # 인덱싱 제외 플래그 (PRD 3.7.2/5.2, Phase 7-1). 자신 또는 조상 폴더 어디에도 이 플래그가
    # 있으면 RAG 인덱싱·외부 API 전송 대상에서 제외한다(권한 상속과 동일하게 조회 시 판정).
    indexing_excluded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    base_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    current_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
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
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    owner: Mapped[User] = relationship(
        "User", back_populates="files", foreign_keys=[user_id]
    )
    parent: Mapped[File | None] = relationship(
        "File", remote_side=[id], back_populates="children"
    )
    children: Mapped[list[File]] = relationship(
        "File", back_populates="parent", cascade="all, delete-orphan"
    )
    versions: Mapped[list[FileVersion]] = relationship(
        "FileVersion",
        back_populates="file",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # 같은 폴더 내 이름 중복 방지 — 휴지통(soft-deleted) 파일은 제외 (PRD 5.2).
        Index(
            "uq_files_sibling_name",
            "parent_folder_id",
            "name",
            unique=True,
            postgresql_where=text("is_deleted = FALSE"),
        ),
        Index("idx_files_parent", "parent_folder_id"),
        Index("idx_files_user", "user_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        kind = "folder" if self.is_folder else "file"
        return f"<File id={self.id} {kind} name={self.name!r} parent={self.parent_folder_id}>"


class FileVersion(Base):
    """앱 레벨 버전 스냅샷 — PostgreSQL 이 버전 이력의 진실 소스 (PRD 2.2, 5.3)."""

    __tablename__ = "file_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uploaded_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    file: Mapped[File] = relationship("File", back_populates="versions")

    __table_args__ = (UniqueConstraint("file_id", "version", name="uq_file_versions_file_version"),)

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return f"<FileVersion file_id={self.file_id} v={self.version}>"
