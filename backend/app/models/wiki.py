"""wiki_documents 테이블 — 문서별 PageIndex 트리 (spec/wiki-index.md).

트리는 **문서당 하나**다. 한 문서가 개인·그룹·전사 어느 스코프에 걸려 있든 트리를 공유하고,
누가 볼 수 있는지는 질의 시점에 기존 파일 권한으로 거른다. 비싼 쪽이 트리 생성(LLM 호출)이라
스코프별로 복제하지 않는다.

파생물이 트리 하나뿐이므로 회수도 `file_id` 행 삭제로 끝난다 — 여러 문서를 가로지르는 종합
위키(L2)를 v1 에서 뺀 이유이기도 하다(태깅 없이는 회수가 불가능해진다).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class WikiDocument(Base):
    __tablename__ = "wiki_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("files.id", ondelete="CASCADE"), nullable=False
    )
    # 트리를 만든 시점의 files.current_version 스냅숏. 이 값이 현재 버전보다 낮으면 stale 이다.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # pending | indexing | ready | failed | stale
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # PageIndex md_to_tree 결과. 인덱싱 중에는 직전 트리를 그대로 두고, 완성 후 원자적으로 교체한다.
    tree: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    __table_args__ = (
        UniqueConstraint("file_id", name="uq_wiki_documents_file"),
        Index("idx_wiki_documents_status", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 디버깅 편의
        return (
            f"<WikiDocument file_id={self.file_id} v{self.version} status={self.status}>"
        )
