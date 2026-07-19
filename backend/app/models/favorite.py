"""file_favorites / file_recents 테이블 (Phase 8-2/8-3, spec/drive-ux-phase8.md).

즐겨찾기와 최근 항목 모두 (user_id, file_id) 복합 PK 로 사용자별 1행을 유지한다. 파일/사용자
삭제 시 FK CASCADE 로 함께 제거된다(행 자체는 파생 데이터라 이력 보존 대상이 아니다).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FileFavorite(Base):
    """사용자가 별표한 파일/폴더 (PRD Phase 8-2). 접근권 상실/삭제 시에도 행은 유지하고
    목록 조회 시점에 숨긴다 — 권한 복구 시 다시 보이게 한다."""

    __tablename__ = "file_favorites"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FileRecent(Base):
    """사용자별 최근 이용 파일 (PRD Phase 8-3). 미리보기/다운로드 티켓 발급 성공 시 upsert.

    사용자당 최신 100개만 유지한다(초과분은 upsert 시 삭제). last_accessed_at 인덱스로
    최신순 조회/상한 삭제를 지원한다."""

    __tablename__ = "file_recents"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="CASCADE"), primary_key=True
    )
    last_accessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        Index("idx_file_recents_last_accessed", "last_accessed_at"),
    )
